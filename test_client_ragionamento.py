"""Il client di DEVIN davanti a un modello che ragiona.

Nessuna rete, nessuna GPU: un finto llama-server che consegna pezzi di
stream come li consegna quello vero, cioe' spezzati dove capita.

Il caso che questi test esistono per sorvegliare e' il marcatore <think>
spezzato fra due pezzi ("<th" + "ink>"). Un banco ingenuo non lo produce
mai e in esercizio capita sempre.
"""
import json

import pytest


class _FintaRisposta:
    def __init__(self, righe, status=200, testo=""):
        self._righe = righe
        self.status_code = status
        self.text = testo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        # requests alza per TUTTI i 4xx e 5xx. Con la soglia a 500 il banco
        # misurava un server che non esiste, e una mutazione che toglieva il
        # `return` dopo un 4xx restava invisibile.
        if self.status_code >= 400:
            import requests as _r
            raise _r.exceptions.HTTPError("HTTP %d" % self.status_code)

    def iter_lines(self):
        for r in self._righe:
            yield r.encode("utf-8")

    def json(self):
        return json.loads(self._righe[0])


@pytest.fixture
def client(monkeypatch):
    """Un AIClient con configurazione dichiarata e la rete sostituita.

    Costruito con __new__ per non far partire refresh(), che andrebbe a
    cercare il rig: questi test riguardano il corpo delle richieste e la
    lettura delle risposte, non la scoperta dell'endpoint.
    """
    from devin.ai import client as modulo

    stato = {"risposta": None, "corpi": [], "timeout": None}

    def finta_post(url, json=None, timeout=None, stream=None, headers=None, **kw):
        stato["corpi"].append(json)
        stato["timeout"] = timeout
        return stato["risposta"]

    monkeypatch.setattr(modulo.requests, "post", finta_post)

    # /props: di default il server non risponde (si tiene il catalogo testuale);
    # i test che vogliono un server col template mettono stato["props"].
    stato["props"] = None
    stato["props_chieste"] = 0

    class _FintaProps:
        def __init__(self, dati):
            self._dati = dati

        def raise_for_status(self):
            return None

        def json(self):
            return self._dati

    def finta_get(url, timeout=None, headers=None, **kw):
        stato["props_chieste"] += 1
        if stato["props"] is None:
            raise modulo.requests.ConnectionError("niente rete nei test")
        return _FintaProps(stato["props"])

    monkeypatch.setattr(modulo.requests, "get", finta_get)

    # refresh() e sleep() vanno CONTATI, non solo neutralizzati: "un 4xx non
    # brucia i retry" e' una promessa su quante volte si riprova, e senza
    # contarle un retry si nasconde dietro un errore diverso e il banco non
    # vede niente. Neutralizzarli evita anche chiamate di rete vere.
    stato["refresh"] = 0
    stato["dormite"] = []
    monkeypatch.setattr(modulo.time, "sleep", lambda s: stato["dormite"].append(s))

    c = modulo.AIClient.__new__(modulo.AIClient)
    c.config = {"models": {
        "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20,
                     "min_p": 0.0, "presence_penalty": 0.0},
        "reasoning": {"default_effort": "medium",
                      "effort_for_completions": "low",
                      "timeouts": {"low": 180, "medium": 600, "xhigh": 2400}},
    }}
    c.remote_base_url = "http://127.0.0.1:18081"
    c.remote_host = "127.0.0.1"
    c.remote_coder_url = "http://127.0.0.1:18081/v1/chat/completions"
    c.remote_reasoning_url = c.remote_coder_url
    c.remote_coder_ok = True
    c.remote_reasoning_ok = True
    c.remote_model_actual = "modello-servito"
    c.allow_local_fallback = False
    c.rig_required = True
    c.rig_api_key = ""
    c.circuit_breaker_enabled = True
    c.use_openai = False
    c.openai = None
    c._rig_health = {"failures": 0, "last_fail": 0, "state": "closed"}
    c.refresh = lambda **kw: stato.__setitem__("refresh", stato["refresh"] + 1)
    c.ultimo_ragionamento = ""
    c._stato = stato
    return c


def _stream(c, pezzi, chiudi=True):
    righe = ["data: " + json.dumps({"choices": [{"delta": p}]}) for p in pezzi]
    if chiudi:
        righe.append("data: [DONE]")
    c._stato["risposta"] = _FintaRisposta(righe)
    return list(c.stream_eventi([{"role": "user", "content": "x"}]))


def _unisci(eventi, tipo):
    return "".join(e["testo"] for e in eventi if e["tipo"] == tipo)


# --- campionamento ------------------------------------------------------

def test_il_campionamento_viene_dalla_configurazione(client):
    """Prima era inchiodato a 0.2 in local(), cloud() e stream(), e a 0.1/0.0
    nei chiamanti di complete(). A temperatura bassa un modello che ragiona
    entra in cerchio: misurato, 11.000 caratteri e zero codice."""
    assert client._campionamento()["temperature"] == 0.6
    assert client._campionamento()["top_k"] == 20


def test_il_corpo_porta_tutti_i_parametri_dichiarati(client):
    corpo = client._corpo("m", [], sforzo="")
    assert corpo["temperature"] == 0.6
    assert corpo["top_p"] == 0.95
    assert corpo["min_p"] == 0.0
    assert "chat_template_kwargs" not in corpo


# --- sforzo di ragionamento --------------------------------------------

def test_lo_sforzo_arriva_al_chat_template(client):
    corpo = client._corpo("m", [], sforzo="xhigh")
    assert corpo["chat_template_kwargs"] == {"reasoning_effort": "xhigh"}


def test_uno_sforzo_non_previsto_alza_invece_di_essere_ignorato(client):
    """Guardrail: nessun fallback silenzioso. Un valore scritto male, passato
    al server, verrebbe ignorato e la richiesta girerebbe in un regime
    diverso da quello creduto senza che niente lo dica."""
    with pytest.raises(ValueError):
        client._sforzo("altissimo")


def test_lo_sforzo_vuoto_prende_quello_della_configurazione(client):
    assert client._sforzo("") == "medium"


# --- timeout ------------------------------------------------------------

@pytest.mark.parametrize("sforzo,atteso", [("low", 180), ("medium", 600), ("xhigh", 2400)])
def test_il_timeout_segue_lo_sforzo(client, sforzo, atteso):
    assert client._timeout_ragionato(sforzo) == atteso


def test_senza_sforzo_il_timeout_non_torna_a_sessanta_secondi(client):
    """60 s era il valore storico, scritto per un modello che non pensava.
    A 8,8 token/s garantisce il taglio a meta' frase."""
    assert client._timeout_ragionato("") >= 180


def test_il_timeout_paga_il_prefill_del_prompt(client):
    """Durante il prefill il server tace: 120k caratteri ~ 40k token ~ 1000 s a 40 tok/s."""
    lungo = [{"role": "system", "content": "x" * 60_000},
             {"role": "user", "content": [{"type": "text", "text": "y" * 60_000}]}]
    assert client._timeout_ragionato("low", messages=lungo) == 180 + 1000
    assert client._timeout_ragionato("low", messages=[{"role": "user", "content": "ciao"}]) == 180
    # Il timeout del chiamante e' un minimo: non puo' scendere sotto il calcolato.
    assert client._timeout_ragionato("low", timeout=30, messages=lungo) == 180 + 1000
    assert client._timeout_ragionato("low", timeout=5000, messages=lungo) == 5000


def test_i_timeout_fissi_degli_agenti_non_tagliano_piu_la_generazione(client):
    """Scaffold del 12/10: coder con timeout=60 -> storage.py "vuoto" dopo 3 tentativi."""
    assert client._timeout_ragionato("", timeout=60) >= 180
    assert client._timeout_ragionato("", timeout=150) >= 180


# --- pensiero e risposta ------------------------------------------------

def test_reasoning_content_finisce_nel_canale_del_pensiero(client):
    eventi = _stream(client, [{"reasoning_content": "penso "},
                              {"reasoning_content": "ancora"},
                              {"content": "def f():"}, {"content": " pass"}])
    assert _unisci(eventi, "ragionamento") == "penso ancora"
    assert _unisci(eventi, "risposta") == "def f(): pass"


def test_il_pensiero_inline_non_finisce_nella_risposta(client):
    eventi = _stream(client, [{"content": "<think>"}, {"content": "rifletto"},
                              {"content": "</think>"}, {"content": "vera"}])
    assert _unisci(eventi, "ragionamento") == "rifletto"
    assert _unisci(eventi, "risposta") == "vera"


def test_il_marcatore_spezzato_fra_due_pezzi_viene_riconosciuto(client):
    """Il caso vero: lo stream taglia dove gli pare."""
    eventi = _stream(client, [{"content": "<th"}, {"content": "ink>seg"},
                              {"content": "reto</thi"}, {"content": "nk>pubblico"}])
    assert _unisci(eventi, "ragionamento") == "segreto"
    assert _unisci(eventi, "risposta") == "pubblico"


def test_il_minore_di_non_viene_mangiato(client):
    """"if a < b" finisce con un possibile inizio di marcatore. Trattenerlo
    e non consegnarlo mai era il difetto trovato dal banco di mutazione."""
    eventi = _stream(client, [{"content": "if a <"}, {"content": " b: pass"}])
    assert _unisci(eventi, "risposta") == "if a < b: pass"


def test_un_marcatore_incompleto_a_fine_stream_resta_testo(client):
    eventi = _stream(client, [{"content": "ciao<"}])
    assert _unisci(eventi, "risposta") == "ciao<"


def test_niente_si_perde_quando_non_ci_sono_marcatori(client):
    eventi = _stream(client, [{"content": "abc"}, {"content": "def"}, {"content": "g"}])
    assert _unisci(eventi, "risposta") == "abcdefg"


def test_un_pensiero_mai_chiuso_non_diventa_risposta(client):
    """Se la generazione si tronca mentre il modello ancora pensa, la
    risposta e' vuota. Dichiararlo vuoto e' piu' onesto che consegnare il
    ragionamento spacciandolo per risposta."""
    eventi = _stream(client, [{"content": "<think>ragiono e mi tronco"}])
    assert _unisci(eventi, "risposta") == ""
    assert "ragiono" in _unisci(eventi, "ragionamento")


def test_stream_vecchio_stile_non_consegna_piu_think_grezzo(client):
    righe = ["data: " + json.dumps({"choices": [{"delta": d}]}) for d in
             [{"reasoning_content": "PENSIERO"},
              {"content": "<think>ALTRO</think>VERA"}]] + ["data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    assert "".join(client.stream([{"role": "user", "content": "x"}])) == "VERA"


def test_lo_stream_manda_lo_sforzo_e_il_timeout_giusti(client):
    client._stato["risposta"] = _FintaRisposta(["data: [DONE]"])
    list(client.stream_eventi([{"role": "user", "content": "x"}], sforzo="xhigh"))
    assert client._stato["corpi"][-1]["chat_template_kwargs"] == {"reasoning_effort": "xhigh"}
    assert client._stato["corpi"][-1]["stream"] is True
    assert client._stato["timeout"] == 2400


def test_un_4xx_non_brucia_i_retry_e_spiega_perche(client):
    """Un 4xx viene da un server RAGGIUNGIBILE che ha rifiutato la richiesta:
    ritentarla identica non serve, e svegliare il rig meno che mai. La
    promessa e' "una richiesta, poi mi fermo e dico perche'", quindi si
    contano i tentativi, i risvegli e le attese — non solo il messaggio."""
    client._stato["risposta"] = _FintaRisposta([], status=400, testo="n_ctx exceeded")
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}]))
    assert len(client._stato["corpi"]) == 1, "ha ritentato una richiesta rifiutata"
    assert client._stato["refresh"] == 0, "ha provato a risvegliare il rig per un 4xx"
    assert client._stato["dormite"] == [], "ha aspettato invano dopo un rifiuto"
    assert [e["tipo"] for e in eventi] == ["avviso"]
    assert "Contesto troppo lungo" in eventi[0]["testo"]


def test_lo_slot_non_disponibile_non_diventa_una_risposta(client):
    """Guardrail fail-closed: se il ruolo DEVIN non e' attivo, l'utente deve
    leggerlo, non ricevere il silenzio."""
    from devin.ai.client import RigUnavailableError
    client.remote_reasoning_ok = False
    client.allow_local_fallback = False
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}]))
    assert len(eventi) == 1 and eventi[0]["tipo"] == "avviso"
    assert "non disponibile" in eventi[0]["testo"]
    with pytest.raises(RigUnavailableError):
        client._get_endpoints("reasoning")


# --- local() ------------------------------------------------------------

def test_local_non_restituisce_il_ragionamento(client):
    """planner, critic e coder cercano JSON o un diff dentro questa stringa:
    del ragionamento in testa glielo spacca."""
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": '<think>ragiono</think>{"a":1}'}}]})])
    assert client.local([{"role": "user", "content": "x"}]) == '{"a":1}'
    assert client.ultimo_ragionamento == "ragiono"


def test_local_preferisce_reasoning_content_quando_ce(client):
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "ok", "reasoning_content": "sep"}}]})])
    assert client.local([{"role": "user", "content": "x"}]) == "ok"
    assert client.ultimo_ragionamento == "sep"


def test_local_manda_lo_sforzo_e_il_timeout_giusti(client):
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "x"}}]})])
    client.local([{"role": "user", "content": "x"}], sforzo="xhigh")
    assert client._stato["corpi"][-1]["chat_template_kwargs"] == {"reasoning_effort": "xhigh"}
    assert client._stato["timeout"] == 2400


def test_un_timeout_esplicito_non_scende_sotto_lo_sforzo(client):
    """Il timeout del chiamante e' un minimo: con xhigh resta il tetto di xhigh."""
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "x"}}]})])
    client.local([{"role": "user", "content": "x"}], timeout=30, sforzo="xhigh")
    assert client._stato["timeout"] == 2400


# --- complete() ---------------------------------------------------------

def test_complete_chiede_uno_sforzo_basso(client):
    """Con max_tokens=80 e il ragionamento acceso, gli 80 token se li mangia
    il pensiero e la funzione torna vuota."""
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "suggerimento"}}]})])
    assert client.complete("prefisso", max_tokens=80) == "suggerimento"
    assert client._stato["corpi"][-1]["chat_template_kwargs"] == {"reasoning_effort": "low"}
    assert client._stato["corpi"][-1]["max_tokens"] == 80


def test_complete_toglie_il_pensiero_anche_dai_riassunti(client):
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "<think>mah</think>Riassunto."}}]})])
    assert client.complete("riassumi", max_tokens=1200) == "Riassunto."


# --- quanto si trattiene ------------------------------------------------
# Il contenuto finale e' giusto anche trattenendo sempre il massimo: la
# differenza e' il RITARDO con cui l'utente vede le lettere. Si misura qui,
# perche' nel testo finale non si vede.

@pytest.mark.parametrize("coda,atteso", [
    ("abc", 0),              # testo normale: non si trattiene niente
    ("if a < b", 0),         # il "<" non e' in coda: niente da trattenere
    ("ab<", 1),              # potrebbe diventare <think>
    ("x<thi", 4),
    ("<think>", 0),          # marcatore completo: lo gestisce find(), non qui
    ("", 0),
])
def test_quanto_si_trattiene(coda, atteso):
    from devin.ai.client import AIClient, _APRE_PENSIERO
    assert AIClient._quanto_trattenere(coda, _APRE_PENSIERO) == atteso


def test_senza_configurazione_lo_sforzo_dei_completamenti_resta_basso(client):
    """Se nessuno dichiara effort_for_completions, la rete deve essere
    "low": con max_tokens=80 e il ragionamento alto la funzione torna vuota."""
    client.config = {"models": {}}
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "sugg"}}]})])
    client.complete("prefisso", max_tokens=80)
    assert client._stato["corpi"][-1]["chat_template_kwargs"] == {"reasoning_effort": "low"}

# --- separazione statica -----------------------------------------------

@pytest.mark.parametrize("dentro,pensiero,risposta", [
    ("ciao", "", "ciao"),
    ("<think>rifletto</think>ecco", "rifletto", "ecco"),
    ("a<think>b</think>c", "b", "ac"),
    ("<think>mai chiuso", "mai chiuso", ""),
])
def test_spezza_pensiero(dentro, pensiero, risposta):
    from devin.ai.client import AIClient
    assert AIClient._spezza_pensiero(dentro) == (pensiero, risposta)


# --- lettura del contesto (prefill) --------------------------------------

def test_lo_stream_chiede_e_inoltra_l_avanzamento_del_prefill(client):
    """16/10: ~2 minuti di prefill su 7k token con la chat vuota. Il server
    dice quanto ha letto se glielo si chiede; quel dato diventa un evento suo,
    che non tocca ne' la risposta ne' il ragionamento."""
    righe = [
        "data: " + json.dumps({"choices": [{"delta": {}}],
                               "prompt_progress": {"total": 7100, "cache": 0, "processed": 3200, "time_ms": 40000}}),
        "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "penso"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "ecco"}}]}),
        "data: [DONE]",
    ]
    client._stato["risposta"] = _FintaRisposta(righe)
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}]))
    assert client._stato["corpi"][-1]["return_progress"] is True
    (lettura,) = [e for e in eventi if e["tipo"] == "lettura"]
    assert (lettura["total"], lettura["processed"], lettura["time_ms"]) == (7100, 3200, 40000)
    assert _unisci(eventi, "risposta") == "ecco" and _unisci(eventi, "ragionamento") == "penso"
    # chi vuole solo il testo (autocomplete, stream()) non vede la lettura
    client._stato["risposta"] = _FintaRisposta(righe)
    assert "".join(client.stream([{"role": "user", "content": "x"}])) == "ecco"


# --- local() in streaming ------------------------------------------------

def test_local_chiede_lo_stream_e_separa_il_pensiero(client):
    """16/10: lo scaffold di scadenze.py scadeva a ~180 s con il modello che
    stava ancora scrivendo, perche' una risposta non-stream non manda byte
    finche' non e' finita. Ora local() legge lo stream: il timeout vale solo
    per un server che tace."""
    righe = [
        "data: " + json.dumps({"choices": [{"delta": {}}], "prompt_progress": {"total": 9, "processed": 9}}),
        "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "penso"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "x = 1"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "\n"}}]}),
        "data: [DONE]",
    ]
    client._stato["risposta"] = _FintaRisposta(righe)
    assert client.local([{"role": "user", "content": "x"}], mode="coder") == "x = 1\n"
    corpo = client._stato["corpi"][-1]
    assert corpo["stream"] is True and corpo["return_progress"] is True
    assert client.ultimo_ragionamento == "penso"


def test_local_legge_ancora_un_json_intero_e_think_inline(client):
    risposta = {"choices": [{"message": {"content": "<think>prima</think>dopo"}}]}
    client._stato["risposta"] = _FintaRisposta([json.dumps(risposta)])
    assert client.local([{"role": "user", "content": "x"}], mode="coder") == "dopo"
    assert client.ultimo_ragionamento == "prima"


def test_local_passa_il_tetto_di_token(client):
    client._stato["risposta"] = _FintaRisposta(["data: [DONE]"])
    client.local([{"role": "user", "content": "x"}], mode="coder", max_tokens=123)
    assert client._stato["corpi"][-1]["max_tokens"] == 123


def test_lo_scaffold_ha_un_tetto_di_uscita(monkeypatch):
    from devin.agents import coder as modulo
    visti = {}

    class FintoAI:
        def local(self, messages, mode="reasoning", timeout=None, sforzo="", max_tokens=None):
            visti["max_tokens"] = max_tokens
            return "x = 1"

    c = modulo.Coder.__new__(modulo.Coder)
    c.ai = FintoAI()
    assert c.generate_file("a.py", "spec") == "x = 1"
    assert visti["max_tokens"] == modulo.SCAFFOLD_MAX_TOKENS


def test_local_registra_perche_si_e_fermata(client):
    client._stato["risposta"] = _FintaRisposta([
        'data: {"choices": [{"delta": {"reasoning_content": "penso"}}]}',
        'data: {"choices": [{"delta": {}, "finish_reason": "length"}]}',
        "data: [DONE]",
    ])
    assert client.local([{"role": "user", "content": "x"}], mode="coder", max_tokens=5) == ""
    assert client.ultima_fine == "length"
    assert client.ultimo_ragionamento == "penso"


class _AIConFine:
    def __init__(self, risposta, fine, pensiero=""):
        self.risposta, self.ultima_fine, self.ultimo_ragionamento = risposta, fine, pensiero
        self.visti = []

    def local(self, messages, mode="reasoning", timeout=None, sforzo="", max_tokens=None):
        self.visti.append(messages)
        return self.risposta


def _coder(ai):
    from devin.agents import coder as modulo
    c = modulo.Coder.__new__(modulo.Coder)
    c.ai = ai
    return c


def test_scaffold_tetto_esaurito_pensando_lo_dice():
    with pytest.raises(ValueError, match="esaurito mentre il modello ragionava"):
        _coder(_AIConFine("", "length", "x" * 900)).generate_file("t.py", "spec")


def test_scaffold_file_tagliato_al_tetto_non_si_scrive():
    with pytest.raises(ValueError, match="tagliato al tetto"):
        _coder(_AIConFine("def f(:\n", "length")).generate_file("t.py", "spec")


def test_scaffold_senza_risposta_lo_dice():
    with pytest.raises(ValueError, match="nessuna risposta"):
        _coder(_AIConFine(None, "")).generate_file("t.py", "spec")


def test_scaffold_vede_il_contesto_oltre_i_3000_caratteri():
    ai = _AIConFine("x = 1", "stop")
    contesto = "a" * 5000 + "FINE_CONTESTO"
    assert _coder(ai).generate_file("t.py", "spec", project_context=contesto) == "x = 1"
    assert "FINE_CONTESTO" in ai.visti[0][1]["content"]


def test_contesto_di_un_modulo_lungo_e_la_sua_interfaccia_intera():
    from devin.agents.coder import contesto_file
    sorgente = (
        "from datetime import date\n"
        "class Scadenza:\n    \"\"\"Una scadenza.\"\"\"\n    titolo: str\n"
        "    def giorni(self, oggi: date) -> int:\n        return 1\n"
        + "\n".join(f"def aiuto_{i}(x):\n    return x + {i}\n" for i in range(300))
        + "\ndef ultima_funzione(percorso: str) -> list:\n    \"\"\"Carica.\"\"\"\n    return []\n"
    )
    vista = contesto_file("scadenze.py", sorgente)
    assert "def ultima_funzione(percorso: str) -> list:" in vista
    assert "def giorni(self, oggi: date) -> int:" in vista
    assert "titolo: str" in vista
    assert "return x + 5" not in vista


def test_contesto_corto_intero_e_non_python_tagliato_dichiarato():
    from devin.agents.coder import contesto_file
    assert contesto_file("a.py", "x = 1\n") == "x = 1\n"
    lungo = contesto_file("README.md", "r" * 6000)
    assert lungo.endswith("(tagliato: 6000 caratteri in tutto)")
    rotto = contesto_file("b.py", "def (\n" * 2000)
    assert "tagliato" in rotto


def test_lo_stream_consegna_le_chiamate_di_strumenti_alla_fine(client):
    righe = ["data: " + json.dumps({"choices": [{"delta": p}]}) for p in [
        {"reasoning_content": "cerco"},
        {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "cerca_web", "arguments": "{\"domanda\""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": ": \"turni 1/21\"}"}}]},
    ]] + ["data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    strumento = {"type": "function", "function": {"name": "cerca_web", "parameters": {"type": "object"}}}
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}], tools=[strumento]))
    assert client._stato["corpi"][-1]["tools"] == [strumento]
    assert client._stato["corpi"][-1]["tool_choice"] == "auto"
    chiamate = [e for e in eventi if e["tipo"] == "chiamate"][0]["chiamate"]
    assert chiamate[0]["function"] == {"name": "cerca_web", "arguments": "{\"domanda\": \"turni 1/21\"}"}


def test_senza_strumenti_il_corpo_non_li_contiene(client):
    _stream(client, [{"content": "ciao"}])
    assert "tools" not in client._stato["corpi"][-1]


CERCA = {"type": "function", "function": {"name": "cerca_web", "parameters": {
    "type": "object", "properties": {"domanda": {"type": "string"}}, "required": ["domanda"]}}}
MARKUP_17_10 = ("Giusto, cerco adesso.\n\n<tool_call>\n<function=cerca_web>\n<parameter=query>\n"
                "schema turni 4 squadre ciclo continuo\n</parameter>\n</function>\n</tool_call>\n"
                "<tool_call>\n<function=cerca_web>\n<parameter=query>\nturnazione 1/21\n</parameter>\n"
                "</function>\n</tool_call>")


def test_le_chiamate_scritte_come_testo_diventano_vere_e_non_si_vedono(client):
    # il caso del 17/10, spezzato a pezzetti come arriva dallo stream
    pezzi = [{"content": MARKUP_17_10[i:i + 7]} for i in range(0, len(MARKUP_17_10), 7)]
    righe = ["data: " + json.dumps({"choices": [{"delta": p}]}) for p in pezzi] + ["data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}], tools=[CERCA]))
    testo = "".join(e["testo"] for e in eventi if e["tipo"] == "risposta")
    assert "<tool_call" not in testo and "<" not in testo
    assert testo.strip() == "Giusto, cerco adesso."
    chiamate = [e for e in eventi if e["tipo"] == "chiamate"][0]["chiamate"]
    assert [json.loads(c["function"]["arguments"]) for c in chiamate] == [
        {"domanda": "schema turni 4 squadre ciclo continuo"}, {"domanda": "turnazione 1/21"}]


def test_nome_inesistente_diventa_chiamata_da_rifiutare_non_markup(client):
    # 17/10: "<function=github__get_user>\n{}\n</parameter>..." restava in chat
    altro = "<tool_call>\n<function=github__get_user>\n{}\n</parameter>\n</function>\n</tool_call>"
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": altro}}]}), "data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}], tools=[CERCA]))
    assert "".join(e["testo"] for e in eventi if e["tipo"] == "risposta") == ""
    chiamata = [e for e in eventi if e["tipo"] == "chiamate"][0]["chiamate"][0]
    assert chiamata["function"] == {"name": "github__get_user", "arguments": "{}"}


def test_nome_senza_prefisso_si_risolve():
    from devin.ai.tool_text import estrai_chiamate
    get_me = {"type": "function", "function": {"name": "github__get_me", "parameters": {}}}
    _, chiamate = estrai_chiamate("<tool_call><function=get_me></function></tool_call>", [CERCA, get_me])
    assert chiamate[0]["function"]["name"] == "github__get_me"


def test_un_minore_nel_testo_normale_non_trattiene_nulla(client):
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) for t in ["3 <", " 5 e <t", "ag> ok"]]
    client._stato["risposta"] = _FintaRisposta(righe + ["data: [DONE]"])
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}], tools=[CERCA]))
    assert "".join(e["testo"] for e in eventi if e["tipo"] == "risposta") == "3 < 5 e <tag> ok"


def test_chiamata_nativa_con_parametro_sbagliato_viene_adattata(client):
    righe = ["data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "cerca_web", "arguments": "{\"query\": \"eclissi\"}"}}]}}]}),
        "data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}], tools=[CERCA]))
    chiamata = [e for e in eventi if e["tipo"] == "chiamate"][0]["chiamate"][0]
    assert json.loads(chiamata["function"]["arguments"]) == {"domanda": "eclissi"}


def test_formato_json_hermes_e_adatta_argomenti():
    from devin.ai.tool_text import adatta_argomenti, estrai_chiamate
    testo, chiamate = estrai_chiamate(
        'prima <tool_call>{"name": "cerca_web", "arguments": {"q": "x"}}</tool_call>', [CERCA])
    assert testo == "prima" and json.loads(chiamate[0]["function"]["arguments"]) == {"domanda": "x"}
    schema = CERCA["function"]["parameters"]
    assert adatta_argomenti({"a": 1, "b": 2}, schema) == {"a": 1, "b": 2}     # ambiguo: non si tocca
    assert adatta_argomenti({"domanda": "y"}, schema) == {"domanda": "y"}


def test_l_agente_recupera_le_chiamate_scritte_come_testo():
    from devin.ai.client import AIClient
    risposta = AIClient._chiamate_ripulite(
        {"content": MARKUP_17_10, "tool_calls": [], "reasoning": "", "finish_reason": "stop"}, [CERCA])
    assert risposta["content"] == "Giusto, cerco adesso."
    assert len(risposta["tool_calls"]) == 2


def test_lo_stream_dice_quanto_era_lungo_il_prompt(client):
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
             "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                    "timings": {"prompt_n": 300, "cache_n": 4000}}),
             "data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    eventi = list(client.stream_eventi([{"role": "user", "content": "x"}], tools=[CERCA]))
    uso = [e for e in eventi if e["tipo"] == "uso"][0]
    assert uso["prompt_tokens"] == 4300 and uso["cache_tokens"] == 4000


def test_il_catalogo_degli_strumenti_va_nel_prompt(client):
    """17/10: il modello in produzione non vedeva gli schemi; ora li legge anche nel testo."""
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}), "data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    list(client.stream_eventi([{"role": "system", "content": "Sei DEVIN."},
                               {"role": "user", "content": "x"}], tools=[CERCA]))
    sistema = client._stato["corpi"][-1]["messages"][0]["content"]
    assert sistema.startswith("Sei DEVIN.") and "- cerca_web(domanda: string) — " in sistema
    assert "<function=NOME>" in sistema
    from devin.ai.tool_text import con_catalogo
    doppio = con_catalogo(client._stato["corpi"][-1]["messages"], [CERCA])
    assert doppio[0]["content"].count("STRUMENTI DISPONIBILI") == 1
    assert con_catalogo([{"role": "user", "content": "x"}], [CERCA])[0]["role"] == "system"


def test_annullato_lo_stream_si_ferma_e_chiude_la_connessione(client):
    """17/10: ricaricando l'app, il modello continuava a generare per minuti per nessuno."""
    import threading
    annulla = threading.Event()
    righe = ["data: " + json.dumps({"choices": [{"delta": {"reasoning_content": f"p{i}"}}]}) for i in range(50)]
    client._stato["risposta"] = _FintaRisposta(righe + ["data: [DONE]"])
    flusso = client.stream_eventi([{"role": "user", "content": "x"}], annulla=annulla)
    primi = [next(flusso), next(flusso)]
    annulla.set()
    resto = list(flusso)
    assert [e["testo"] for e in primi] == ["p0", "p1"]
    assert len(resto) <= 1


def test_col_template_che_rende_i_tools_niente_catalogo_doppio(client):
    """18/10: col template di chat il server mette gli schemi nel prompt da solo."""
    client._stato["props"] = {"chat_template": "{%- if tools %}<tools>{% endif %}"}
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}), "data: [DONE]"]
    for _ in range(2):
        client._stato["risposta"] = _FintaRisposta(list(righe))
        list(client.stream_eventi([{"role": "system", "content": "Sei DEVIN."},
                                   {"role": "user", "content": "x"}], tools=[CERCA]))
        corpo = client._stato["corpi"][-1]
        assert corpo["messages"][0]["content"] == "Sei DEVIN." and corpo["tools"] == [CERCA]
    assert client._stato["props_chieste"] == 1          # una volta per server, poi ricordato


def test_template_senza_tools_tiene_il_catalogo(client):
    client._stato["props"] = {"chat_template": "{{ messages }}"}
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}), "data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    list(client.stream_eventi([{"role": "system", "content": "Sei DEVIN."},
                               {"role": "user", "content": "x"}], tools=[CERCA]))
    assert "STRUMENTI DISPONIBILI" in client._stato["corpi"][-1]["messages"][0]["content"]


def test_sforzo_high_rifiutato_con_i_valori_ammessi(client):
    import pytest as _pytest
    with _pytest.raises(ValueError) as errore:
        client._sforzo("high")
    assert "xhigh" in str(errore.value) and "medium" in str(errore.value)
    assert client._sforzo("XHIGH") == "xhigh"


def test_un_turno_con_strumenti_vuoti_non_manda_tools(client):
    """Dopo lo stop del sorvegliante l'agente chiede una risposta senza strumenti:
    niente "tools": [] con tool_choice, che il server potrebbe rifiutare (19/10)."""
    righe = ["data: " + json.dumps({"choices": [{"delta": {"content": "riassunto"}}]}), "data: [DONE]"]
    client._stato["risposta"] = _FintaRisposta(righe)
    esito = client.chiama_con_strumenti([{"role": "user", "content": "x"}], [])
    corpo = client._stato["corpi"][-1]
    assert "tools" not in corpo and "tool_choice" not in corpo
    assert esito["content"] == "riassunto" and not esito["tool_calls"]
    client._stato["risposta"] = _FintaRisposta(righe)
    client.chiama_con_strumenti([{"role": "user", "content": "x"}], [CERCA])
    assert client._stato["corpi"][-1]["tools"] == [CERCA] and client._stato["corpi"][-1]["tool_choice"] == "auto"


# --- pensiero aperto dal template (22/10) -------------------------------------

def test_il_pensiero_senza_apertura_non_finisce_nella_risposta(client):
    """22/10, spyengine: dopo uno strumento il server non separava il pensiero e
    nella risposta finiva "Hmm, wait... Let me write the scratch file. </think>"."""
    eventi = _stream(client, [{"content": "Hmm, wait. Let me"}, {"content": " check.\n</th"},
                              {"content": "ink>\n\nHo controllato i file."}])
    assert _unisci(eventi, "risposta") == "Ho controllato i file."
    assert _unisci(eventi, "ragionamento") == "Hmm, wait. Let me check.\n"


def test_senza_chiusura_il_testo_provvisorio_era_la_risposta(client):
    eventi = _stream(client, [{"content": "Ecco "}, {"content": "la risposta."}])
    assert _unisci(eventi, "risposta") == "Ecco la risposta."
    assert [e["tipo"] for e in eventi if e["tipo"] == "risposta"] == ["risposta"], "una volta sola, alla fine"


def test_col_pensiero_separato_dal_server_la_risposta_scorre_subito(client):
    eventi = _stream(client, [{"reasoning_content": "penso"}, {"content": "uno "}, {"content": "due"}])
    assert [e["testo"] for e in eventi if e["tipo"] == "risposta"] == ["uno ", "due"]


def test_spazi_iniziali_non_decidono(client):
    eventi = _stream(client, [{"content": "\n"}, {"content": "<think>x</think>y"}])
    assert _unisci(eventi, "risposta").strip() == "y" and _unisci(eventi, "ragionamento") == "x"


def test_spezza_pensiero_con_sola_chiusura():
    from devin.ai.client import AIClient
    assert AIClient._spezza_pensiero("penso ancora</think>ecco") == ("penso ancora", "ecco")


def test_uno_spazio_iniziale_non_spegne_il_pensiero_provvisorio(client):
    eventi = _stream(client, [{"content": "\n"}, {"content": "Hmm, ragiono</think>risposta"}])
    assert _unisci(eventi, "risposta").strip() == "risposta"

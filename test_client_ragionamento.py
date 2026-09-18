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


def test_un_timeout_esplicito_vince_sullo_sforzo(client):
    client._stato["risposta"] = _FintaRisposta([json.dumps(
        {"choices": [{"message": {"content": "x"}}]})])
    client.local([{"role": "user", "content": "x"}], timeout=30, sforzo="xhigh")
    assert client._stato["timeout"] == 30


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

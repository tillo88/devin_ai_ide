"""DEVIN con gli strumenti: ciclo, confini della sandbox, gate che decide, Stop."""
from __future__ import annotations

import json
from pathlib import Path

from devin.agents.tool_agent import AgenteStrumenti, STRUMENTI
from devin.ai.client import AIClient

CALC = "def somma(a, b):\n    return a + b\n"
CALC_ROTTO = "def somma(a, b):\n    return a - b\n"
TEST_CALC = "from calc import somma\n\ndef test_somma():\n    assert somma(2, 3) == 5\n"


def chiamata(nome, **args):
    return {"id": f"id_{nome}", "type": "function", "function": {"name": nome, "arguments": json.dumps(args)}}


class AIFinta:
    """Restituisce i turni del copione in ordine; registra cosa ha visto."""

    def __init__(self, turni):
        self.turni = list(turni)
        self.visti = []

    def chiama_con_strumenti(self, messages, tools, **kwargs):
        self.visti.append([dict(m) for m in messages])
        assert {t["function"]["name"] for t in tools} >= {"scrivi_file", "verifica", "fine"}
        turno = self.turni.pop(0)
        if isinstance(turno, str):
            return {"content": turno, "tool_calls": [], "finish_reason": "stop"}
        if isinstance(turno, dict):
            return turno
        return {"content": "", "tool_calls": turno, "finish_reason": "tool_calls"}


def _agente(tmp_path, ai, **kw):
    log = []
    agente = AgenteStrumenti(ai, tmp_path, log=lambda m, l="info": log.append(m), **kw)
    return agente, log


def test_lo_stream_ricompone_le_chiamate_a_pezzi():
    righe = [
        'data: {"choices":[{"delta":{"reasoning_content":"penso"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"scrivi_file","arguments":"{\\"percorso\\": \\"x.py\\","}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" \\"contenuto\\": \\"y\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"name":"verifica","arguments":"{}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    esito = AIClient._raccogli_con_strumenti(righe)
    assert esito["reasoning"] == "penso" and esito["finish_reason"] == "tool_calls"
    assert [c["function"]["name"] for c in esito["tool_calls"]] == ["scrivi_file", "verifica"]
    assert json.loads(esito["tool_calls"][0]["function"]["arguments"]) == {"percorso": "x.py", "contenuto": "y"}
    assert esito["tool_calls"][1]["id"] == "call_1"


def test_un_giro_completo_scrive_esegue_verifica_e_finisce(tmp_path):
    verifiche = []

    def verifica():
        verifiche.append(1)
        return {"status": "verified_success", "errors": []}

    ai = AIFinta([
        [chiamata("lista_file")],
        [chiamata("scrivi_file", percorso="calc.py", contenuto=CALC),
         chiamata("scrivi_file", percorso="test_calc.py", contenuto=TEST_CALC)],
        [chiamata("esegui", comando=["python", "-c", "from calc import somma; print(somma(2, 3))"])],
        [chiamata("verifica")],
        [chiamata("fine", riassunto="Creata calc.py con somma e un test.")],
    ])
    agente, log = _agente(tmp_path, ai, verifica=verifica)
    esito = agente.esegui("Crea calc.py con somma(a, b) e un test")
    assert esito.motivo == "fine" and esito.riassunto.startswith("Creata calc.py")
    assert (tmp_path / "calc.py").read_text() == CALC
    assert verifiche == [1]
    # l'uscita vera del comando torna al modello
    risultato_esegui = [m for m in ai.visti[3] if m["role"] == "tool"][-1]["content"]
    assert risultato_esegui.startswith("exit 0") and "5" in risultato_esegui
    assert any("🔧 scrivi_file calc.py" in riga for riga in log)


def test_i_confini_della_sandbox_sono_errori_per_il_modello_non_eccezioni(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    ai = AIFinta([
        [chiamata("scrivi_file", percorso="../fuori.py", contenuto="x"),
         chiamata("leggi_file", percorso="/etc/passwd"),
         chiamata("modifica_file", percorso="a.py", vecchio="x = 1", nuovo="x = 2"),
         chiamata("esegui", comando="python a.py"),
         chiamata("esegui", comando=["programma-che-non-esiste"]),
         {"id": "rotta", "type": "function", "function": {"name": "leggi_file", "arguments": "{non json"}}],
        "ho finito",
    ])
    agente, _ = _agente(tmp_path, ai)
    esito = agente.esegui("prova")
    risultati = [m["content"] for m in ai.visti[1] if m["role"] == "tool"]
    assert "fuori dal progetto" in risultati[0] and "fuori dal progetto" in risultati[1]
    assert "compare 2 volte" in risultati[2]
    assert "lista di testi" in risultati[3]
    assert "programma non trovato" in risultati[4]
    assert "non JSON" in risultati[5]
    assert not (tmp_path.parent / "fuori.py").exists()
    assert esito.motivo == "risposta"


def test_la_sintassi_rotta_torna_subito_al_modello(tmp_path):
    ai = AIFinta([[chiamata("scrivi_file", percorso="b.py", contenuto="def f(:\n")], "ok"])
    agente, _ = _agente(tmp_path, ai)
    agente.esegui("x")
    assert "ERRORI DI SINTASSI" in [m for m in ai.visti[1] if m["role"] == "tool"][0]["content"]


def test_stop_risposta_tagliata_e_limite_di_turni(tmp_path):
    fermo = {"si": False}
    ai = AIFinta([[chiamata("lista_file")]] * 5)
    agente, _ = _agente(tmp_path, ai, should_stop=lambda: fermo["si"], max_turni=3)
    assert agente.esegui("x").motivo == "turni"
    fermo["si"] = True
    assert agente.esegui("y").motivo == "stop"

    ai = AIFinta([{"content": "def lunghissimo", "tool_calls": [], "finish_reason": "length"},
                  [chiamata("fine", riassunto="ok")]])
    agente, log = _agente(tmp_path, ai)
    assert agente.esegui("z").motivo == "fine"
    assert "interrotta per lunghezza" in ai.visti[1][-1]["content"]


def test_un_contesto_pieno_riduce_i_risultati_vecchi(tmp_path):
    (tmp_path / "grande.txt").write_text("y" * 9000)
    ai = AIFinta([[chiamata("leggi_file", percorso="grande.txt")]] * 12 + ["fine"])
    agente, _ = _agente(tmp_path, ai, contesto_token=20_000)
    agente.esegui("leggi")
    ultimi = ai.visti[-1]
    ridotti = [m for m in ultimi if m["role"] == "tool" and "risultato vecchio ridotto" in m["content"]]
    interi = [m for m in ultimi if m["role"] == "tool" and len(m["content"]) == 9000]
    assert ridotti and len(interi) >= 1


def test_gli_strumenti_hanno_schemi_chiusi():
    for strumento in STRUMENTI:
        parametri = strumento["function"]["parameters"]
        assert parametri["type"] == "object" and parametri["additionalProperties"] is False


def test_lo_scaffold_con_l_agente_ripassa_dal_gate_impara_e_applica(tmp_path):
    from test_orchestrator_wholefile_scaffold import _make_orchestrator, _make_project
    from devin.ai.hybrid_memory_client import HybridMemoryClient

    project = _make_project(str(tmp_path))
    orch, lp, ap = _make_orchestrator(project, lambda messages, **kw: "OK")
    turni = [
        # giro 1: scrive codice sbagliato e dichiara finito senza verificare
        [chiamata("scrivi_file", percorso="calc.py", contenuto=CALC_ROTTO),
         chiamata("scrivi_file", percorso="test_calc.py", contenuto=TEST_CALC)],
        [chiamata("fine", riassunto="fatto")],
        # giro 2: riceve gli errori del gate, corregge, verifica, finisce
        [chiamata("modifica_file", percorso="calc.py", vecchio="a - b", nuovo="a + b")],
        [chiamata("verifica")],
        [chiamata("fine", riassunto="Somma corretta, test verdi.")],
    ]
    ai = AIFinta(turni)
    try:
        orch.ai_client.chiama_con_strumenti = ai.chiama_con_strumenti
        orch.agent_mode = True
        orch.change_application_mode = "review"
        orch.auto_apply_verified = True
        orch.memory_client = HybridMemoryClient({"local_memory": {"path": str(tmp_path / "mem.jsonl")},
                                                 "understory": {"enabled": False},
                                                 "automem": {"enabled": False}})
        from unittest.mock import patch
        with patch("devin.engine.git_ops.GitOps.commit", return_value={"success": True}):
            result = orch.run_scaffold("Calcolatrice con somma", project_path=str(project),
                                       run_id="run_agente")
    finally:
        lp.stop()
        ap.stop()
    assert result["status"] == "verified_success", result
    assert result["applied"] is True
    assert "return a + b" in (project / "calc.py").read_text()
    assert (project / "test_calc.py").exists()
    # il secondo giro e' partito dagli errori veri del gate, nella stessa conversazione
    feedback = [m for m in ai.visti[2] if m["role"] == "user"][-1]["content"]
    assert "NON e' passata" in feedback and "esito: verified_failure" in feedback
    assert orch.ultimo_riassunto_agente == "Somma corretta, test verdi."
    memoria = (tmp_path / "mem.jsonl").read_text()
    assert "SOLUZIONE VERIFICATA" in memoria and "+    return a + b" in memoria

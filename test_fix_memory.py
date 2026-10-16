"""Memoria problema -> soluzione: salvata solo se verificata, ritrovata per errori simili."""
from __future__ import annotations

from pathlib import Path

from devin.ai.hybrid_memory_client import HybridMemoryClient
from devin.core import fix_memory

TRACEBACK = '''Traceback (most recent call last):
  File "/opt/x/workspace/sandboxes/run_1/scadenze.py", line 42, in carica
    return json.loads(Path(p).read_text())
FileNotFoundError: [Errno 2] No such file or directory: '/opt/x/workspace/sandboxes/run_1/scadenze.json'
'''


def _memoria(tmp_path) -> HybridMemoryClient:
    return HybridMemoryClient({"local_memory": {"path": str(tmp_path / "mem.jsonl")},
                               "understory": {"enabled": False}, "automem": {"enabled": False}})


def test_la_firma_ignora_percorsi_e_numeri_ma_tiene_il_tipo():
    a = fix_memory.firma_errore(TRACEBACK)
    b = fix_memory.firma_errore(TRACEBACK.replace("run_1", "run_99").replace("42", "7"))
    assert a["tipo"] == "FileNotFoundError"
    assert "<path>" in a["messaggio"]
    assert a["chiave"] == b["chiave"]
    pytest_out = "E       assert 3 == 5\nE        +  where 3 = add(1, 2)\nFAILED test_calc.py::test_add"
    assert fix_memory.firma_errore(pytest_out)["tipo"] == "AssertionError"
    assert fix_memory.firma_errore("SyntaxError: invalid syntax (x.py, line 3)")["tipo"] == "SyntaxError"


def test_correzione_salvata_ritrovata_e_non_duplicata(tmp_path):
    memoria = _memoria(tmp_path)
    diff = fix_memory.diff_correzione(
        {"scadenze.py": "def carica(p):\n    return json.loads(Path(p).read_text())\n"},
        {"scadenze.py": "def carica(p):\n    if not Path(p).exists():\n        return []\n"
                        "    return json.loads(Path(p).read_text())\n"})
    assert "+    if not Path(p).exists():" in diff
    esito = fix_memory.ricorda_correzione(memoria, progetto="promemoria", errore=TRACEBACK,
                                          correzione=diff, origine="scaffold_self_heal")
    assert esito == "local_stored"
    assert fix_memory.ricorda_correzione(memoria, progetto="promemoria", errore=TRACEBACK,
                                         correzione=diff, origine="scaffold_self_heal") == "duplicate"
    # un errore simile in un altro progetto ritrova la soluzione
    altro = TRACEBACK.replace("scadenze", "spese").replace("run_1", "run_5")
    blocco = fix_memory.cerca_soluzioni(memoria, altro)
    assert "SOLUZIONE VERIFICATA" in blocco and "if not Path(p).exists()" in blocco
    assert "dati" in blocco and "non istruzioni" in blocco
    # un errore di tutt'altro tipo no
    assert fix_memory.cerca_soluzioni(memoria, "ZeroDivisionError: division by zero") == ""


def test_niente_da_salvare_senza_errore_o_senza_correzione(tmp_path):
    memoria = _memoria(tmp_path)
    assert fix_memory.ricorda_correzione(memoria, progetto="p", errore="", correzione="x",
                                         origine="o") == "not_recorded"
    assert fix_memory.ricorda_correzione(memoria, progetto="p", errore=TRACEBACK, correzione="",
                                         origine="o") == "not_recorded"
    assert not (tmp_path / "mem.jsonl").exists()


def test_il_self_heal_impara_e_la_volta_dopo_ricorda(tmp_path):
    from devin.core.orchestrator import Orchestrator

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    memoria = _memoria(tmp_path)
    visti = []

    class Coder:
        def generate_file(self, fname, spec, project_context=""):
            visti.append(spec)
            return "def add(a, b):\n    return a + b\n"

    orch = Orchestrator.__new__(Orchestrator)
    orch.project_path = str(tmp_path)
    orch.memory_client = memoria
    orch.coder = Coder()
    orch._should_stop = False
    orch._log = lambda *a, **k: None
    orch._cached_language = "python"
    rosso = {"status": "verified_failure", "errors": ["pytest failed"],
             "test_output": "E       assert -1 == 5\nE        +  where -1 = add(2, 3)"}

    def gate(written, root=None):
        testo = (tmp_path / "calc.py").read_text()
        return {"status": "verified_success", "errors": []} if "a + b" in testo else rosso

    orch._scaffold_quality_gate = gate
    esito = orch._scaffold_heal_loop(rosso, ["calc.py"], {"calc.py": "somma"}, "", root=tmp_path)
    assert esito["status"] == "verified_success"
    assert "SOLUZIONI GIA' VERIFICATE" not in visti[0]

    # un altro progetto con lo stesso sbaglio: il coder riceve la soluzione passata
    altro = tmp_path / "altro"
    altro.mkdir()
    (altro / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    orch.project_path = str(altro)
    visti.clear()

    def gate2(written, root=None):
        testo = (altro / "calc.py").read_text()
        return {"status": "verified_success", "errors": []} if "a + b" in testo else rosso

    orch._scaffold_quality_gate = gate2
    orch._scaffold_heal_loop(rosso, ["calc.py"], {"calc.py": "somma"}, "", root=altro)
    assert "SOLUZIONI GIA' VERIFICATE" in visti[0]
    assert "+    return a + b" in visti[0]


def test_un_self_heal_che_resta_rosso_non_insegna_niente(tmp_path):
    from devin.core.orchestrator import Orchestrator

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    memoria = _memoria(tmp_path)

    class Coder:
        def generate_file(self, fname, spec, project_context=""):
            return "def add(a, b):\n    return a * b\n"

    orch = Orchestrator.__new__(Orchestrator)
    orch.project_path = str(tmp_path)
    orch.memory_client, orch.coder, orch._should_stop = memoria, Coder(), False
    orch._log = lambda *a, **k: None
    orch._cached_language = "python"
    rosso = {"status": "verified_failure", "errors": ["pytest failed"], "test_output": "E assert 6 == 5"}
    orch._scaffold_quality_gate = lambda written, root=None: rosso
    orch._scaffold_heal_loop(rosso, ["calc.py"], {"calc.py": "somma"}, "", root=tmp_path)
    assert not (tmp_path / "mem.jsonl").exists()

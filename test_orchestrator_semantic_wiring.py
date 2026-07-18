#!/usr/bin/env python3
"""
Regression tests for the Orchestrator semantic-context wiring defect
(2026-07-18, side observation del W9 multi-progetto — promossa a fix dedicato):

    Orchestrator.__init__ creava DUE VectorStore distinti:
    - `self.vector_store` (orchestrator.py), indicizzato da run() via
      `index_project(...)` durante la fase "Building context";
    - `self.context_retriever.store` (context_retriever.py), su cui
      `retrieve()` -> `search_semantic(...)` cercava SEMPRE — mai indicizzato.

    Risultato: il blocco "FILE RILEVANTI SEMANTICAMENTE AL TASK" era sempre
    vuoto. Silenzioso perche' `search_semantic` su indice vuoto torna []
    e `build_context` lo traduce in "" senza errori — indistinguibile da
    "nessun file rilevante".

Il test di integrazione e' comportamentale: dopo la fase di indicizzazione
di run(), il retriever DEVE trovare il contenuto di un file noto del
progetto. Non lega la shape del costruttore: qualsiasi wiring che faccia
confluire indicizzazione e ricerca sullo stesso store lo fa passare.

Modelli e launcher mockati: niente GPU, rete o llama-server. Fixture
autocontenute (copiate da test_stalled_guard.py, come da stile del repo),
con `semantic_search_enabled: True` — nei fixture base e' False e la fase
vector store verrebbe saltata del tutto.
"""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from devin.core.orchestrator import Orchestrator, LauncherStatus
from devin.core.context_retriever import ContextRetriever
from devin.memory.vector_store import VectorStore


def _make_orchestrator(project: Path, mock_local):
    config = {
        "context": {"max_chars": 100000, "semantic_search_enabled": True},
        "coder": {"whole_file_enabled": False},
        "models": {
            "local_models_dir": str(project / "models"),
            "llama_server_path": "/bin/echo",
            "auto_start_local": False,
            "local_models": {}
        }
    }
    config_path = project / "config.json"
    config_path.write_text(json.dumps(config))

    mock_client = MagicMock()
    mock_client.refresh = MagicMock()
    mock_client.health.return_value = {}
    mock_client.local = mock_local

    mock_launcher = MagicMock()
    mock_launcher.ensure_models.return_value = LauncherStatus(
        rig_available=False,
        rig_host="",
        rig_ports=[],
        local_running={
            "8000": {"name": "coder", "port": 8000, "status": "running"},
            "8001": {"name": "planner", "port": 8001, "status": "running"},
        },
        model_source="local",
        errors=[],
    )
    mock_launcher.get_status.return_value = mock_launcher.ensure_models.return_value
    mock_launcher.shutdown_all = MagicMock()

    launcher_patch = patch('devin.core.orchestrator.LocalModelLauncher')
    ai_patch = patch('devin.core.orchestrator.AIClient')
    MockLauncher = launcher_patch.start()
    MockAI = ai_patch.start()
    MockLauncher.from_config.return_value = mock_launcher
    MockAI.return_value = mock_client

    orch = Orchestrator(config_path=str(config_path), project_path=str(project))
    return orch, launcher_patch, ai_patch


def _make_project(tmpdir: str) -> Path:
    project = Path(tmpdir) / "project"
    project.mkdir()
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (project / "main.py").write_text("from calc import add\nprint(add(2, 3))\n")
    return project


def _user_content(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def test_run_indexes_the_same_store_the_retriever_searches():
    """Dopo la fase di indicizzazione di run(), retrieve() DEVE trovare il
    contenuto di un file noto del progetto. Pre-fix: il retriever cercava
    sul proprio VectorStore mai indicizzato -> sempre "" (silenzioso)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                raise RuntimeError("Connection refused")  # stallo rapido
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        # Il run fallisce/stalla per il coder offline — irrilevante qui:
        # la fase "Building context" (index_project) e' gia' avvenuta.
        assert result["success"] is False

        semantic = orch.context_retriever.retrieve(
            "add numbers sum calculation", str(project))
        assert semantic, (
            "semantic context vuoto dopo l'indicizzazione di run(): "
            "il retriever cerca su uno store diverso da quello indicizzato"
        )
        assert "calc.py" in semantic
        print("✓ Semantic wiring: run() indicizza lo store che retrieve() cerca")


def test_context_retriever_uses_injected_store():
    """Unit pin: ContextRetriever accetta uno store per dependency injection
    e lo usa per la ricerca (niente seconda istanza privata)."""
    store = VectorStore()
    retriever = ContextRetriever(enabled=True, store=store)
    assert retriever.store is store
    print("✓ ContextRetriever usa lo store iniettato")


def test_context_retriever_default_store_is_own_instance():
    """Default invariato: senza injection il retriever crea il suo store
    (retrocompatibilita' per caller esterni all'Orchestrator)."""
    retriever = ContextRetriever(enabled=True)
    assert isinstance(retriever.store, VectorStore)
    print("✓ ContextRetriever senza injection crea il proprio store")

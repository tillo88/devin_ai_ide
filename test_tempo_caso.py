"""Tetto di tempo per caso del training (29/10): un caso oltre un'ora bloccava il giro della palestra."""
import asyncio
from pathlib import Path
import time

import devin.ui.routers.training as training_router
from devin.core.orchestrator import Orchestrator
from devin.training.store import TrainingStore
from test_training_runner_declass import _patch_runner_surface, _register_job, _runner_case  # noqa: F401


class _Req:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


def test_allo_scadere_e_come_uno_stop():
    orch = Orchestrator.__new__(Orchestrator)
    orch._log = lambda *a, **k: None
    assert orch._should_stop is False and orch.tempo_scaduto is False
    orch.scadenza_caso = time.time() + 60
    assert orch._should_stop is False
    orch.scadenza_caso = time.time() - 1
    assert orch._should_stop is True and orch.tempo_scaduto is True
    altro = Orchestrator.__new__(Orchestrator)
    altro._log = lambda *a, **k: None
    altro._should_stop = True
    assert altro._should_stop is True and altro.tempo_scaduto is False, "lo stop vero resta uno stop"
    altro._should_stop = False
    assert altro._should_stop is False


def test_l_agente_vede_la_scadenza(tmp_path):
    """L'agente riceve should_stop=lambda: self._should_stop: la scadenza gli arriva cosi'."""
    from devin.agents.tool_agent import AgenteStrumenti
    orch = Orchestrator.__new__(Orchestrator)
    orch._log = lambda *a, **k: None
    orch.scadenza_caso = time.time() - 1

    class Ai:
        def chiama_con_strumenti(self, *a, **k):
            raise AssertionError("nessun turno nuovo dopo la scadenza")

    agente = AgenteStrumenti(Ai(), tmp_path, log=lambda *a, **k: None, should_stop=lambda: orch._should_stop)
    assert agente.esegui("compito").motivo == "stop"


def caso_lento(monkeypatch, tmp_path, scade):
    visti = {}

    def orch_factory():
        class Orch:
            def __init__(self, **kw):
                self.project = Path(kw["project_path"])
                self.tempo_scaduto = False

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def run_scaffold(self, task, project_path, run_id):
                visti["scadenza"] = self.scadenza_caso
                self.tempo_scaduto = scade
                return {"success": False, "status": "failed", "files_written": [],
                        "quality_gate": {"status": "verified_failure", "errors": ["test rossi"]}}
        return Orch

    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", orch_factory())
    caso = _runner_case(expected_signals=["tests_pass"])
    job = _register_job("job_t", [caso])
    job["max_minuti_caso"] = 20
    store = TrainingStore(tmp_path / "t")
    inizio = time.time()
    training_router._run_training_cases_background("job_t", store, [caso], "mbpp")
    return store.list_attempts()[-1], visti["scadenza"] - inizio


def test_il_training_mette_la_scadenza_e_la_dice(monkeypatch, tmp_path):
    tentativo, secondi = caso_lento(monkeypatch, tmp_path, scade=True)
    assert 19 * 60 < secondi <= 20 * 60 + 5, "la scadenza del job"
    assert tentativo["status"] == "auto_failure"
    assert tentativo["error_reason"].startswith("tempo del caso scaduto (20 min): ")


def test_senza_scadenza_il_motivo_resta_quello(monkeypatch, tmp_path):
    tentativo, _ = caso_lento(monkeypatch, tmp_path, scade=False)
    assert not tentativo["error_reason"].startswith("tempo del caso scaduto")


def test_il_run_porta_il_tetto_del_caso(monkeypatch, tmp_path):
    store = TrainingStore(tmp_path / "t")
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    monkeypatch.setattr(training_router.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: None})())
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "devin-mini", "max_cases": 1})))
    assert out["job"]["max_minuti_caso"] == training_router.MINUTI_PER_CASO == 30
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "devin-mini", "max_cases": 1,
                                                             "max_minutes_per_case": 45})))
    assert out["job"]["max_minuti_caso"] == 45
    for cattivo in (0.5, 500, "x"):
        risposta = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "devin-mini",
                                                                      "max_minutes_per_case": cattivo})))
        assert "error" in risposta, cattivo

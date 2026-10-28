"""28/10: gold ufficiali verdi ma test scritti da DEVIN rossi = esercizio risolto, con un segnale a parte."""
from pathlib import Path

import devin.ui.routers.training as training_router
from devin.training.store import TrainingStore
from test_training_runner_declass import (_patch_runner_surface, _register_job,  # noqa: F401
                                          _runner_case, _isolate_training_jobs)

GOLD = "from sol import add\n\n\ndef test_gold_add():\n    assert add(2, 3) == 5\n"
GIUSTA = "def add(a, b):\n    return a + b\n"
SBAGLIATA = "def add(a, b):\n    return a - b\n"
TEST_DI_DEVIN_ROSSO = "from sol import add\n\n\ndef test_inventato():\n    assert add('a', 1) is None  # requisito inventato\n"


def giro(monkeypatch, tmp_path, *, soluzione=GIUSTA, gold_in_sandbox=GOLD, segnali=("tests_pass",), fuori=False):
    def orch_factory():
        class Orch:
            def __init__(self, **kw):
                self.project = Path(kw["project_path"])

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def run_scaffold(self, task, project_path, run_id):
                base = tmp_path / "altrove" if fuori else Path(project_path)
                sandbox = base / "workspace" / "sandboxes" / run_id
                sandbox.mkdir(parents=True)
                (sandbox / "sol.py").write_text(soluzione)
                (sandbox / "test_sol.py").write_text(TEST_DI_DEVIN_ROSSO)
                (sandbox / "test_gold_add.py").write_text(gold_in_sandbox)
                return {"success": False, "status": "failed", "files_written": ["sol.py", "test_sol.py"],
                        "quality_gate": {"status": "verified_failure", "errors": ["test_sol.py::test_inventato FAILED"]},
                        "sandbox_path": str(sandbox)}
        return Orch

    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", orch_factory())
    caso = _runner_case(expected_signals=list(segnali), metadata={"gold_tests": {"test_gold_add.py": GOLD}})
    _register_job("job_v", [caso])
    store = TrainingStore(tmp_path / "t")
    training_router._run_training_cases_background("job_v", store, [caso], "mbpp")
    with training_router._training_jobs_lock:
        job = dict(training_router._training_jobs["job_v"])
    return store.list_attempts()[-1], job


def test_soluzione_giusta_e_test_propri_rossi_conta_come_risolto(monkeypatch, tmp_path):
    tentativo, job = giro(monkeypatch, tmp_path)
    assert tentativo["status"] == "auto_success", tentativo["error_reason"]
    assert tentativo["tests"]["test_propri_rossi"] is True and "test_inventato" in tentativo["tests"]["motivo_test_propri"]
    assert "altri test rossi" in tentativo["tests"]["gold_executed"]["detail"]
    assert job["auto_success"] == 1 and job["test_propri_rossi"] == 1
    assert job["results"][0]["test_propri_rossi"] is True


def test_soluzione_sbagliata_resta_un_fallimento(monkeypatch, tmp_path):
    tentativo, job = giro(monkeypatch, tmp_path, soluzione=SBAGLIATA)
    assert tentativo["status"] == "auto_failure" and tentativo["tests"]["test_propri_rossi"] is False
    assert job.get("test_propri_rossi", 0) == 0


def test_gold_toccato_nella_sandbox_non_salva_nulla(monkeypatch, tmp_path):
    tentativo, _ = giro(monkeypatch, tmp_path, soluzione=SBAGLIATA,
                        gold_in_sandbox="def test_gold_add():\n    assert True\n")
    assert tentativo["status"] == "auto_failure"


def test_solo_per_i_casi_che_chiedono_soltanto_test_verdi(monkeypatch, tmp_path):
    tentativo, _ = giro(monkeypatch, tmp_path, segnali=("tests_pass", "file_created"))
    assert tentativo["status"] == "auto_failure", "altri segnali attesi: niente salvataggio"


def test_sandbox_fuori_dal_progetto_non_vale(monkeypatch, tmp_path):
    tentativo, _ = giro(monkeypatch, tmp_path, fuori=True)
    assert tentativo["status"] == "auto_failure"


def test_la_verifica_normale_resta_severa(tmp_path):
    """Fuori dal salvataggio un altro test rosso continua a bocciare: solo_gold e' l'unica eccezione."""
    (tmp_path / "sol.py").write_text(GIUSTA)
    (tmp_path / "test_gold_add.py").write_text(GOLD)
    (tmp_path / "test_sol.py").write_text(TEST_DI_DEVIN_ROSSO)
    normale = training_router._verify_gold_tests_executed(tmp_path, ["test_gold_add.py"])
    assert normale["executed"] is False and "suite instabile" in normale["detail"]
    solo = training_router._verify_gold_tests_executed(tmp_path, ["test_gold_add.py"], solo_gold=True)
    assert solo["executed"] is True and solo["missing"] == []

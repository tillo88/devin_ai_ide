"""La lezione di un ripasso riuscito (30/10): perche' era fallito e cosa e' cambiato nel codice."""
from pathlib import Path

import devin.ui.routers.training as training_router
from devin.training.store import TrainingStore
from test_training_runner_declass import (_fake_gold_check, _patch_runner_surface, _register_job,  # noqa: F401
                                          _runner_case, GREEN_GATE)

SBAGLIATA = "def add(a, b):\n    return a - b\n"
GIUSTA = "def add(a, b):\n    return a + b\n"


class Memoria:
    def __init__(self):
        self.salvati = []

    def store_local(self, contenuto, tags=None, importance=0.5):
        self.salvati.append((contenuto, tags))
        return "local_stored"

    def store(self, *a, **k):
        return "stored"

    def recall(self, *a, **k):
        return []


def giro(monkeypatch, tmp_path, memoria, *, riesce=True, codice=GIUSTA, precedente=True, etichetta="palestra"):
    def orch_factory():
        class Orch:
            def __init__(self, **kw):
                self.memory_client = memoria
                self.etichetta_memoria = ""
                self.correzioni_salvate = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def run_scaffold(self, task, project_path, run_id):
                visti["prompt"] = task
                (Path(project_path) / "sol.py").write_text(codice)
                (Path(project_path) / "test_sol.py").write_text("def test_x():\n    assert True\n")
                if riesce:
                    return {"success": True, "status": "completed", "files_written": ["sol.py"],
                            "quality_gate": dict(GREEN_GATE)}
                return {"success": False, "status": "failed", "files_written": ["sol.py"],
                        "quality_gate": {"status": "verified_failure", "errors": ["test rossi"]}}
        return Orch

    visti = {}
    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", orch_factory())
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    caso = _runner_case(expected_signals=["tests_pass"], title="MBPP 7: somma",
                        metadata={"gold_tests": {"test_gold_add.py": "def test_g():\n    assert True\n"}})
    store = TrainingStore(tmp_path / "t")
    vecchio = tmp_path / "vecchio"
    sandbox = vecchio / "workspace" / "sandboxes" / "train_1"
    sandbox.mkdir(parents=True)
    (sandbox / "sol.py").write_text(SBAGLIATA)
    (sandbox / "test_gold_add.py").write_text("GOLD")
    (sandbox / "conftest.py").write_text("CONFTEST = 1")
    if precedente:
        store.seed_cases([{k: v for k, v in caso.items() if k != "case_id"}], source="mbpp")
        caso = dict(store.list_cases(limit=10)[0])
        store.add_attempt(case_id=caso["case_id"], status="auto_failure",
                          error_reason="gold test non eseguiti: non raccolti/passati: test_gold_add.py",
                          tests={"gold_executed": {"exit_code": 1, "output": "E   assert -1 == 5"}},
                          artifacts=[str(vecchio)])
    job = _register_job("job_l", [caso])
    job["memory_label"] = etichetta
    training_router._run_training_cases_background("job_l", store, [caso], "mbpp")
    with training_router._training_jobs_lock:
        job = dict(training_router._training_jobs["job_l"])
    return store, job, visti


def test_il_ripasso_riuscito_salva_la_lezione(monkeypatch, tmp_path):
    memoria = Memoria()
    store, job, visti = giro(monkeypatch, tmp_path, memoria)
    assert "TENTATIVO PRECEDENTE FALLITO" in visti["prompt"]
    assert store.list_attempts()[-1]["status"] == "auto_success"
    assert job["lessons_saved"] == 1 and job["results"][0]["lessons"] == 1
    (contenuto, tags), = memoria.salvati
    assert "assert -1 == 5" in contenuto and "gold test non eseguiti" in contenuto, "il perche' del fallimento"
    assert "-    return a - b" in contenuto and "+    return a + b" in contenuto, "cosa e' cambiato"
    assert "Esercizio: MBPP 7: somma" in contenuto
    assert "origin:palestra:ripasso" in tags and "status:verified_success" in tags
    assert "GOLD" not in contenuto and "CONFTEST" not in contenuto, "mai i gold, mai i conftest"


def test_senza_tentativo_fallito_nessuna_lezione_del_ripasso(monkeypatch, tmp_path):
    memoria = Memoria()
    _store, job, _ = giro(monkeypatch, tmp_path, memoria, precedente=False)
    assert memoria.salvati == [] and job["lessons_saved"] == 0


def test_se_il_ripasso_fallisce_di_nuovo_nessuna_lezione(monkeypatch, tmp_path):
    memoria = Memoria()
    store, job, _ = giro(monkeypatch, tmp_path, memoria, riesce=False)
    assert store.list_attempts()[-1]["status"] == "auto_failure"
    assert memoria.salvati == [] and job["lessons_saved"] == 0


def test_la_memoria_rotta_non_ferma_il_caso(monkeypatch, tmp_path):
    class Rotta(Memoria):
        def store_local(self, *a, **k):
            raise RuntimeError("AutoMem giu'")
    store, job, _ = giro(monkeypatch, tmp_path, Rotta())
    assert store.list_attempts()[-1]["status"] == "auto_success" and job["lessons_saved"] == 0


def test_la_lezione_ha_un_tetto():
    grande = "\n".join(f"x{i} = {i}" for i in range(2000))
    errore, correzione = training_router._lezione_dal_ripasso(
        {"error_reason": "motivo", "_uscita_gold": "uscita"}, {"a.py": ""}, {"a.py": grande}, "t")
    assert errore == "motivo\nuscita" and "(diff tagliato)" in correzione
    assert len(correzione) < training_router.MAX_DIFF_LEZIONE + 400

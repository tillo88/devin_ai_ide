"""Pin della catena verdict/declass del runner training background.

Baseline (2026-07-18) che precede il batch di fix approvati: pinna il
comportamento CORRENTE di `_run_training_cases_background`
(devin/ui/routers/training.py L303-452). Il fix A (validator crash
fail-closed, 2026-07-18) ha ribaltato il test #6: ora il crash declassa.

Il runner viene chiamato SINCRONAMENTE (niente thread) con FakeOrch
(pattern test_state_persistence.py L178-193) monkeypatchato su
`fast_app.Orchestrator`: il runner risolve le dipendenze del run-core con
lazy import a call time, quindi i patch su fast_app restano validi.
Il job viene registrato in `_training_jobs` replicando il dict minimo di
`/api/training/run` (stesso modulo, L455-509). Nessun sorgente toccato.
"""

import threading
from datetime import datetime
from pathlib import Path

import pytest

import devin.ui.routers.training as training_router
from devin.training.store import TrainingStore


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_training_jobs():
    """Isola `_training_jobs` (stato globale del router) tra i test."""
    with training_router._training_jobs_lock:
        saved = dict(training_router._training_jobs)
        training_router._training_jobs.clear()
    yield
    with training_router._training_jobs_lock:
        training_router._training_jobs.clear()
        training_router._training_jobs.update(saved)


def _runner_case(**overrides):
    """Case dict nello stile di `_steam_case()` (test_training_quality_gate)."""
    case = {
        "case_id": "case_runner",
        "title": "Runner case",
        "task": "Scrivi una funzione add e verificala",
        "tags": ["python"],
        "expected_signals": ["file_created"],
        "metadata": {},
    }
    case.update(overrides)
    return case


def _fake_orch(result=None, side_effect=None, during_run=None):
    """FakeOrch context-manager (pattern test_state_persistence L178-193).

    `during_run(project_path)` simula cio' che lo scaffold scrive nel sandbox
    (file prodotti, manomissione dei gold test...)."""
    class FakeOrch:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_scaffold(self, task, project_path, run_id):
            if during_run is not None:
                during_run(Path(project_path))
            if side_effect is not None:
                raise side_effect
            return dict(result)

    return FakeOrch


def _patch_runner_surface(monkeypatch, tmp_path):
    """Neutralizza le dipendenze lazy-importate da fast_app: log/events I/O
    finti, sandbox e log dir dentro tmp_path. active_runs/runs_lock reali
    (dict + threading.Lock innocui)."""
    import devin.ui.fast_app as fast_app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(fast_app, "LOG_DIR", log_dir)
    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(fast_app, "_run_events", type("Events", (), {
        "start": lambda self, *a, **k: {},
        "finish": lambda self, *a, **k: {},
        "append_log": lambda self, *a, **k: {},
    })())
    monkeypatch.setattr(
        fast_app, "_make_run_callback", lambda run_id, log_path: (lambda *a, **k: None))
    monkeypatch.setattr(fast_app, "_finish_run_events", lambda *a, **k: None)
    return fast_app


def _register_job(job_id, cases, benchmark_id="bench-test"):
    """Replica il dict minimo che `/api/training/run` registra in
    `_training_jobs` (training.py L485-501). Nota: la route NON inizializza
    la chiave `runner_error` — il runner la crea via .get(..., 0)."""
    job = {
        "job_id": job_id,
        "benchmark_id": benchmark_id,
        "status": "queued",
        "total": len(cases),
        "completed": 0,
        "auto_success": 0,
        "auto_failure": 0,
        "case_ids": [c.get("case_id") for c in cases],
        "attempt_ids": [],
        "run_ids": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "requires_teacher_validation": True,
    }
    with training_router._training_jobs_lock:
        training_router._training_jobs[job_id] = job
    return job


def _fake_gold_check(project_path, gold_names, **kwargs):
    """Fake di `_verify_gold_tests_executed`: i pin esistenti non devono
    fare run pytest reali (specchio del gate finto via result dict)."""
    return {"executed": True, "command": "pytest", "exit_code": 0,
            "missing": [], "output": "",
            "detail": f"{len(gold_names)} gold file eseguiti e passati"}


def _run_sync(monkeypatch, tmp_path, case, *, result=None, side_effect=None,
              during_run=None, gold_check=None):
    """Esegue il runner in modo sincrono e deterministico. Ritorna
    (job, attempts) con il job letto DENTRO il lock dopo la fine.

    `gold_check`: None -> fake verde (default, niente pytest reale);
    "real" -> la funzione vera del router (run pytest nel sandbox);
    callable -> fake custom (es. rossa per il pin del declass)."""
    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(
        fast_app, "Orchestrator",
        _fake_orch(result=result, side_effect=side_effect, during_run=during_run))
    if gold_check is None:
        gold_check = _fake_gold_check
    if gold_check != "real":
        monkeypatch.setattr(training_router, "_verify_gold_tests_executed", gold_check)
    store = TrainingStore(tmp_path / "training")
    _register_job("job_test", [case])
    training_router._run_training_cases_background("job_test", store, [case], "bench-test")
    with training_router._training_jobs_lock:
        job = dict(training_router._training_jobs["job_test"])
    return job, store.list_attempts()


def _write(path, text):
    path.write_text(text, encoding="utf-8")


GOLD_NAME = "test_gold_add.py"
GOLD_CONTENT = "def test_gold_add():\n    assert 1 + 1 == 2\n"

GREEN_GATE = {"status": "verified_success", "tests_run": True,
              "test_command": "pytest", "errors": []}


# ---------------------------------------------------------------------------
# 1. scaffold pulito -> auto_success
# ---------------------------------------------------------------------------

def test_runner_clean_scaffold_auto_success(monkeypatch, tmp_path):
    case = _runner_case(metadata={"gold_tests": {GOLD_NAME: GOLD_CONTENT}})
    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py"]}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result,
        during_run=lambda p: _write(p / "sol.py", "def add(a, b):\n    return a + b\n"))

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["status"] == "auto_success"
    assert attempt["error_reason"] == ""
    assert attempt["case_id"] == "case_runner"
    assert attempt["run_id"].startswith("train_")
    tests = attempt["tests"]
    assert tests["source"] == "devin_training_runner"
    assert tests["passed"] is True
    assert tests["requires_teacher_validation"] is True
    assert tests["infra_error"] is False
    assert tests["quality_gate"]["status"] == "verified_success"
    assert tests["validators"]["overall"] == "pass"
    assert tests["gold_tests"] == [GOLD_NAME]
    assert tests["gold_tampered"] == []
    assert tests["gold_executed"]["executed"] is True

    assert job["status"] == "finished"
    assert job["completed"] == 1
    assert job["auto_success"] == 1
    assert job["auto_failure"] == 0
    assert job.get("runner_error", 0) == 0
    assert job["current_case_id"] == "" and job["current_title"] == ""
    assert job["attempt_ids"] == [attempt["attempt_id"]]
    assert job["run_ids"] == [attempt["run_id"]]


# ---------------------------------------------------------------------------
# 2. quality gate rosso -> declass auto_failure
# ---------------------------------------------------------------------------

def test_runner_gate_red_auto_failure(monkeypatch, tmp_path):
    case = _runner_case()
    result = {"success": True,
              "quality_gate": {"status": "verified_failure", "tests_run": True,
                               "test_command": "pytest",
                               "errors": ["pytest exit 1: 1 failed"]},
              "files_written": ["sol.py"]}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result,
        during_run=lambda p: _write(p / "sol.py", "def add(a, b):\n    return a - b\n"))

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    # prefisso reale dal sorgente (L398): "quality gate: " + errori joinati
    assert attempt["error_reason"] == "quality gate: pytest exit 1: 1 failed"
    tests = attempt["tests"]
    assert tests["passed"] is False
    assert tests["requires_teacher_validation"] is True  # auto_failure: in review queue
    assert tests["quality_gate"]["status"] == "verified_failure"

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 3. validatori caso falliti -> declass auto_failure
# ---------------------------------------------------------------------------

def test_runner_validator_fail_auto_failure(monkeypatch, tmp_path):
    # gate verde, ma files_written dichiara ghost.py mai scritto nel sandbox:
    # file_created fallisce -> overall "fail" -> declass.
    case = _runner_case()
    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["ghost.py"]}
    job, attempts = _run_sync(monkeypatch, tmp_path, case, result=result)

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    # prefisso reale dal sorgente (L401): "validatori caso: " + decision_reason
    assert attempt["error_reason"].startswith("validatori caso: ")
    assert "ghost.py" in attempt["error_reason"]
    assert attempt["tests"]["validators"]["overall"] == "fail"
    assert attempt["tests"]["passed"] is False

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 4. gold test manomesso -> declass auto_failure (guardia anti-manomissione)
# ---------------------------------------------------------------------------

def test_runner_gold_tampered_auto_failure(monkeypatch, tmp_path):
    case = _runner_case(metadata={"gold_tests": {GOLD_NAME: GOLD_CONTENT}})

    def tamper(project_path):
        # lo scaffold "riuscito" sovrascrive il gold test con contenuto diverso
        _write(project_path / GOLD_NAME, "def test_gold_add():\n    assert True\n")
        _write(project_path / "sol.py", "def add(a, b):\n    return a + b\n")

    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py"]}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result, during_run=tamper)

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    # stringa reale dal sorgente (L395): confronto contenuto esatto vs gold_expected
    assert attempt["error_reason"] == "gold test sovrascritti dal modello: " + GOLD_NAME
    assert attempt["tests"]["gold_tampered"] == [GOLD_NAME]
    assert attempt["tests"]["gold_tests"] == [GOLD_NAME]

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 5. eccezione dello scaffold -> runner_error (infra, non verdict)
# ---------------------------------------------------------------------------

def test_runner_scaffold_exception_runner_error(monkeypatch, tmp_path):
    case = _runner_case()
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case,
        side_effect=RuntimeError("scaffold esploso"))

    attempt = attempts[0]
    assert attempt["status"] == "runner_error"
    assert attempt["error_reason"] == "scaffold esploso"
    tests = attempt["tests"]
    assert tests["infra_error"] is True
    assert tests["passed"] is False
    # runner_error NON entra in review queue (requires_teacher_validation solo auto_*)
    assert tests["requires_teacher_validation"] is False
    assert tests["quality_gate"] is None  # result ricostruito senza gate
    assert tests["validators"] == {}      # validate_case mai raggiunto

    assert job["status"] == "finished"  # il job FINISCE comunque
    assert job["completed"] == 1
    assert job["auto_success"] == 0 and job["auto_failure"] == 0
    assert job.get("runner_error", 0) == 1


# ---------------------------------------------------------------------------
# 6. crash dei validatori -> declass auto_failure (fail-closed, fix 2026-07-18)
# ---------------------------------------------------------------------------

def test_runner_validator_crash_fails_closed(monkeypatch, tmp_path):
    # Fix A (2026-07-18): un crash di validate_case non puo' restare verde.
    # Prima il runner catturava l'eccezione in {"overall": "unknown", ...} e
    # "unknown" NON declassava -> auto_success (fail-open, buco noto). Ora il
    # crash declassa ad auto_failure con "validatori in errore: <exc>" e
    # l'evidenza validators resta registrata.
    case = _runner_case()
    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py"]}

    def crash(case_, result_, project_path_):
        raise RuntimeError("validator boom")

    # import top-level nel router (L37): si patcha sul modulo router
    monkeypatch.setattr(training_router, "validate_case", crash)
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result,
        during_run=lambda p: _write(p / "sol.py", "def add(a, b):\n    return a + b\n"))

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    assert attempt["error_reason"] == "validatori in errore: validator boom"
    # evidenza preservata: il crash resta visibile nei validators registrati
    assert attempt["tests"]["validators"]["overall"] == "unknown"
    assert "validator crash: validator boom" in attempt["tests"]["validators"]["error"]
    assert attempt["tests"]["passed"] is False
    assert attempt["tests"]["requires_teacher_validation"] is True

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 7. gold check rossa (fake) -> declass auto_failure (fix B, 2026-07-18)
# ---------------------------------------------------------------------------

def test_runner_gold_not_executed_auto_failure(monkeypatch, tmp_path):
    # Gate verde + validatori ok + gold intatti, ma la verifica di esecuzione
    # dei gold riporta che NON sono stati eseguiti -> declass. Fake rossa:
    # niente pytest reale in questo pin.
    case = _runner_case(metadata={"gold_tests": {GOLD_NAME: GOLD_CONTENT}})
    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py"]}
    red_check = lambda *a, **k: {
        "executed": False, "command": "pytest", "exit_code": 5,
        "missing": [GOLD_NAME], "output": "",
        "detail": "pytest exit 5: nessun test raccolto (conftest esclude i gold?)"}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result, gold_check=red_check,
        during_run=lambda p: _write(p / "sol.py", "def add(a, b):\n    return a + b\n"))

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    assert attempt["error_reason"] == (
        "gold test non eseguiti: pytest exit 5: nessun test raccolto "
        "(conftest esclude i gold?)")
    tests = attempt["tests"]
    assert tests["passed"] is False
    assert tests["gold_executed"]["executed"] is False
    assert tests["gold_executed"]["missing"] == [GOLD_NAME]
    assert tests["gold_tampered"] == []  # byte intatti: non e' manomissione

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 8. HOLE REPRO: conftest ostile collect_ignore -> declass (pytest REALE)
# ---------------------------------------------------------------------------

def test_runner_gold_conftest_collect_ignore_auto_failure(monkeypatch, tmp_path):
    # Il buco chiuso dal fix B: il modello scrive un conftest.py con
    # collect_ignore sui gold -> pytest salta i gold -> gate verde su codice
    # mai controllato. Qui il sandbox e' reale e la verifica gira DAVVERO
    # (gold_check="real"): la raccolta da directory rispetta collect_ignore,
    # i gold spariscono dal report -> auto_failure.
    case = _runner_case(metadata={"gold_tests": {GOLD_NAME: GOLD_CONTENT}})

    def hostile(project_path):
        _write(project_path / "conftest.py",
               'collect_ignore = ["test_gold_add.py"]\n')
        _write(project_path / "sol.py", "def add(a, b):\n    return a + b\n")

    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py", "conftest.py"]}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result,
        during_run=hostile, gold_check="real")

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    assert attempt["error_reason"].startswith("gold test non eseguiti: ")
    assert "exit 5" in attempt["error_reason"]
    gold_exec = attempt["tests"]["gold_executed"]
    assert gold_exec["executed"] is False
    assert gold_exec["exit_code"] == 5
    assert gold_exec["missing"] == [GOLD_NAME]
    assert attempt["tests"]["gold_tampered"] == []  # byte gold mai toccati

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 9. variante: conftest con autouse skip -> declass (pytest REALE)
# ---------------------------------------------------------------------------

def test_runner_gold_conftest_autouse_skip_auto_failure(monkeypatch, tmp_path):
    # Variante del bypass: fixture autouse che skippa tutto. Exit code 0,
    # gold "raccolti" ma skipped -> contano come NON eseguiti -> declass.
    case = _runner_case(metadata={"gold_tests": {GOLD_NAME: GOLD_CONTENT}})

    def hostile(project_path):
        _write(project_path / "conftest.py",
               "import pytest\n"
               "@pytest.fixture(autouse=True)\n"
               "def _skip_all():\n"
               "    pytest.skip('nope')\n")
        _write(project_path / "sol.py", "def add(a, b):\n    return a + b\n")

    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py", "conftest.py"]}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result,
        during_run=hostile, gold_check="real")

    attempt = attempts[0]
    assert attempt["status"] == "auto_failure"
    assert attempt["error_reason"].startswith("gold test non eseguiti: ")
    assert GOLD_NAME in attempt["error_reason"]
    gold_exec = attempt["tests"]["gold_executed"]
    assert gold_exec["executed"] is False
    assert gold_exec["exit_code"] == 0  # pytest "verde": i gold erano skipped
    assert gold_exec["missing"] == [GOLD_NAME]

    assert job["status"] == "finished"
    assert job["auto_success"] == 0 and job["auto_failure"] == 1


# ---------------------------------------------------------------------------
# 10. sandbox pulita, verifica REALE -> resta auto_success
# ---------------------------------------------------------------------------

def test_runner_gold_real_verification_clean_auto_success(monkeypatch, tmp_path):
    # Controparte positiva end-to-end: niente conftest ostile, la verifica
    # reale raccoglie ed esegue il gold -> auto_success confermato.
    case = _runner_case(metadata={"gold_tests": {GOLD_NAME: GOLD_CONTENT}})
    result = {"success": True, "quality_gate": dict(GREEN_GATE),
              "files_written": ["sol.py"]}
    job, attempts = _run_sync(
        monkeypatch, tmp_path, case, result=result, gold_check="real",
        during_run=lambda p: _write(p / "sol.py", "def add(a, b):\n    return a + b\n"))

    attempt = attempts[0]
    assert attempt["status"] == "auto_success"
    assert attempt["error_reason"] == ""
    gold_exec = attempt["tests"]["gold_executed"]
    assert gold_exec["executed"] is True
    assert gold_exec["exit_code"] == 0
    assert gold_exec["missing"] == []

    assert job["status"] == "finished"
    assert job["auto_success"] == 1 and job["auto_failure"] == 0


# ---------------------------------------------------------------------------
# 11. skip_attempted: runner_error NON conta come "attempted" (fix 2026-07-18)
# ---------------------------------------------------------------------------

def test_skip_attempted_retries_runner_error_cases(monkeypatch, tmp_path):
    """Prima del fix, un caso il cui UNICO attempt era runner_error (infra
    giu', modello mai partito) veniva saltato da skip_attempted=true e
    restava escluso per sempre dalla resume coverage. Ora la selezione
    salta solo i casi con un esito reale (auto_*/verified/human) e ritenta
    i runner_error. Pattern FakeRequest + _training_store_for monkeypatch
    (test_understory_hybrid L732-765); thread di background finto, nessun
    run-core eseguito."""
    import asyncio

    store = TrainingStore(tmp_path / "training")
    monkeypatch.setattr(training_router, "_training_store_for",
                        lambda project_path="": store)
    case_a = store.add_case(task="Task A", title="Case A", source="bench-test")
    case_b = store.add_case(task="Task B", title="Case B", source="bench-test")
    store.add_attempt(case_id=case_a["case_id"], status="runner_error",
                      error_reason="backend giu' a meta' batch")
    store.add_attempt(case_id=case_b["case_id"], status="auto_success")

    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, **kwargs):
            started["target"] = target
            started["args"] = args

        def start(self):
            started["started"] = True

    monkeypatch.setattr(training_router.threading, "Thread", FakeThread)

    class FakeRequest:
        async def json(self):
            return {"benchmark_id": "bench-test", "skip_attempted": True}

    result = asyncio.run(training_router.api_training_run(FakeRequest()))

    assert result["status"] == "started"
    # caso A (runner_error) riselezionato, caso B (auto_success) saltato
    assert result["job"]["case_ids"] == [case_a["case_id"]]
    assert started.get("started") is True
    selected_arg = started["args"][2]  # (job_id, store, selected, benchmark_id)
    assert [c["case_id"] for c in selected_arg] == [case_a["case_id"]]

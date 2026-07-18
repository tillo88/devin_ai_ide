"""Router training: CRUD casi/attempt/review/corrections/lessons/export + jobs
+ /api/training/run e il runner background.

Terzo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

Fetta split 15 (FINALE): `/api/training/run` e `_run_training_cases_background`
rientrano qui, nel modulo che possiede il loro STATO (`_training_jobs` + lock
+ snapshot) — lo stesso stato letto dal busy check di
`api_desktop_close_cleanup` (routers/models_desktop, import diretto
router->router).

Le dipendenze del run-core RESTANO in fast_app e sono risolte con lazy import
a thread-run time dentro il runner (stesso pattern di runs_core):
`Orchestrator`, `CONFIG_PATH`, `LOG_DIR`, `_run_events`,
`_make_run_callback`, `_finish_run_events`, `active_runs`/`runs_lock`.
Cosi' i test che monkeypatchano `fast_app.*` continuano a valere.

`_training_store_for` dipende da `_validated_project_path` e `WORKSPACE_DIR`
di fast_app: import lazy dentro la funzione (mai top-level — circolo fatale
se il router e' importato per primo).
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from devin.ai.structured_contracts import TrainingReviewDecision
from devin.training.adapters import import_mbpp_cases
from devin.training.benchmarks import get_builtin_cases, list_builtin_benchmarks
from devin.training.store import (
    AUTO_FAILURE_STATUSES,
    AUTO_SUCCESS_STATUSES,
    FAILURE_STATUSES,
    SAFE_SUCCESS_STATUSES,
    TrainingStore,
)
from devin.training.validators import decision_reason, validate_case

router = APIRouter()


def _training_store_for(project_path: str = "") -> TrainingStore:
    from devin.ui.fast_app import WORKSPACE_DIR, _validated_project_path  # lazy
    if project_path:
        safe = _validated_project_path(project_path, allow_general=False)
        return TrainingStore(Path(safe) / ".devin" / "training")
    return TrainingStore(WORKSPACE_DIR / "_training")


_training_jobs: Dict[str, Dict[str, Any]] = {}
_training_jobs_lock = threading.Lock()


def _training_safe_slug(value: str, fallback: str = "case") -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or ""))
    text = "_".join(part for part in text.split("_") if part)
    return (text[:48] or fallback).strip("_") or fallback


def _training_case_prompt(case: Dict[str, Any]) -> str:
    expected = case.get("expected_signals") or []
    expected_text = ", ".join(expected) if expected else "crea output verificabile"
    return (
        f"TRAINING/EVAL CASE: {case.get('title') or case.get('case_id')}\n\n"
        f"Task: {case.get('task') or case.get('prompt') or ''}\n\n"
        f"Criteri attesi: {expected_text}.\n\n"
        "Esegui in modo operativo su file reali nel progetto sandbox assegnato. "
        "Mantieni il piano breve, non fare ragionamento lungo, non inventare fonti o endpoint. "
        "Crea test o mock ripetibili quando possibile. Output finale: cosa hai creato e come verificarlo."
    )


def _training_run_project_path(benchmark_id: str, case: Dict[str, Any], run_id: str) -> Path:
    from devin.ui.fast_app import WORKSPACE_DIR  # lazy: no import circolare
    bench_slug = _training_safe_slug(benchmark_id or case.get("source") or "bench", "bench")
    case_slug = _training_safe_slug(case.get("title") or case.get("case_id") or "case", "case")
    return WORKSPACE_DIR / "_training_runs" / bench_slug / f"{case_slug}_{run_id}"


def _verify_gold_tests_executed(project_path: Path, gold_names: List[str], timeout: int = 180) -> Dict[str, Any]:
    """Verifica che i gold test siano stati DAVVERO raccolti, eseguiti e passati.

    Guardia anti-bypass conftest (2026-07-18, fix B): la guardia
    anti-manomissione confronta solo i BYTE dei gold file — un conftest.py
    scritto dal modello con `collect_ignore`, hook di collezione o fixture
    autouse di skip esclude i gold test dal run di pytest -> gate verde su
    codice mai controllato. Qui si RIGIRA pytest sul sandbox intero (stessa
    igiene di `_run_pytest_gate`: sys.executable, cwd=sandbox,
    PYTHONDONTWRITEBYTECODE, timeout, niente cacheprovider) con report
    junitxml e si esige che OGNI gold file abbia almeno un testcase passato
    (no failure/error/skipped) nel report.

    Nota: i gold file NON vanno passati esplicitamente a pytest — un path
    esplicito bypassa `collect_ignore` e il buco resterebbe aperto. Serve la
    raccolta da directory, quella che il conftest ostile sabota.

    Ritorna evidenza {"executed", "command", "exit_code", "missing",
    "detail", "output"}; executed=True solo a verifica riuscita.
    """
    evidence: Dict[str, Any] = {
        "executed": False, "command": "pytest", "exit_code": None,
        "missing": list(gold_names), "detail": "", "output": "",
    }
    names = [str(n) for n in gold_names if str(n).endswith(".py")]
    if not names:
        evidence.update(executed=True, missing=[], detail="nessun gold test da verificare")
        return evidence
    xml_fd, xml_name = tempfile.mkstemp(prefix="gold_verify_", suffix=".xml")
    os.close(xml_fd)
    xml_path = Path(xml_name)
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--maxfail=20", f"--junitxml={xml_path}"]
    # PYTHONDONTWRITEBYTECODE: come nel gate, niente .pyc stale tra i rerun.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        try:
            proc = subprocess.run(argv, cwd=str(project_path), capture_output=True,
                                  text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            evidence["detail"] = f"timeout dopo {timeout}s"
            return evidence
        except Exception as exc:
            evidence["detail"] = f"{type(exc).__name__}: {exc}"
            return evidence
        evidence["exit_code"] = proc.returncode
        evidence["output"] = (((proc.stdout or "") + "\n" + (proc.stderr or "")).strip())[-1000:]
        passed_gold: set = set()
        try:
            for tc in ET.parse(str(xml_path)).iter("testcase"):
                if any(tc.find(tag) is not None for tag in ("failure", "error", "skipped")):
                    continue
                classname = str(tc.get("classname") or "")
                for name in names:
                    stem = Path(name).stem
                    if classname == stem or classname.startswith(stem + "."):
                        passed_gold.add(name)
        except Exception as exc:
            evidence["detail"] = f"report junit illeggibile: {exc}"
            return evidence
    finally:
        try:
            xml_path.unlink()
        except OSError:
            pass
    missing = [n for n in names if n not in passed_gold]
    evidence["missing"] = missing
    if proc.returncode == 5:
        # exit 5 = no tests collected: la firma classica del conftest bypass
        evidence["detail"] = "pytest exit 5: nessun test raccolto (conftest esclude i gold?)"
    elif missing:
        evidence["detail"] = "non raccolti/passati: " + ", ".join(missing)
    elif proc.returncode != 0:
        evidence["detail"] = f"pytest exit {proc.returncode} a gold verdi (suite instabile)"
    else:
        evidence["executed"] = True
        evidence["detail"] = f"{len(passed_gold)} gold file eseguiti e passati"
    return evidence


def _training_job_snapshot(job_id: str = "") -> List[Dict[str, Any]] | Dict[str, Any]:
    with _training_jobs_lock:
        if job_id:
            return dict(_training_jobs.get(job_id, {}))
        return [dict(item) for item in _training_jobs.values()]


@router.get("/api/training/overview")
async def api_training_overview(project_path: str = ""):
    store = _training_store_for(project_path)
    # FIX dropdown (2026-07-15): la UI ricostruisce la select dai benchmarks
    # di questo payload — solo i builtin non bastano, le source IMPORTATE
    # (es. mbpp) devono comparire qui o il run non e' selezionabile.
    builtin = list_builtin_benchmarks()
    known_ids = {item.get("id") for item in builtin}
    imported_sources = sorted({c.get("source") for c in store.list_cases(limit=10000)
                               if c.get("source") and c.get("source") not in known_ids})
    benchmarks = builtin + [{"id": s, "name": f"{s} (importati)", "source": "imported"}
                            for s in imported_sources]
    return {
        "summary": store.summary(),
        "cases": store.list_cases(limit=50),
        "attempts": store.list_attempts(limit=50),
        "corrections": store.list_corrections(limit=30),
        "reviews": store.list_reviews(limit=50),
        "latest_reviews": store.latest_reviews_by_attempt(limit=10000),
        "review_queue": store.review_queue(limit=30),
        "lessons": store.list_lessons(limit=30),
        "benchmarks": benchmarks,
        "jobs": _training_job_snapshot(),
        "memory_policy": {
            "auto_promote": False,
            "success_statuses": ["verified_success", "human_confirmed"],
            "failure_statuses": ["verified_failure", "failed", "needs_correction"],
            "auto_statuses": ["auto_success", "auto_failure"],
            "infra_statuses": ["runner_error"],
        },
    }


@router.post("/api/training/adapters/mbpp/import")
async def api_training_adapter_mbpp(request: Request):
    """Import ESPLICITO di casi MBPP (mai automatico: arriva solo dal bottone
    con conferma in Diagnostics o da una chiamata deliberata). Il download
    (~5MB, una volta) va nella cache training; i casi creati hanno i gold
    test derivati dagli assert ufficiali."""
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    try:
        result = await asyncio.to_thread(
            import_mbpp_cases,
            store,
            # cap = intero dataset MBPP (~974): l'import crea solo i casi
            # (economico), e' il RUN a costare tempo ed e' gia' gestito dal
            # resume. Prima il tetto era 200 e "limitava" a sorpresa.
            limit=max(1, min(int(data.get("limit", 10) or 10), 1000)),
            offset=max(0, int(data.get("offset", 0) or 0)),
            force_download=bool(data.get("force_download", False)),
        )
    except Exception as exc:
        return {"error": f"import MBPP fallito: {exc}"}
    return result


@router.get("/api/training/review_queue")
async def api_training_review_queue(project_path: str = "", limit: int = 50):
    """Coda Teacher: attempt auto_*/runner_error/pending_review senza review,
    con evidenze (quality gate + validatori) gia' allegate. Read-only."""
    store = _training_store_for(project_path)
    queue = store.review_queue(limit=max(1, min(int(limit or 50), 200)))
    return {"queue": queue, "count": len(queue)}


@router.post("/api/training/cases")
async def api_training_cases_add(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    try:
        case = store.add_case(
            task=data.get("task", ""),
            title=data.get("title", ""),
            kind=data.get("kind", "custom"),
            tags=data.get("tags", []),
            source=data.get("source", "manual"),
            expected_signals=data.get("expected_signals", []),
            metadata=data.get("metadata", {}),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"case": case}


@router.post("/api/training/seed")
async def api_training_seed(request: Request):
    data = await request.json()
    benchmark_id = data.get("benchmark_id", "devin-mini")
    store = _training_store_for(data.get("project_path", ""))
    cases = get_builtin_cases(benchmark_id)
    created = store.seed_cases(cases, source=benchmark_id)
    return {"created": created, "count": len(created), "benchmark_id": benchmark_id}


@router.get("/api/training/jobs")
async def api_training_jobs(job_id: str = ""):
    return {"jobs": _training_job_snapshot() if not job_id else [], "job": _training_job_snapshot(job_id) if job_id else None}


@router.post("/api/training/attempts")
async def api_training_attempts_add(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    try:
        attempt = store.add_attempt(
            case_id=data.get("case_id", "manual"),
            prompt=data.get("prompt", ""),
            response=data.get("response", ""),
            status=data.get("status", "pending_review"),
            tests=data.get("tests", {}),
            error_reason=data.get("error_reason", ""),
            run_id=data.get("run_id", ""),
            artifacts=data.get("artifacts", []),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"attempt": attempt}


@router.post("/api/training/reviews")
async def api_training_reviews_add(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    try:
        review = store.add_review(
            attempt_id=data.get("attempt_id", ""),
            status=data.get("status", "pending_review"),
            rationale=data.get("rationale", ""),
            reviewer=data.get("reviewer", "human"),
            confidence=data.get("confidence", 1.0),
            tags=data.get("tags", []),
            evidence=data.get("evidence", {}),
            method_trace=data.get("method_trace", ""),
            failure_mode=data.get("failure_mode", ""),
            next_action=data.get("next_action", ""),
            lesson_candidate=data.get("lesson_candidate", ""),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"review": review}


@router.post("/api/training/reviews/structured")
async def api_training_reviews_structured(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    reviewer = data.get("reviewer", "teacher")
    payload = data.get("decision") if isinstance(data.get("decision"), dict) else data
    try:
        decision = TrainingReviewDecision.model_validate(payload)
        review = store.add_review(**decision.to_store_payload(reviewer=reviewer))
    except Exception as exc:
        return {"error": str(exc)}
    return {"review": review, "validated": True, "schema": "TrainingReviewDecision"}


@router.post("/api/training/corrections")
async def api_training_corrections_add(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    try:
        correction = store.add_correction(
            attempt_id=data.get("attempt_id", ""),
            correction=data.get("correction", ""),
            corrected_solution=data.get("corrected_solution", ""),
            reviewer=data.get("reviewer", "human"),
            tags=data.get("tags", []),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"correction": correction}


@router.post("/api/training/lessons")
async def api_training_lessons_add(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    try:
        lesson = store.add_lesson(
            content=data.get("content", ""),
            status=data.get("status", "pending_review"),
            tags=data.get("tags", []),
            evidence=data.get("evidence", {}),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"lesson": lesson}


@router.post("/api/training/export")
async def api_training_export(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    return store.export_sft_dataset(data.get("filename", ""))


@router.get("/api/training/exports")
async def api_training_exports(project_path: str = ""):
    store = _training_store_for(project_path)
    return {"exports": store.list_exports(limit=50)}


@router.post("/api/training/export_teacher_packet")
async def api_training_export_teacher_packet(request: Request):
    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    return store.export_teacher_packet(data.get("filename", ""))


# ============================================================
# /api/training/run + runner background (fetta split 15, 2026-07-18)
# ============================================================
# Il runner rientra nel modulo che possiede il suo stato (_training_jobs +
# lock, vedi header). Le dipendenze del run-core restano in fast_app e sono
# risolte con lazy import a thread-run time (pattern runs_core: i monkeypatch
# dei test su fast_app.Orchestrator/LOG_DIR/_run_events restano validi).


def _run_training_cases_background(job_id: str, store: TrainingStore, cases: List[Dict[str, Any]], benchmark_id: str) -> None:
    from devin.ui.fast_app import (  # lazy: risolti a thread-run time
        CONFIG_PATH,
        LOG_DIR,
        Orchestrator,
        _finish_run_events,
        _make_run_callback,
        _run_events,
        active_runs,
        runs_lock,
    )
    with _training_jobs_lock:
        job = _training_jobs.get(job_id)
        if job:
            job["status"] = "running"
            job["started_at"] = datetime.now().isoformat(timespec="seconds")

    for index, case in enumerate(cases, start=1):
        case_id = case.get("case_id") or f"case_{index}"
        run_id = datetime.now().strftime("train_%Y%m%d_%H%M%S_%f")
        project_path = _training_run_project_path(benchmark_id, case, run_id)
        project_path.mkdir(parents=True, exist_ok=True)

        # GOLD TESTS (2026-07-15): test canonici NOSTRI iniettati nel sandbox
        # PRIMA dello scaffold; il quality gate li scopre (test_*.py) e li
        # esegue insieme a quelli del modello. Senza gold il modello si
        # corregge i compiti da solo (test deboli => gate verde su codice
        # sbagliato). Il contenuto atteso resta in gold_expected per la
        # guardia anti-manomissione dopo il run.
        gold_expected: Dict[str, str] = {}
        for fname, content in ((case.get("metadata") or {}).get("gold_tests") or {}).items():
            safe_name = Path(str(fname)).name
            if not safe_name.endswith(".py") or not str(content).strip():
                continue
            (project_path / safe_name).write_text(str(content), encoding="utf-8")
            gold_expected[safe_name] = str(content)

        prompt = _training_case_prompt(case)
        log_path = LOG_DIR / f"{run_id}.log"
        log_path.write_text(
            f"Training eval started: {run_id}\n"
            f"Job: {job_id}\nCase: {case_id}\nTask: {case.get('title') or ''}\n",
            encoding="utf-8",
        )
        _run_events.start(run_id, mode="training", task=case.get("title") or prompt, project_path=str(project_path))

        with _training_jobs_lock:
            job = _training_jobs.get(job_id)
            if job:
                job["current_case_id"] = case_id
                job["current_title"] = case.get("title") or case_id
                job["completed"] = index - 1

        status = "auto_failure"
        result: Dict[str, Any] = {}
        error_reason = ""
        validation: Dict[str, Any] = {}
        validator_crash = ""
        gold_check: Dict[str, Any] = {}
        gold_tampered: List[str] = []
        try:
            with Orchestrator(config_path=CONFIG_PATH, project_path=str(project_path), sse_callback=_make_run_callback(run_id, log_path)) as orch:
                with runs_lock:
                    active_runs[run_id] = orch
                try:
                    result = orch.run_scaffold(task=prompt, project_path=str(project_path), run_id=run_id)
                finally:
                    with runs_lock:
                        active_runs.pop(run_id, None)
            status = "auto_success" if result.get("success") else "auto_failure"
            error_reason = "" if result.get("success") else str(result.get("error") or "training scaffold failed")

            # QUALITY GATE STRETTO (2026-07-15, fix del falso "ok 3"): lo
            # scaffold "riuscito" non basta. Il verdetto automatico puo' solo
            # essere DECLASSATO qui (mai promosso): (a) gate tecnico fallito
            # (sintassi/test rossi) -> auto_failure; (b) validatori semantici
            # del caso violati (es. endpoint fuori allowlist) -> auto_failure.
            try:
                validation = validate_case(case, result, str(project_path))
            except Exception as val_exc:
                # FAIL-CLOSED (2026-07-18, fix A): un crash dei validatori non
                # puo' restare "unknown" verde — senza verdict semantico
                # l'attempt DEVE essere declassato (vedi catena sotto).
                validation = {"overall": "unknown", "error": f"validator crash: {val_exc}"}
                validator_crash = str(val_exc)
            gate = result.get("quality_gate") or {}
            # Guardia anti-manomissione: se lo scaffold ha sovrascritto un gold
            # test, il gate ha girato su test corrotti -> mai auto_success.
            for safe_name, expected in gold_expected.items():
                try:
                    current = (project_path / safe_name).read_text(encoding="utf-8")
                except OSError:
                    current = ""
                if current != expected:
                    gold_tampered.append(safe_name)
            if status == "auto_success":
                if gold_tampered:
                    status = "auto_failure"
                    error_reason = "gold test sovrascritti dal modello: " + ", ".join(gold_tampered)
                elif validator_crash:
                    status = "auto_failure"
                    error_reason = "validatori in errore: " + validator_crash[:600]
                elif gate.get("status") == "verified_failure":
                    status = "auto_failure"
                    error_reason = "quality gate: " + "; ".join(gate.get("errors") or [])[:800]
                elif validation.get("overall") == "fail":
                    status = "auto_failure"
                    error_reason = "validatori caso: " + decision_reason(validation)
                elif gold_expected:
                    # GUARDIA ESECUZIONE GOLD (2026-07-18, fix B): byte intatti
                    # non bastano — un conftest ostile puo' escludere i gold
                    # dalla raccolta pytest (collect_ignore, autouse skip).
                    # Verifica bounded che i gold siano stati DAVVERO eseguiti
                    # e passati prima di concedere auto_success.
                    gold_check = _verify_gold_tests_executed(
                        project_path, sorted(gold_expected.keys()))
                    if not gold_check.get("executed"):
                        status = "auto_failure"
                        error_reason = ("gold test non eseguiti: "
                                        + str(gold_check.get("detail") or "verifica fallita"))[:800]

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\ntraining_status: {status}\n")
                f.write(f"validators: {validation.get('overall', 'n/a')}\n")
                f.write(f"status: {'success' if status == 'auto_success' else 'failed'}\n")
            _finish_run_events(run_id, "success" if status == "auto_success" else "failed", mode="training")
        except Exception as exc:
            status = "runner_error"
            error_reason = str(exc)
            result = {"success": False, "error": error_reason, "infra_error": True}
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[FATAL] {error_reason}\ntraining_status: runner_error\nstatus: failed\n")
            _finish_run_events(run_id, "failed", mode="training")

        attempt = store.add_attempt(
            case_id=case_id,
            prompt=prompt,
            response=json.dumps(result, ensure_ascii=False, sort_keys=True)[:5000],
            status=status,
            tests={
                "source": "devin_training_runner",
                "passed": status == "auto_success",
                "requires_teacher_validation": status in {"auto_success", "auto_failure"},
                "infra_error": status == "runner_error",
                "quality_gate": result.get("quality_gate"),
                "validators": validation,
                "gold_tests": sorted(gold_expected.keys()),
                "gold_tampered": gold_tampered,
                "gold_executed": gold_check,
            },
            error_reason=error_reason,
            run_id=run_id,
            artifacts=[str(project_path), str(log_path)],
        )

        with _training_jobs_lock:
            job = _training_jobs.get(job_id)
            if job:
                job["completed"] = index
                job.setdefault("attempt_ids", []).append(attempt.get("attempt_id"))
                job.setdefault("run_ids", []).append(run_id)
                job["auto_success"] = int(job.get("auto_success", 0)) + (1 if status == "auto_success" else 0)
                job["auto_failure"] = int(job.get("auto_failure", 0)) + (1 if status == "auto_failure" else 0)
                job["runner_error"] = int(job.get("runner_error", 0)) + (1 if status == "runner_error" else 0)

    with _training_jobs_lock:
        job = _training_jobs.get(job_id)
        if job:
            job["status"] = "finished"
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            job["current_case_id"] = ""
            job["current_title"] = ""


@router.post("/api/training/run")
async def api_training_run(request: Request):
    data = await request.json()
    benchmark_id = data.get("benchmark_id", "devin-mini")
    case_id = data.get("case_id", "")
    store = _training_store_for(data.get("project_path", ""))
    cases = store.list_cases(limit=1000)
    selected = [item for item in cases if item.get("source") == benchmark_id]
    if case_id:
        selected = [item for item in selected if item.get("case_id") == case_id]
    if not selected:
        builtin = get_builtin_cases(benchmark_id)
        if builtin:
            store.seed_cases(builtin, source=benchmark_id)
            cases = store.list_cases(limit=1000)
            selected = [item for item in cases if item.get("source") == benchmark_id]
            if case_id:
                selected = [item for item in selected if item.get("case_id") == case_id]
    if not selected:
        return {"error": "nessun caso training disponibile: premi prima Seed mini bench"}

    # RESUME per batch lunghi (2026-07-15): se un run da N casi muore a meta'
    # (backend giu', riavvio), skip_attempted=true riparte dai casi SENZA
    # attempt invece di rigirare tutto da capo.
    # FIX (2026-07-18): runner_error NON conta come "attempted" — e' un
    # fallimento di INFRASTRUTTURA (il modello non ha mai girato), quindi il
    # caso deve essere ritentato, non escluso per sempre dalla resume
    # coverage. Contano solo gli esiti reali: auto_* e gli status
    # verified/human (canonical sets dello store).
    if bool(data.get("skip_attempted", False)):
        attempted_statuses = (AUTO_SUCCESS_STATUSES | AUTO_FAILURE_STATUSES
                              | SAFE_SUCCESS_STATUSES | FAILURE_STATUSES)
        attempted = {a.get("case_id") for a in store.list_attempts(limit=10000)
                     if (a.get("status") or "") in attempted_statuses}
        selected = [c for c in selected if c.get("case_id") not in attempted]
        if not selected:
            return {"error": "tutti i casi di questa source hanno gia' un attempt (skip_attempted attivo): niente da riprendere"}

    job_id = datetime.now().strftime("training_%Y%m%d_%H%M%S_%f")
    job = {
        "job_id": job_id,
        "benchmark_id": benchmark_id,
        "status": "queued",
        "total": len(selected),
        "completed": 0,
        "auto_success": 0,
        "auto_failure": 0,
        "case_ids": [item.get("case_id") for item in selected],
        "attempt_ids": [],
        "run_ids": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "requires_teacher_validation": True,
    }
    with _training_jobs_lock:
        _training_jobs[job_id] = job

    thread = threading.Thread(
        target=_run_training_cases_background,
        args=(job_id, store, selected, benchmark_id),
        daemon=True,
    )
    thread.start()
    return {"job": job, "status": "started"}

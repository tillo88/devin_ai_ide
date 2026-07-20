"""Coverage finale training: CRUD endpoints + export paths dello store.

Slice di chiusura (2026-07-18): gli endpoint CRUD di
`devin/ui/routers/training.py` (cases/seed/attempts/reviews/corrections/
lessons/export/jobs/overview) e gli export di `devin/training/store.py`
(`export_teacher_packet`, `export_sft_dataset`, `list_exports`) erano gli
unici pezzi del sottosistema training senza pin (`/api/training/run` e
`/api/training/reviews/structured` gia' coperti).

Pattern riusati:
- store-level: `TrainingStore(tmp_path)` diretto (test_training_runner_declass).
- endpoint-level: FakeRequest + chiamata async diretta all'handler con
  `_training_store_for` monkeypatchato sul ROUTER (nome risolto a call-time,
  test_understory_hybrid L732-765). Niente TestClient: il router non ha un
  pattern TestClient consolidato nei test esistenti.

Il teacher packet e' il contratto anti-contaminazione mostrato al Teacher
esterno: schema pinnato preciso (packet_version, promotion_policy con
auto_promote False, liste di status recall-safe esatte, campi evidenza).
Nessuna sorgente toccata.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

import devin.ui.routers.training as training_router
from devin.training.store import (
    AUTO_FAILURE_STATUSES,
    AUTO_SUCCESS_STATUSES,
    FAILURE_STATUSES,
    INFRA_STATUSES,
    SAFE_SUCCESS_STATUSES,
    TrainingStore,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_training_jobs():
    """Isola `_training_jobs` (stato globale del router) tra i test
    (stesso fixture di test_training_runner_declass)."""
    with training_router._training_jobs_lock:
        saved = dict(training_router._training_jobs)
        training_router._training_jobs.clear()
    yield
    with training_router._training_jobs_lock:
        training_router._training_jobs.clear()
        training_router._training_jobs.update(saved)


def _fake_request(payload):
    class FakeRequest:
        async def json(self):
            return payload
    return FakeRequest()


def _patched_store(monkeypatch, tmp_path):
    """Store su tmp_path + monkeypatch del lookup sul modulo router."""
    store = TrainingStore(tmp_path / "training")
    monkeypatch.setattr(training_router, "_training_store_for",
                        lambda project_path="": store)
    return store


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---------------------------------------------------------------------------
# 1. add_case: validazione + retire_case tombstone
# ---------------------------------------------------------------------------

def test_add_case_requires_task(tmp_path):
    store = TrainingStore(tmp_path)
    with pytest.raises(ValueError, match="training case task is required"):
        store.add_case(task="")
    with pytest.raises(ValueError, match="training case task is required"):
        store.add_case(task="   ")
    assert store.list_cases() == []  # niente record scritto
    assert not store.cases_file.exists()


def test_add_case_defaults_and_retire_tombstone(tmp_path):
    store = TrainingStore(tmp_path)
    case = store.add_case(task="Scrivi add() e verificala", title="Add case")
    assert case["case_id"].startswith("case_")
    assert case["status"] == "active"
    assert case["kind"] == "custom" and case["source"] == "manual"
    assert case["tags"] == [] and case["expected_signals"] == []

    store.retire_case(case["case_id"], reason="superseded by reseed")
    # il caso esce dal listing attivo ma resta nello storico (tombstone)
    assert store.list_cases() == []
    retired = store.list_cases(include_retired=True)
    assert [c["case_id"] for c in retired] == [case["case_id"]]
    records = _read_jsonl(store.cases_file)
    tombstones = [r for r in records if r.get("op") == "retire"]
    assert len(tombstones) == 1
    assert tombstones[0]["case_id"] == case["case_id"]
    assert tombstones[0]["reason"] == "superseded by reseed"
    assert tombstones[0]["created_at"]

    # no-op difensivi: case_id vuoto/sconosciuto non scrive nulla di nuovo
    store.retire_case("")
    store.retire_case("case_ghost", reason="ghost")
    assert store.list_cases() == []
    assert [c["case_id"] for c in store.list_cases(include_retired=True)] == [case["case_id"]]


# ---------------------------------------------------------------------------
# 2. add_review: status invalido / attempt sconosciuto
# ---------------------------------------------------------------------------

def test_add_review_rejects_invalid_status_and_unknown_attempt(tmp_path):
    store = TrainingStore(tmp_path)
    case = store.add_case(task="Task")
    attempt = store.add_attempt(case_id=case["case_id"], status="auto_failure")

    with pytest.raises(ValueError, match="known attempt_id is required"):
        store.add_review(attempt_id="attempt_ghost", status="verified_failure")
    with pytest.raises(ValueError, match="known attempt_id is required"):
        store.add_review(attempt_id="", status="verified_failure")
    with pytest.raises(ValueError, match="unsupported review status"):
        store.add_review(attempt_id=attempt["attempt_id"], status="auto_succes")
    with pytest.raises(ValueError, match="unsupported review status"):
        store.add_review(attempt_id=attempt["attempt_id"], status="")
    assert store.list_reviews() == []  # niente record scritto


# ---------------------------------------------------------------------------
# 3. add_correction / add_lesson: validazione + happy path;
#    latest_reviews_by_attempt
# ---------------------------------------------------------------------------

def test_add_correction_validation_and_happy_path(tmp_path):
    store = TrainingStore(tmp_path)
    with pytest.raises(ValueError, match="attempt_id and correction are required"):
        store.add_correction(attempt_id="", correction="fix")
    with pytest.raises(ValueError, match="attempt_id and correction are required"):
        store.add_correction(attempt_id="attempt_x", correction="")
    with pytest.raises(ValueError, match="attempt_id and correction are required"):
        store.add_correction(attempt_id="attempt_x", correction="   ")
    assert store.list_corrections() == []

    item = store.add_correction(
        attempt_id="attempt_x", correction="usa add() non sub()",
        corrected_solution="def add(a, b):\n    return a + b\n",
        reviewer="colibri", tags=["math"])
    assert item["correction_id"].startswith("correction_")
    assert item["reviewer"] == "colibri"
    assert item["tags"] == ["math"]
    assert store.list_corrections() == [item]


def test_add_lesson_validation_and_promotion_flag(tmp_path):
    store = TrainingStore(tmp_path)
    with pytest.raises(ValueError, match="lesson content is required"):
        store.add_lesson(content="")
    with pytest.raises(ValueError, match="lesson content is required"):
        store.add_lesson(content="   ")
    assert store.list_lessons() == []

    pending = store.add_lesson(content="Mai inventare endpoint")
    assert pending["lesson_id"].startswith("lesson_")
    assert pending["status"] == "pending_review"
    assert pending["promotion"] == "manual_required"

    safe = store.add_lesson(content="Lezione verificata", status="verified_success")
    assert safe["promotion"] == "eligible"
    assert store.list_lessons() == [pending, safe]


def test_latest_reviews_by_attempt_returns_latest_per_attempt(tmp_path):
    store = TrainingStore(tmp_path)
    case = store.add_case(task="Task")
    att_a = store.add_attempt(case_id=case["case_id"], status="auto_success")
    att_b = store.add_attempt(case_id=case["case_id"], status="auto_failure")
    first = store.add_review(attempt_id=att_a["attempt_id"], status="pending_review",
                             rationale="presa in carico")
    last = store.add_review(attempt_id=att_a["attempt_id"], status="verified_success",
                            rationale="verdetto finale")
    only_b = store.add_review(attempt_id=att_b["attempt_id"], status="needs_correction")

    latest = store.latest_reviews_by_attempt()
    assert set(latest) == {att_a["attempt_id"], att_b["attempt_id"]}
    assert latest[att_a["attempt_id"]]["review_id"] == last["review_id"]
    assert latest[att_a["attempt_id"]]["status"] == "verified_success"
    assert latest[att_b["attempt_id"]]["review_id"] == only_b["review_id"]
    assert first["review_id"] not in {r["review_id"] for r in latest.values()}


# ---------------------------------------------------------------------------
# 4. summary() su store misto
# ---------------------------------------------------------------------------

def test_summary_counters_mixed_store(tmp_path):
    store = TrainingStore(tmp_path)
    case = store.add_case(task="Task")
    cid = case["case_id"]
    statuses = ["verified_success", "auto_success", "auto_failure",
                "runner_error", "failed", "pending_review"]
    attempts = [store.add_attempt(case_id=cid, status=s) for s in statuses]
    store.add_review(attempt_id=attempts[0]["attempt_id"], status="human_confirmed")
    store.add_review(attempt_id=attempts[1]["attempt_id"], status="verified_failure")
    store.add_review(attempt_id=attempts[3]["attempt_id"], status="runner_error")
    store.add_correction(attempt_id=attempts[2]["attempt_id"], correction="fix")
    store.add_lesson(content="lesson")

    summary = store.summary()
    assert summary["path"] == str(store.base_dir)
    assert summary["cases"] == 1
    assert summary["attempts"] == 6
    assert summary["corrections"] == 1
    assert summary["reviews"] == 3
    assert summary["lessons"] == 1
    assert summary["verified_success"] == 1      # SAFE_SUCCESS_STATUSES
    assert summary["verified_failure"] == 1      # FAILURE_STATUSES ("failed")
    assert summary["auto_success"] == 1
    assert summary["auto_failure"] == 1
    assert summary["runner_error"] == 1
    assert summary["review_verified_success"] == 1   # human_confirmed
    assert summary["review_verified_failure"] == 1
    assert summary["review_runner_error"] == 1
    # pending_review NON e' conteggiato in nessun contatore di esito
    assert (summary["verified_success"] + summary["verified_failure"]
            + summary["auto_success"] + summary["auto_failure"]
            + summary["runner_error"]) == 5
    assert summary["last_attempt_at"] == attempts[-1]["created_at"]
    assert summary["last_review_at"] is not None


def test_summary_empty_store_nulls(tmp_path):
    summary = TrainingStore(tmp_path).summary()
    assert summary["cases"] == 0 and summary["attempts"] == 0
    assert summary["last_attempt_at"] is None
    assert summary["last_review_at"] is None


# ---------------------------------------------------------------------------
# 5. export_teacher_packet: contratto anti-contaminazione (schema pin)
# ---------------------------------------------------------------------------

def _store_with_full_chain(tmp_path):
    """Case + attempt + review + correction: la catena minima del packet."""
    store = TrainingStore(tmp_path)
    case = store.add_case(task="Scrivi add()", title="Add", source="bench-test",
                          tags=["python"], expected_signals=["file_created"],
                          metadata={"gold_tests": {"test_gold.py": "..."}})
    attempt = store.add_attempt(
        case_id=case["case_id"], prompt="PROMPT", response="RESP",
        status="auto_failure", error_reason="quality gate: red",
        run_id="train_x", tests={"quality_gate": {"status": "verified_failure"}},
        artifacts=["/sandbox"])
    review = store.add_review(attempt_id=attempt["attempt_id"],
                              status="needs_correction", rationale="red gate",
                              reviewer="teacher")
    correction = store.add_correction(attempt_id=attempt["attempt_id"],
                                      correction="usa + non -",
                                      corrected_solution="def add(a,b): return a+b")
    return store, case, attempt, review, correction


def test_export_teacher_packet_schema_and_promotion_policy(tmp_path):
    store, case, attempt, review, correction = _store_with_full_chain(tmp_path)

    result = store.export_teacher_packet("packet_test.jsonl")
    assert result["format"] == "teacher_review_v1"
    assert result["rows"] == 1
    target = Path(result["path"])
    assert target.parent == store.exports_dir
    assert target.name == "packet_test.jsonl"  # filename sanificato a .name

    rows = _read_jsonl(target)
    assert len(rows) == 1
    row = rows[0]

    # contratto versionato: schema drift qui corromperebbe la review esterna
    assert row["packet_version"] == "teacher_review_v1"
    assert "Classify this DEVIN attempt" in row["review_task"]

    policy = row["promotion_policy"]
    assert policy["auto_promote"] is False
    # liste di status ESATTE (contratto recall-safe mostrato al Teacher)
    assert policy["recall_safe_only"] == ["human_confirmed", "verified_success"]
    assert policy["review_only"] == ["auto_failure", "auto_success", "failed",
                                     "needs_correction", "runner_error",
                                     "verified_failure"]
    # coerenza con i set canonici dello store
    assert policy["recall_safe_only"] == sorted(SAFE_SUCCESS_STATUSES)
    assert policy["review_only"] == sorted(
        FAILURE_STATUSES | AUTO_SUCCESS_STATUSES | AUTO_FAILURE_STATUSES | INFRA_STATUSES)

    packet_case = row["case"]
    assert packet_case["case_id"] == case["case_id"]
    assert packet_case["benchmark_id"] == "bench-test"
    assert packet_case["title"] == "Add"
    assert packet_case["task"] == "Scrivi add()"
    assert packet_case["tags"] == ["python"]
    assert packet_case["expected_signals"] == ["file_created"]
    assert packet_case["metadata"]["gold_tests"] == {"test_gold.py": "..."}

    packet_attempt = row["attempt"]
    assert packet_attempt["attempt_id"] == attempt["attempt_id"]
    assert packet_attempt["run_id"] == "train_x"
    assert packet_attempt["status"] == "auto_failure"
    assert packet_attempt["prompt"] == "PROMPT"
    assert packet_attempt["response"] == "RESP"
    assert packet_attempt["error_reason"] == "quality gate: red"
    assert packet_attempt["artifacts"] == ["/sandbox"]
    assert packet_attempt["tests"]["quality_gate"]["status"] == "verified_failure"
    # auto_failure richiede validazione Teacher (mai auto-promote)
    assert packet_attempt["requires_teacher_validation"] is True

    assert [r["review_id"] for r in row["known_reviews"]] == [review["review_id"]]
    assert [c["correction_id"] for c in row["known_corrections"]] == [correction["correction_id"]]


def test_export_teacher_packet_default_filename_and_empty_store(tmp_path):
    store = TrainingStore(tmp_path)
    result = store.export_teacher_packet()
    assert result["rows"] == 0
    assert Path(result["path"]).name.startswith("devin_teacher_packet_")
    assert Path(result["path"]).suffix == ".jsonl"
    assert _read_jsonl(result["path"]) == []


# ---------------------------------------------------------------------------
# 6. export_sft_dataset: shape messages + list_exports
# ---------------------------------------------------------------------------

def test_export_sft_dataset_messages_shape(tmp_path):
    store, case, attempt, _review, _corr = _store_with_full_chain(tmp_path)
    # correzione SENZA corrected_solution: ricade sul testo della correction
    store.add_correction(attempt_id=attempt["attempt_id"], correction="fallback fix")
    # correzione su attempt sconosciuto: case/attempt risolvono a {} ->
    # user fallback "Fix the previous attempt." (comportamento attuale, pinnato)
    store.add_correction(attempt_id="attempt_ghost", correction="orphan fix")

    result = store.export_sft_dataset("sft_test.jsonl")
    assert result["rows"] == 3
    rows = _read_jsonl(result["path"])
    by_corr = {r["metadata"]["correction_id"]: r for r in rows}

    first = rows[0]
    messages = first["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant"]
    assert messages[0]["content"] == (
        "You are DEVIN, a local coding agent. Produce verified, testable coding work.")
    assert messages[1]["content"] == "Scrivi add()"  # task del caso
    assert messages[2]["content"] == "def add(a,b): return a+b"  # corrected_solution
    meta = first["metadata"]
    assert meta["case_id"] == case["case_id"]
    assert meta["attempt_id"] == attempt["attempt_id"]
    assert meta["source"] == "bench-test"
    assert meta["tags"] == ["python"]  # tags caso+correzione, sorted set

    # SFT e' guidato dalle CORREZIONI, non dagli status dell'attempt:
    # la correzione su attempt auto_failure entra nel dataset (pin del
    # comportamento reale: nessun filtro per status in export_sft_dataset).
    fallback = rows[1]
    assert fallback["messages"][2]["content"] == "fallback fix"

    orphan = rows[2]
    assert orphan["messages"][1]["content"] == "Fix the previous attempt."
    assert orphan["metadata"]["case_id"] is None
    assert orphan["metadata"]["source"] is None


def test_list_exports_lists_both_formats(tmp_path):
    store, _case, _attempt, _review, _corr = _store_with_full_chain(tmp_path)
    assert store.list_exports() == []  # niente datasets dir -> lista vuota

    sft = store.export_sft_dataset("sft_list_test.jsonl")
    packet = store.export_teacher_packet("packet_list_test.jsonl")

    exports = {item["filename"]: item for item in store.list_exports()}
    assert set(exports) == {"sft_list_test.jsonl", "packet_list_test.jsonl"}
    sft_item = exports["sft_list_test.jsonl"]
    assert sft_item["format"] == "sft_messages_jsonl"
    assert sft_item["rows"] == 1
    assert sft_item["size"] > 0
    assert sft_item["path"] == sft["path"]
    packet_item = exports["packet_list_test.jsonl"]
    assert packet_item["format"] == "teacher_review_v1"
    assert packet_item["rows"] == 1
    assert packet_item["path"] == packet["path"]


def test_list_exports_uses_logical_order_when_filesystem_mtimes_tie(tmp_path):
    store, _case, _attempt, _review, _corr = _store_with_full_chain(tmp_path)
    sft = store.export_sft_dataset("sft_same_tick.jsonl")
    packet = store.export_teacher_packet("packet_same_tick.jsonl")

    # Shared/virtual filesystems can give consecutive writes the exact same
    # mtime.  The persisted export metadata must remain authoritative.
    same_ns = 1_700_000_000_000_000_000
    os.utime(sft["path"], ns=(same_ns, same_ns))
    os.utime(packet["path"], ns=(same_ns, same_ns))

    exports = store.list_exports()
    assert [item["filename"] for item in exports] == [
        "packet_same_tick.jsonl",
        "sft_same_tick.jsonl",
    ]
    assert exports[0]["format"] == "teacher_review_v1"
    assert exports[0]["created_at"] == packet["created_at"]
    assert Path(packet["path"] + ".meta.json").exists()
    assert Path(sft["path"] + ".meta.json").exists()


# ---------------------------------------------------------------------------
# 7. endpoint cases: create round-trip + errore di validazione
# ---------------------------------------------------------------------------

def test_cases_endpoint_create_roundtrip_and_error(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)

    created = asyncio.run(training_router.api_training_cases_add(_fake_request({
        "task": "Scrivi count_up_to()", "title": "Count",
        "tags": ["python"], "expected_signals": ["file_created"],
    })))
    assert "error" not in created
    case = created["case"]
    assert case["title"] == "Count"
    assert case["source"] == "manual"
    # round-trip: il caso e' nello store reale
    assert [c["case_id"] for c in store.list_cases()] == [case["case_id"]]

    # convenzione errore del router: {"error": str(exc)}, niente eccezione/500
    bad = asyncio.run(training_router.api_training_cases_add(_fake_request({"title": "no task"})))
    assert bad == {"error": "training case task is required"}
    assert len(store.list_cases()) == 1


def test_seed_endpoint_roundtrip_and_idempotent(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)

    first = asyncio.run(training_router.api_training_seed(_fake_request({
        "benchmark_id": "devin-mini"})))
    assert first["benchmark_id"] == "devin-mini"
    assert first["count"] == len(first["created"]) > 0
    assert all(c["source"] == "devin-mini" for c in first["created"])

    # reseed idempotente: stessi (source, title, task) -> zero nuovi casi
    second = asyncio.run(training_router.api_training_seed(_fake_request({
        "benchmark_id": "devin-mini"})))
    assert second["count"] == 0 and second["created"] == []
    assert len(store.list_cases()) == first["count"]

    # benchmark sconosciuto: seed vuoto senza errore
    unknown = asyncio.run(training_router.api_training_seed(_fake_request({
        "benchmark_id": "bench-ghost"})))
    assert unknown["count"] == 0 and unknown["created"] == []


# ---------------------------------------------------------------------------
# 8. endpoint attempts: status invalido -> {"error": ...}, non 500
# ---------------------------------------------------------------------------

def test_attempts_endpoint_invalid_status_error_convention(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)

    bad = asyncio.run(training_router.api_training_attempts_add(_fake_request({
        "case_id": "manual", "status": "auto_succes"})))
    assert bad == {"error": "unsupported attempt status: auto_succes"}
    assert "attempt" not in bad
    assert store.list_attempts() == []

    ok = asyncio.run(training_router.api_training_attempts_add(_fake_request({
        "case_id": "manual", "status": "auto_failure", "prompt": "p"})))
    assert "error" not in ok
    assert ok["attempt"]["status"] == "auto_failure"
    # default: status omesso -> pending_review
    defaulted = asyncio.run(training_router.api_training_attempts_add(_fake_request({
        "case_id": "manual"})))
    assert defaulted["attempt"]["status"] == "pending_review"


def test_reviews_corrections_lessons_endpoints_error_convention(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)
    case = store.add_case(task="Task")
    attempt = store.add_attempt(case_id=case["case_id"], status="auto_success")

    bad_review = asyncio.run(training_router.api_training_reviews_add(_fake_request({
        "attempt_id": attempt["attempt_id"], "status": "nope"})))
    assert bad_review == {"error": "unsupported review status: nope"}
    ghost_review = asyncio.run(training_router.api_training_reviews_add(_fake_request({
        "attempt_id": "attempt_ghost", "status": "verified_success"})))
    assert ghost_review == {"error": "known attempt_id is required"}

    ok_review = asyncio.run(training_router.api_training_reviews_add(_fake_request({
        "attempt_id": attempt["attempt_id"], "status": "verified_success",
        "rationale": "ok", "reviewer": "colibri"})))
    assert ok_review["review"]["promotion"] == "eligible"
    assert ok_review["review"]["reviewer"] == "colibri"

    bad_correction = asyncio.run(training_router.api_training_corrections_add(_fake_request({
        "attempt_id": attempt["attempt_id"], "correction": ""})))
    assert bad_correction == {"error": "attempt_id and correction are required"}
    ok_correction = asyncio.run(training_router.api_training_corrections_add(_fake_request({
        "attempt_id": attempt["attempt_id"], "correction": "fix it"})))
    assert ok_correction["correction"]["correction"] == "fix it"

    bad_lesson = asyncio.run(training_router.api_training_lessons_add(_fake_request({
        "content": ""})))
    assert bad_lesson == {"error": "lesson content is required"}
    ok_lesson = asyncio.run(training_router.api_training_lessons_add(_fake_request({
        "content": "lesson", "status": "verified_success"})))
    assert ok_lesson["lesson"]["promotion"] == "eligible"


# ---------------------------------------------------------------------------
# 9. endpoint jobs: job sconosciuto + listing shape
# ---------------------------------------------------------------------------

def test_jobs_endpoint_unknown_job_and_listing():
    # listing vuoto: {"jobs": [...], "job": None}
    empty = asyncio.run(training_router.api_training_jobs())
    assert empty == {"jobs": [], "job": None}

    with training_router._training_jobs_lock:
        training_router._training_jobs["job_test"] = {
            "job_id": "job_test", "status": "queued", "total": 2, "completed": 0}

    listing = asyncio.run(training_router.api_training_jobs())
    assert [j["job_id"] for j in listing["jobs"]] == ["job_test"]
    assert listing["job"] is None

    single = asyncio.run(training_router.api_training_jobs(job_id="job_test"))
    assert single["jobs"] == []  # con job_id il listing resta vuoto
    assert single["job"]["job_id"] == "job_test"
    assert single["job"]["status"] == "queued"

    unknown = asyncio.run(training_router.api_training_jobs(job_id="job_ghost"))
    assert unknown["jobs"] == []
    assert unknown["job"] == {}  # snapshot di job sconosciuto: dict vuoto


# ---------------------------------------------------------------------------
# 10. endpoint overview: chiavi con store vuoto
# ---------------------------------------------------------------------------

def test_overview_endpoint_empty_store_keys(monkeypatch, tmp_path):
    _patched_store(monkeypatch, tmp_path)

    overview = asyncio.run(training_router.api_training_overview())
    expected_keys = {"summary", "cases", "attempts", "corrections", "reviews",
                     "latest_reviews", "review_queue", "lessons", "benchmarks",
                     "jobs", "memory_policy"}
    assert expected_keys <= set(overview)
    assert overview["cases"] == [] and overview["attempts"] == []
    assert overview["latest_reviews"] == {} and overview["review_queue"] == []
    assert overview["summary"]["cases"] == 0

    policy = overview["memory_policy"]
    assert policy["auto_promote"] is False
    assert policy["success_statuses"] == ["verified_success", "human_confirmed"]
    assert policy["failure_statuses"] == ["verified_failure", "failed", "needs_correction"]
    assert policy["auto_statuses"] == ["auto_success", "auto_failure"]
    assert policy["infra_statuses"] == ["runner_error"]

    # i benchmark builtin compaiono sempre (dropdown UI)
    builtin_ids = {b["id"] for b in overview["benchmarks"]}
    assert "devin-mini" in builtin_ids
    # store vuoto: nessuna source importata extra
    assert not any(b.get("source") == "imported" for b in overview["benchmarks"])


def test_overview_endpoint_surfaces_imported_sources(monkeypatch, tmp_path):
    # Fix dropdown (2026-07-15): le source importate (es. mbpp) devono
    # comparire nei benchmarks dell'overview o il run non e' selezionabile.
    store = _patched_store(monkeypatch, tmp_path)
    store.add_case(task="Task mbpp", title="MBPP 1", source="mbpp")

    overview = asyncio.run(training_router.api_training_overview())
    imported = [b for b in overview["benchmarks"] if b.get("source") == "imported"]
    assert [b["id"] for b in imported] == ["mbpp"]
    assert imported[0]["name"] == "mbpp (importati)"


# ---------------------------------------------------------------------------
# 11. endpoint export: scrittura file + listing via router
# ---------------------------------------------------------------------------

def test_export_endpoints_write_and_list(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)
    case = store.add_case(task="Scrivi add()", title="Add")
    attempt = store.add_attempt(case_id=case["case_id"], status="auto_failure")
    store.add_correction(attempt_id=attempt["attempt_id"], correction="fix",
                         corrected_solution="def add(a,b): return a+b")

    sft = asyncio.run(training_router.api_training_export(_fake_request({
        "filename": "sft_endpoint_test.jsonl"})))
    assert sft["rows"] == 1
    assert Path(sft["path"]).parent == store.exports_dir

    packet = asyncio.run(training_router.api_training_export_teacher_packet(_fake_request({
        "filename": "packet_endpoint_test.jsonl"})))
    assert packet["format"] == "teacher_review_v1"
    assert packet["rows"] == 1

    listing = asyncio.run(training_router.api_training_exports())
    formats = {item["filename"]: item["format"] for item in listing["exports"]}
    assert formats == {"sft_endpoint_test.jsonl": "sft_messages_jsonl",
                       "packet_endpoint_test.jsonl": "teacher_review_v1"}


# ---------------------------------------------------------------------------
# 12. endpoint MBPP import: download mockato (mai rete nei test)
# ---------------------------------------------------------------------------
# L'handler chiama `import_mbpp_cases` risolto dai globali del ROUTER a
# call-time (importato a livello di modulo in training.py), quindi il patch
# va sul modulo router, come `_training_store_for`.

def _fake_mbpp_import(seeded_titles):
    """Fake di import_mbpp_cases: niente download, seeda N casi nello store
    REALE e ritorna la shape di summary reale (devin/training/adapters.py
    L163-170). Registra gli argomenti per il pin del clamping."""
    calls = []

    def fake(store, limit=10, offset=0, force_download=False):
        calls.append({"limit": limit, "offset": offset,
                      "force_download": force_download})
        cases = [{"prompt": f"Task {t}", "title": t, "kind": "code_generation",
                  "tags": ["python", "mbpp"], "expected_signals": ["tests_pass"]}
                 for t in seeded_titles[:limit]]
        created = store.seed_cases(cases, source="mbpp")
        return {
            "dataset_rows": len(seeded_titles),
            "downloaded_now": False,
            "converted": len(cases),
            "created": len(created),
            "skipped_existing": len(cases) - len(created),
            "source": "mbpp",
            "cache": "/fake/cache/mbpp.jsonl",
        }

    fake.calls = calls
    return fake


def test_mbpp_import_endpoint_happy_path_and_clamping(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)
    fake = _fake_mbpp_import(["MBPP 1: add", "MBPP 2: sub", "MBPP 3: mul"])
    monkeypatch.setattr(training_router, "import_mbpp_cases", fake)

    result = asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({
        "limit": 2, "offset": 0, "force_download": False})))
    assert "error" not in result
    assert result["created"] == 2
    assert result["converted"] == 2
    assert result["skipped_existing"] == 0
    assert result["source"] == "mbpp"
    assert result["downloaded_now"] is False
    # round-trip: i casi sono davvero nello store (source mbpp)
    titles = {c["title"] for c in store.list_cases()}
    assert titles == {"MBPP 1: add", "MBPP 2: sub"}
    assert all(c["source"] == "mbpp" for c in store.list_cases())

    # clamping del router: limit >1000 -> 1000, offset <0 -> 0, default 10
    asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({
        "limit": 5000, "offset": -7})))
    assert fake.calls[-1] == {"limit": 1000, "offset": 0, "force_download": False}
    asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({})))
    assert fake.calls[-1] == {"limit": 10, "offset": 0, "force_download": False}

    # reseed idempotente via endpoint: stessi casi -> zero nuovi
    again = asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({
        "limit": 2})))
    assert again["created"] == 0 and again["skipped_existing"] == 2
    assert len(store.list_cases()) == 3  # 2 + 1 (MBPP 3 dal call clampato)


def test_mbpp_import_endpoint_download_failure_error_convention(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)

    def boom(store, limit=10, offset=0, force_download=False):
        raise RuntimeError("download fallito: rete assente")

    monkeypatch.setattr(training_router, "import_mbpp_cases", boom)

    result = asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({
        "limit": 10})))
    # convenzione errore del router: {"error": ...}, niente eccezione/500
    assert result == {"error": "import MBPP fallito: download fallito: rete assente"}
    assert store.list_cases() == []  # niente casi parziali


def test_mbpp_import_endpoint_invalid_payload_error_convention(monkeypatch, tmp_path):
    store = _patched_store(monkeypatch, tmp_path)
    fake = _fake_mbpp_import(["MBPP 1: add"])
    monkeypatch.setattr(training_router, "import_mbpp_cases", fake)

    # limit non convertibile a int: la validazione (int()) e' DENTRO il try
    # dell'handler -> stessa convenzione {"error": ...}, import mai chiamato
    result = asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({
        "limit": "abc"})))
    assert "error" in result
    assert result["error"].startswith("import MBPP fallito: ")
    assert fake.calls == []
    assert store.list_cases() == []

    # payload vuoto/non-dict-safe: data.get(...) su dict vuoto -> default ok
    ok = asyncio.run(training_router.api_training_adapter_mbpp(_fake_request({})))
    assert "error" not in ok and ok["created"] == 1

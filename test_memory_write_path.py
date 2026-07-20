"""Memory write-path hardening regressions (2026-07-18).

Anti-contamination policy: nothing becomes permanent/recall-safe memory
automatically — only verified_success / verified_failure / human_confirmed
are recall-safe, and the automatic write paths may only MINT verified_*.
These tests pin the four write-path fixes:

1. record_eval_result + LocalMemoryStore.store enforce the taxonomy
   (no auto-minting recall-safe memories).
2. _remember_scaffold_outcome dedups by memory_key (no repeat-failure flooding).
3. Success memories carry security warnings (no evidence overstatement).
4. _fallback_envelope derives polarity from status (no positive quarantine).
"""

from __future__ import annotations

import json

import pytest


def _make_memory_client(path):
    from devin.ai.hybrid_memory_client import HybridMemoryClient

    client = HybridMemoryClient({"local_memory": {"path": str(path)}})
    client.understory.enabled = False
    client.automem.enabled = False
    return client


def _read_records(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class _CaptureMemory:
    """Minimal memory_client fake: captures store_local calls (no .local attr,
    so key-based dedup is bypassed exactly like the legacy test fakes)."""

    def __init__(self):
        self.calls = []

    def store_local(self, content, **kwargs):
        self.calls.append({"content": content, **kwargs})
        return "local_stored"


def _make_orchestrator(project_path, memory_client):
    from devin.core.orchestrator import Orchestrator

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_path = str(project_path)
    orchestrator.memory_client = memory_client
    return orchestrator


# ---------------------------------------------------------------------------
# Fix 1 — eval/write path can never mint recall-safe memory
# ---------------------------------------------------------------------------

def test_record_eval_result_normalizes_human_confirmed_to_review_only(tmp_path):
    from devin.memory.eval_recorder import record_eval_result

    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    outcome = record_eval_result(
        client,
        project_path=str(tmp_path / "Demo Project"),
        task="build something",
        eval_name="some_eval",
        status="human_confirmed",
        reason="caller tried to mint recall-safe memory",
    )
    assert outcome == "local_stored"
    (record,) = _read_records(path)
    assert record["status"] == "pending_review"
    assert record["polarity"] == "neutral"
    assert "status:pending_review" in record["tags"]
    assert "status:human_confirmed" not in record["tags"]
    assert "human_confirmed" in record["content"]  # original status kept as evidence
    assert client.local.recall("build something") == []  # not recallable


def test_record_eval_result_normalizes_hypothesis_to_review_only(tmp_path):
    from devin.memory.eval_recorder import record_eval_result

    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    outcome = record_eval_result(
        client,
        project_path=str(tmp_path / "Demo Project"),
        task="build something",
        eval_name="some_eval",
        status="hypothesis",
        reason="unverified guess",
    )
    assert outcome == "local_stored"
    (record,) = _read_records(path)
    assert record["status"] == "pending_review"
    assert record["polarity"] == "neutral"
    assert record["kind"] == "raw_observation"
    assert client.local.recall("build something") == []


def test_record_eval_result_normalizes_garbage_status_to_review_only(tmp_path):
    from devin.memory.eval_recorder import record_eval_result

    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    outcome = record_eval_result(
        client,
        project_path=str(tmp_path / "Demo Project"),
        task="build something",
        eval_name="some_eval",
        status="totally_made_up",
        reason="garbage status string",
    )
    assert outcome == "local_stored"
    (record,) = _read_records(path)
    assert record["status"] == "pending_review"
    assert record["polarity"] == "neutral"
    assert client.local.recall("build something") == []


def test_record_eval_result_legitimate_statuses_unchanged(tmp_path):
    from devin.memory.eval_recorder import record_eval_result

    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    ok = record_eval_result(
        client,
        project_path=str(tmp_path / "Demo Project"),
        task="build something that worked",
        eval_name="some_eval",
        status="verified_success",
        reason="tests green",
    )
    ko = record_eval_result(
        client,
        project_path=str(tmp_path / "Demo Project"),
        task="build something that broke",
        eval_name="some_eval",
        status="verified_failure",
        failure_type="own_tests_failed",
        reason="tests red",
    )
    assert ok == "local_stored"
    assert ko == "local_stored"
    records = _read_records(path)
    by_status = {record["status"]: record for record in records}
    assert by_status["verified_success"]["polarity"] == "positive"
    assert by_status["verified_success"]["kind"] == "eval_result"
    assert by_status["verified_failure"]["polarity"] == "negative"
    assert by_status["verified_failure"]["kind"] == "failure_lesson"
    # verified_* remain recall-safe (legitimate evidence)
    assert len(client.local.recall("build something")) == 2


def test_validate_memory_tags_unknown_status():
    from devin.memory.taxonomy import validate_memory_tags

    errors = validate_memory_tags(["status:bogus", "kind:lesson"])
    assert any("unknown status:bogus" in error for error in errors)


def test_validate_memory_tags_unknown_kind():
    from devin.memory.taxonomy import validate_memory_tags

    errors = validate_memory_tags(["status:pending_review", "kind:diary"])
    assert any("unknown kind:diary" in error for error in errors)


def test_validate_memory_tags_quarantine_cannot_be_promoted():
    from devin.memory.taxonomy import validate_memory_tags

    errors = validate_memory_tags(
        ["status:quarantine", "kind:lesson", "promotion:promoted"]
    )
    assert any("promotion:promoted" in error for error in errors)


def test_validate_memory_tags_accepts_verified_vocab():
    from devin.memory.taxonomy import build_memory_tags, validate_memory_tags

    assert validate_memory_tags(build_memory_tags(status="verified_success")) == []
    assert validate_memory_tags(build_memory_tags(status="human_confirmed")) == []


def test_local_store_normalizes_invalid_tags_to_review_only(tmp_path):
    client = _make_memory_client(tmp_path / "mem.jsonl")
    outcome = client.local.store(
        "evidence that must not be lost",
        tags=["status:bogus", "kind:diary", "promotion:promoted", "polarity:positive"],
    )
    assert outcome == "local_stored"  # fail-safe: stored, not raised/dropped
    (record,) = _read_records(tmp_path / "mem.jsonl")
    assert record["status"] == "pending_review"
    assert record["kind"] == "raw_observation"
    assert record["polarity"] == "neutral"
    assert record["promotion"] == "manual_required"
    assert record["taxonomy_violations"]  # violation recorded on the record
    assert client.local.recall("evidence lost") == []  # not recallable


def test_local_store_normalizes_quarantine_promoted_combo(tmp_path):
    client = _make_memory_client(tmp_path / "mem.jsonl")
    outcome = client.local.store(
        "quarantined note",
        tags=["status:quarantine", "kind:lesson", "promotion:promoted"],
    )
    assert outcome == "local_stored"
    (record,) = _read_records(tmp_path / "mem.jsonl")
    assert record["status"] == "pending_review"
    assert record["promotion"] == "manual_required"
    assert record["taxonomy_violations"]


def test_local_store_clean_tags_have_no_violations(tmp_path):
    from devin.memory.taxonomy import build_memory_tags

    client = _make_memory_client(tmp_path / "mem.jsonl")
    outcome = client.local.store(
        "verified lesson",
        tags=build_memory_tags(status="verified_success", polarity="positive"),
    )
    assert outcome == "local_stored"
    (record,) = _read_records(tmp_path / "mem.jsonl")
    assert record["status"] == "verified_success"
    assert record["taxonomy_violations"] == []


# ---------------------------------------------------------------------------
# Fix 2 — _remember_scaffold_outcome dedups via memory_key
# ---------------------------------------------------------------------------

def test_scaffold_outcome_memories_dedup_by_memory_key(tmp_path):
    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    orchestrator = _make_orchestrator(tmp_path / "Demo Project", client)
    quality = {"status": "verified_failure", "errors": ["1 test failed"]}

    first = orchestrator._remember_scaffold_outcome("build a parser", quality, ["parser.py"])
    second = orchestrator._remember_scaffold_outcome("build a parser", quality, ["parser.py"])

    assert first == "local_stored"
    assert second == "duplicate"
    records = _read_records(path)
    assert len(records) == 1
    assert records[0]["memory_key"]
    assert "memory_key:" + records[0]["memory_key"] in records[0]["tags"]


def test_scaffold_outcome_different_failure_still_stores(tmp_path):
    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    orchestrator = _make_orchestrator(tmp_path / "Demo Project", client)
    quality = {"status": "verified_failure", "errors": ["1 test failed"]}

    first = orchestrator._remember_scaffold_outcome("build a parser", quality, ["parser.py"])
    other = orchestrator._remember_scaffold_outcome(
        "build a different parser", quality, ["parser.py"]
    )

    assert first == "local_stored"
    assert other == "local_stored"
    records = _read_records(path)
    assert len(records) == 2
    assert records[0]["memory_key"] != records[1]["memory_key"]


def test_scaffold_outcome_success_and_failure_have_distinct_keys(tmp_path):
    path = tmp_path / "mem.jsonl"
    client = _make_memory_client(path)
    orchestrator = _make_orchestrator(tmp_path / "Demo Project", client)

    ko = orchestrator._remember_scaffold_outcome(
        "build a parser", {"status": "verified_failure", "errors": ["red"]}, ["parser.py"]
    )
    ok = orchestrator._remember_scaffold_outcome(
        "build a parser", {"status": "verified_success", "errors": []}, ["parser.py"]
    )

    assert ko == "local_stored"
    assert ok == "local_stored"
    assert len(_read_records(path)) == 2


# ---------------------------------------------------------------------------
# Fix 3 — success memories must not overstate evidence (security warnings)
# ---------------------------------------------------------------------------

def test_success_memory_carries_security_warnings(tmp_path):
    memory = _CaptureMemory()
    orchestrator = _make_orchestrator(tmp_path / "Demo Project", memory)
    outcome = orchestrator._remember_scaffold_outcome(
        "build a downloader",
        {
            "status": "verified_success",
            "errors": [],
            "security_warnings": [
                "B301: blacklist pickle usage",
                "B602: subprocess with shell=True",
            ],
            "security_scanner": "bandit",
        },
        ["app.py"],
    )
    assert outcome == "local_stored"
    content = memory.calls[0]["content"]
    assert "B301: blacklist pickle usage" in content
    assert "B602: subprocess with shell=True" in content
    assert "Security warnings (2 finding MEDIUM+" in content
    assert "WITH security warnings" in content
    assert "a tested successful approach." not in content  # not a clean success


def test_clean_success_memory_unchanged(tmp_path):
    memory = _CaptureMemory()
    orchestrator = _make_orchestrator(tmp_path / "Demo Project", memory)
    outcome = orchestrator._remember_scaffold_outcome(
        "build a calculator",
        {"status": "verified_success", "errors": [], "security_warnings": []},
        ["calc.py"],
    )
    assert outcome == "local_stored"
    content = memory.calls[0]["content"]
    assert "This is a tested successful approach." in content
    assert "Security warnings" not in content


def test_failure_memory_also_lists_security_warnings_when_present(tmp_path):
    memory = _CaptureMemory()
    orchestrator = _make_orchestrator(tmp_path / "Demo Project", memory)
    outcome = orchestrator._remember_scaffold_outcome(
        "build a downloader",
        {
            "status": "verified_failure",
            "errors": ["2 tests failed"],
            "security_warnings": ["B602: subprocess with shell=True"],
        },
        ["app.py"],
    )
    assert outcome == "local_stored"
    content = memory.calls[0]["content"]
    assert "do not repeat" in content
    assert "B602: subprocess with shell=True" in content


# ---------------------------------------------------------------------------
# Fix 4 — _fallback_envelope polarity coherence
# ---------------------------------------------------------------------------

def test_fallback_envelope_review_only_statuses_are_neutral():
    from devin.ai.hybrid_memory_client import _fallback_envelope

    for status in ("pending_review", "quarantine", "hypothesis", "inconclusive"):
        text = _fallback_envelope("some content", [f"status:{status}"])
        assert f"status={status}" in text
        assert "polarity=neutral" in text, f"{status} must not default to positive"


def test_fallback_envelope_verified_statuses_derive_polarity():
    from devin.ai.hybrid_memory_client import _fallback_envelope

    assert "polarity=negative" in _fallback_envelope("c", ["status:verified_failure"])
    assert "polarity=positive" in _fallback_envelope("c", ["status:verified_success"])
    assert "polarity=positive" in _fallback_envelope("c", ["status:human_confirmed"])


def test_fallback_envelope_explicit_polarity_tag_still_wins():
    from devin.ai.hybrid_memory_client import _fallback_envelope

    text = _fallback_envelope(
        "wrong API choice",
        ["status:verified_failure", "polarity:negative", "evidence:test_failure"],
    )
    assert "status=verified_failure" in text
    assert "polarity=negative" in text
    assert "wrong API choice" in text


# ---------------------------------------------------------------------------
# W8 (2026-07-18) — is_operational_build_request routing boundaries
#
# Regola pinnata (eval_recorder.py L25-32):
#   create_score >= 1 AND deliverable_score >= 2 AND explanation_score == 0
# Consumatori: chat.py scaffold routing + detect_chat_only_output.
# I termini matchano per SOTTOSTRINGA sul messaggio lowercased (es. "test"
# matcha dentro "tests.py", "app" dentro "applicazione"): i casi sotto
# tengono conto di questo conteggio.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        # --- create_score: soglia >= 1 ---
        ("fammi una app con i test", False),          # create=0, deliv=2 -> sotto soglia create
        ("crea una app con i test", True),            # create=1 (just-above), deliv=2
        ("crea e implementa una app con i test", True),  # create=2: piu' create non cambia il verdetto
        # --- deliverable_score: soglia >= 2 ---
        ("crea una app", False),                      # deliv=1 (just-below)
        ("build an mvp", False),                      # deliv=1 (just-below, EN)
        ("build an mvp with a gui", True),            # deliv=2 (just-above, EN)
        ("realizza il progetto e la gui", True),      # deliv=2 (just-above, IT)
        ("rendi la UI/UX più moderna e simmetrica", True),
        ("migliora l'interfaccia frontend e scrivi il codice", True),
        # --- explanation_score: veto (deve essere 0) ---
        ("crea una app con i test e spiega come funziona", False),  # veto spiega+come funziona
        ("analizza e crea una app con i test", False),              # veto analizza (ordine irrilevante)
        ("implementa il progetto e la gui, poi la roadmap", False), # veto roadmap
        ("crea una app con i test: che ne pensi?", False),          # veto che ne pensi
        # --- non-operational / vuoto ---
        ("", False),
        ("ciao, come va?", False),
    ],
    ids=[
        "create-below",
        "create-just-above",
        "create-multiple-still-true",
        "deliverable-just-below-it",
        "deliverable-just-below-en",
        "deliverable-just-above-en",
        "deliverable-just-above-it",
        "change-ui-ux",
        "change-frontend-code",
        "explanation-veto-spiega",
        "explanation-veto-analizza",
        "explanation-veto-roadmap",
        "explanation-veto-che-ne-pensi",
        "empty-string",
        "generic-chat",
    ],
)
def test_is_operational_build_request_boundaries(message, expected):
    from devin.memory.eval_recorder import is_operational_build_request

    assert is_operational_build_request(message) is expected


def test_is_operational_build_request_none_message():
    from devin.memory.eval_recorder import is_operational_build_request

    assert is_operational_build_request(None) is False


def test_detect_chat_only_output_skips_non_operational_messages():
    """Consumer pin: un messaggio sotto soglia (deliverable=1) non deve mai
    produrre un memory di failure chat_only_output, anche se la risposta e'
    solo snippet in fences."""
    from devin.memory.eval_recorder import detect_chat_only_output

    result = detect_chat_only_output(
        "crea una app",  # deliv=1 -> non operational
        "```python\nprint('hello')\n```",
    )
    assert result is None


def test_detect_chat_only_output_fires_on_operational_chat_only():
    """Consumer pin: richiesta operational (create>=1, deliv>=2, no spiegazione)
    risposta con sole fences e senza claim di esecuzione => verified_failure."""
    from devin.memory.eval_recorder import detect_chat_only_output

    result = detect_chat_only_output(
        "crea una app con i test",
        "Ecco il codice:\n```python\nprint('hello')\n```",
    )
    assert result is not None
    assert result["status"] == "verified_failure"
    assert result["failure_type"] == "chat_only_output"

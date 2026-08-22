from datetime import datetime, timedelta, timezone

from devin.memory.knowledge_exchange import KnowledgeExchangeStore


def _ref(seed="a", status="verified"):
    return {"evidence_id": "sha256:" + seed * 64, "claim": "targeted tests passed", "status": status}


def test_exchange_quarantines_then_promotes_only_with_verified_review(tmp_path):
    store = KnowledgeExchangeStore(tmp_path / "exchange")
    item = store.propose(
        content="Use bounded retries and stop on an identical failure signature.",
        source_role="devin",
        source_project_id="project:abc",
        audience=["teacher", "hermes"],
        evidence=[_ref("a")],
    )
    assert item["status"] == "quarantine"
    assert store.list_promoted("teacher") == []

    review = store.review(
        artifact_id=item["artifact_id"], decision="approve", reviewer_role="human",
        reason="The cited regression test reproduces and closes the loop safely.",
        evidence=[_ref("b")],
    )
    assert review["schema"] == "knowledge_exchange_review_v1"
    promoted = store.list_promoted("teacher")
    assert promoted[0]["content"].startswith("Use bounded retries")
    assert store.list_promoted("clippy") == []


def test_exchange_rejects_unverified_approval_and_supports_revocation(tmp_path):
    store = KnowledgeExchangeStore(tmp_path)
    item = store.propose(
        content="Candidate lesson pending evidence.", source_role="devin",
        source_project_id="general", audience=["teacher"], evidence=[_ref("c", "unverified")],
    )
    try:
        store.review(
            artifact_id=item["artifact_id"], decision="approve", reviewer_role="human",
            reason="Looks plausible but lacks a verified rerun.", evidence=[_ref("d")],
        )
    except ValueError as exc:
        assert "not recall-safe" in str(exc)
    else:
        raise AssertionError("unverified artifact promoted")

    store.review(
        artifact_id=item["artifact_id"], decision="revoke", reviewer_role="human",
        reason="Withdrawn before promotion because the source evidence is stale.",
    )
    assert store.effective_status(item["artifact_id"]) == "revoked"
    try:
        store.review(
            artifact_id=item["artifact_id"], decision="approve", reviewer_role="human",
            reason="A revoked artifact must require a fresh proposal instead.", evidence=[_ref("e")],
        )
    except ValueError as exc:
        assert "new artifact" in str(exc)
    else:
        raise AssertionError("revoked artifact was promoted")


def test_exchange_rejects_same_role_self_approval(tmp_path):
    store = KnowledgeExchangeStore(tmp_path)
    item = store.propose(
        content="A source role cannot approve its own lesson.", source_role="devin",
        source_project_id="general", audience=["teacher"], evidence=[_ref("1")],
    )
    try:
        store.review(
            artifact_id=item["artifact_id"], decision="approve", reviewer_role="devin",
            reason="This must fail even when the evidence reference is verified.", evidence=[_ref("2")],
        )
    except ValueError as exc:
        assert "independent reviewer" in str(exc)
    else:
        raise AssertionError("source role self-approved its knowledge")


def test_exchange_expiry_and_idempotent_proposal(tmp_path):
    store = KnowledgeExchangeStore(tmp_path)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    kwargs = dict(
        content="Versioned operational lesson.", source_role="devin", source_project_id="p",
        audience=["teacher"], evidence=[_ref("e")], expires_at=expiry,
    )
    first = store.propose(**kwargs)
    assert store.propose(**kwargs)["artifact_id"] == first["artifact_id"]
    store.review(
        artifact_id=first["artifact_id"], decision="approve", reviewer_role="human",
        reason="Verified against an independent targeted rerun receipt.", evidence=[_ref("f")],
    )
    assert len(store.list_promoted("teacher")) == 1
    assert store.list_promoted("teacher", now=datetime.now(timezone.utc) + timedelta(hours=2)) == []

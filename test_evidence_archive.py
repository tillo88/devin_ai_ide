"""CS1 tests: content-addressed evidence archive.

Covers: idempotent store, byte-for-byte retrieval, integrity/tamper detection,
provenance index, and the cardinal rule that a checkpoint reference never
contains the artifact body.
"""
import pytest

from devin.core.evidence_archive import EvidenceArchive, EvidenceArchiveError


def test_store_is_idempotent_and_content_addressed(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    body = "PASS 192K vanilla F16/F16\n" * 100
    id1 = arc.store(body, kind="benchmark")
    id2 = arc.store(body, kind="benchmark")
    assert id1 == id2 and id1.startswith("sha256:")
    # One blob on disk despite two logical stores.
    blobs = list((tmp_path / "sess" / "evidence").iterdir())
    assert len(blobs) == 1
    # But provenance records both occurrences.
    assert len(list(arc.records())) == 2


def test_retrieval_is_byte_for_byte(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    body = "run-20260721-165249 topology 23/23 PASS"
    eid = arc.store(body, kind="log")
    assert arc.get_text(eid) == body
    assert arc.get_bytes(eid) == body.encode("utf-8")


def test_tamper_is_detected(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    eid = arc.store("original evidence", kind="log")
    digest = eid.split(":", 1)[1]
    # Corrupt the blob on disk.
    (tmp_path / "sess" / "evidence" / digest).write_text("tampered", encoding="utf-8")
    with pytest.raises(EvidenceArchiveError, match="integrity"):
        arc.get_text(eid)


def test_invalid_and_missing_ids(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    with pytest.raises(EvidenceArchiveError, match="invalid evidence_id"):
        arc.get_bytes("not-a-hash")
    missing = "sha256:" + ("0" * 64)
    assert arc.exists(missing) is False
    with pytest.raises(EvidenceArchiveError, match="not found"):
        arc.get_bytes(missing)


def test_reference_never_contains_body(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    body = "SECRET-LONG-ARTIFACT-BODY " * 50
    eid = arc.store(body, kind="log")
    ref = EvidenceArchive.make_ref(eid, claim="192K vanilla is stable",
                                   status="unverified")
    serialized = str(ref)
    assert body not in serialized
    assert ref["evidence_id"] == eid
    assert ref["status"] == "unverified"
    assert ref["claim"] == "192K vanilla is stable"


def test_reference_rejects_unknown_status(tmp_path):
    with pytest.raises(EvidenceArchiveError, match="invalid status"):
        EvidenceArchive.make_ref("sha256:" + "a" * 64, claim="x", status="PASS")


def test_index_records_have_utc_and_local(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    arc.store("x", kind="raw", meta={"run_id": "r1"})
    rec = list(arc.records())[0]
    assert rec["created_at"] and rec["created_at_local"]
    assert rec["meta"]["run_id"] == "r1"
    assert rec["kind"] == "raw"

"""CS2 tests: hybrid evidence retrieval (exact / structured / keyword) and the
cardinal DoD: targeted retrieval returns a small bounded fragment, not the
whole artifact.
"""
from devin.core.evidence_archive import EvidenceArchive
from devin.core.evidence_retriever import EvidenceRetriever


def _seed(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    arc.store("bench mmq output\n" * 10, kind="benchmark",
              meta={"run_id": "20260721-165249", "model": "Qwen3.6",
                    "test": "context", "status": "verified",
                    "claim": "192K vanilla F16/F16 is stable"})
    arc.store("power topology log\n" * 10, kind="log",
              meta={"run_id": "20260721-151701", "model": "Qwen3.6",
                    "test": "psu", "claim": "1660 Ti moved to 650W PSU"})
    arc.store("unrelated note", kind="note", meta={"topic": "misc"})
    return arc, EvidenceRetriever(arc)


def test_exact_lookup_by_id_and_run(tmp_path):
    arc, ret = _seed(tmp_path)
    eid = arc.store("dup", kind="benchmark", meta={"run_id": "R-exact"})
    assert ret.by_id(eid)["evidence_id"] == eid
    # bare sha256 also works
    assert ret.by_id(eid.split(":", 1)[1])["evidence_id"] == eid
    runs = ret.by_run("20260721-165249")
    assert len(runs) == 1 and runs[0]["meta"]["test"] == "context"


def test_structured_query_by_meta(tmp_path):
    _arc, ret = _seed(tmp_path)
    verified = ret.by_meta(status="verified", model="Qwen3.6")
    assert len(verified) == 1
    assert verified[0]["meta"]["test"] == "context"
    # kind filter matches the top-level field
    logs = ret.by_meta(kind="log")
    assert len(logs) == 1 and logs[0]["meta"]["test"] == "psu"


def test_keyword_search_ranks_relevant(tmp_path):
    _arc, ret = _seed(tmp_path)
    hits = ret.search("psu power 1660", top_k=2)
    assert hits
    assert hits[0]["meta"].get("test") == "psu"
    # a query with no overlap returns nothing
    assert ret.search("zzz-nonexistent-token") == []


def test_fragment_is_bounded_not_whole_artifact(tmp_path):
    arc = EvidenceArchive(tmp_path / "sess")
    big = "\n".join(f"line {i} filler content here" for i in range(5000))
    big += "\nNEEDLE unique marker line\n"
    eid = arc.store(big, kind="log", meta={"run_id": "big"})
    ret = EvidenceRetriever(arc)

    # Whole artifact is large...
    assert len(arc.get_text(eid)) > 100_000
    # ...but a bounded fragment stays small.
    head = ret.fragment(eid, max_chars=1500)
    assert len(head) <= 1500

    # Query-directed fragment finds the needle without returning everything.
    hit = ret.fragment(eid, max_chars=1500, query="NEEDLE unique marker")
    assert "NEEDLE unique marker" in hit
    assert len(hit) <= 1500


def test_refs_for_context_carry_claim_not_body(tmp_path):
    arc, ret = _seed(tmp_path)
    records = ret.by_meta(status="verified")
    refs = ret.refs_for_context(records, status="verified")
    assert refs[0]["claim"] == "192K vanilla F16/F16 is stable"
    assert refs[0]["status"] == "verified"
    assert refs[0]["evidence_id"].startswith("sha256:")

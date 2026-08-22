from devin.core.chat_continuity import accept_checkpoint_proposal, build_checkpoint
from devin.core.prompt_layout import compose_prompt_layout


def _history(count=12):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 40}
        for i in range(count)
    ]


def test_cs4_proposal_requires_explicit_trigger_and_exact_evidence_boundary():
    history = _history()
    proposal = build_checkpoint(
        history,
        recent_messages=4,
        trigger="context_pressure",
        summarizer=lambda _: "## Facts\n" + "bounded evidence " * 8,
    )
    accepted = accept_checkpoint_proposal(history, proposal, recent_messages=4)
    assert accepted["validation"] == "accepted"
    assert accepted["promotion"] == "none"
    assert accepted["trigger"] == "context_pressure"
    assert accepted["checkpoint_id"].startswith("sha256:")

    tampered = dict(proposal, source_fingerprint="0" * 64)
    try:
        accept_checkpoint_proposal(history, tampered, recent_messages=4)
    except ValueError as exc:
        assert "fingerprint" in str(exc)
    else:
        raise AssertionError("tampered checkpoint accepted")


def test_cs5_prefix_is_stable_when_only_retrieval_changes():
    common = dict(
        stable_parts=["system", "project rules"],
        recent_history=[{"role": "user", "content": "old"}],
        user_content="new",
    )
    first = compose_prompt_layout(retrieval_parts=["chunk A"], **common)
    second = compose_prompt_layout(retrieval_parts=["chunk B"], **common)
    assert first["stable_prefix_fingerprint"] == second["stable_prefix_fingerprint"]
    assert first["messages"][0] == second["messages"][0]
    assert first["messages"][1] != second["messages"][1]
    assert "EPHEMERAL RETRIEVAL" in first["messages"][1]["content"]


def test_cs5_checkpoint_change_intentionally_invalidates_prefix():
    base = dict(stable_parts=["system"], retrieval_parts=[], recent_history=[], user_content="q")
    one = compose_prompt_layout(checkpoint={"checkpoint_id": "sha256:a"}, **base)
    two = compose_prompt_layout(checkpoint={"checkpoint_id": "sha256:b"}, **base)
    assert one["stable_prefix_fingerprint"] != two["stable_prefix_fingerprint"]

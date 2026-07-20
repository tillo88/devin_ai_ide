import json

from devin.core.chat_continuity import (
    CHECKPOINT_SCHEMA,
    build_checkpoint,
    checkpoint_needs_refresh,
    context_from_checkpoint,
    history_fingerprint,
    should_checkpoint,
)
from devin.core.chat_persistence import ChatPersistence
from devin.core.project_space import ProjectSpace


def _history(count=16):
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message {i}: verified detail about file_{i}.py and next step",
        }
        for i in range(count)
    ]


def test_checkpoint_triggers_before_message_window_drops_old_turns():
    assert should_checkpoint(
        _history(13), context_size=8192, max_history_messages=20,
        recent_messages=8, min_messages=12,
    )
    assert not should_checkpoint(
        _history(11), context_size=8192, max_history_messages=20,
        recent_messages=8, min_messages=12,
    )


def test_checkpoint_is_structured_bounded_and_reused():
    calls = []

    def summarize(prompt):
        calls.append(prompt)
        return ("## Facts\nVerified A with exact file and test evidence.\n"
                "## Next action\nRun the targeted tests and preserve the current branch.")

    history = _history(16)
    checkpoint = build_checkpoint(
        history, summarizer=summarize, recent_messages=8, summary_max_chars=500,
    )
    assert checkpoint["schema"] == CHECKPOINT_SCHEMA
    assert checkpoint["summarized_messages"] == 8
    assert checkpoint["source_fingerprint"] == history_fingerprint(history[:8])
    assert checkpoint["generation"] == "model"
    assert len(checkpoint["summary"]) <= 500
    assert len(calls) == 1

    same = build_checkpoint(history, existing=checkpoint, summarizer=summarize,
                            recent_messages=8, summary_max_chars=500)
    assert same == checkpoint
    assert len(calls) == 1
    assert "not long-term memory" in context_from_checkpoint(checkpoint)


def test_checkpoint_refresh_is_incremental_and_detects_history_edits():
    first = build_checkpoint(
        _history(16), summarizer=lambda _: "## Facts\n" + "A" * 100,
        recent_messages=8,
    )
    extended = _history(22)
    assert checkpoint_needs_refresh(extended, first, recent_messages=8,
                                    refresh_messages=6)
    prompts = []
    second = build_checkpoint(
        extended, existing=first,
        summarizer=lambda prompt: prompts.append(prompt) or "## Facts\n" + "B" * 100,
        recent_messages=8,
    )
    assert second["summarized_messages"] == 14
    assert "PREVIOUS VERIFIED HANDOFF" in prompts[0]
    assert "message 8:" in prompts[0]
    assert "message 0:" not in prompts[0].split("NEW CONVERSATION EVIDENCE:", 1)[1]

    edited = list(extended)
    edited[0] = {"role": "user", "content": "edited historical evidence"}
    assert checkpoint_needs_refresh(edited, second, recent_messages=8,
                                    refresh_messages=99)


def test_failed_summarizer_uses_evidence_fallback():
    checkpoint = build_checkpoint(
        _history(14), summarizer=lambda _: None, recent_messages=6,
        summary_max_chars=700,
    )
    assert checkpoint["generation"] == "deterministic"
    assert "verbatim evidence" in checkpoint["summary"]
    assert len(checkpoint["summary"]) <= 700


def test_chat_persistence_preserves_checkpoint_title_and_history(tmp_path):
    cp = ChatPersistence(str(tmp_path), chat_id="chat_safe")
    cp.chat_dir.mkdir(parents=True)
    cp.session_file.write_text(json.dumps({
        "title": "Important work",
        "history": _history(4),
        "updated_at": "old",
    }), encoding="utf-8")
    checkpoint = build_checkpoint(
        _history(12), summarizer=lambda _: "## Facts\n" + "C" * 100,
        recent_messages=4,
    )
    cp.set_continuity(checkpoint)
    cp.append("assistant", "new result")

    document = cp.load_document()
    assert document["title"] == "Important work"
    assert document["continuity"] == checkpoint
    assert document["history"][-1]["content"] == "new result"
    cp.save(document["history"], continuity=None)
    assert cp.get_continuity() is None


def test_new_chat_can_inherit_checkpoint_without_copying_history(tmp_path):
    checkpoint = build_checkpoint(
        _history(12), summarizer=lambda _: "## Facts\n" + "D" * 100,
        recent_messages=4,
    )
    ps = ProjectSpace(tmp_path)
    chat_id = ps.new_chat(
        "Continuation", continuity=checkpoint, continued_from="chat_parent",
    )
    document = json.loads(
        (ps.chats_dir / f"{chat_id}.json").read_text(encoding="utf-8")
    )
    assert document["history"] == []
    assert document["continuity"] == checkpoint
    assert document["continued_from"] == "chat_parent"

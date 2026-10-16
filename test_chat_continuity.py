import json

from devin.core.chat_continuity import (
    CHECKPOINT_SCHEMA,
    build_checkpoint,
    checkpoint_matches,
    checkpoint_needs_refresh,
    pending_messages,
    unsupported_identifiers,
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


def test_checkpoint_triggers_on_tokens_not_on_message_count():
    # tanti messaggi corti in un contesto grande: nessuna compattazione
    assert not should_checkpoint(
        _history(40), context_size=131072, recent_messages=8, min_messages=12,
    )
    # pochi messaggi ma pesanti: si compatta prima di sforare
    pesanti = [dict(m, content=m["content"] + " " + "y" * 3000) for m in _history(14)]
    assert should_checkpoint(
        pesanti, context_size=8192, recent_messages=8, min_messages=12,
    )
    assert not should_checkpoint(
        pesanti[:11], context_size=8192, recent_messages=8, min_messages=12,
    )


def test_after_a_checkpoint_only_uncovered_messages_count():
    pesanti = [dict(m, content=m["content"] + " " + "y" * 3000) for m in _history(14)]
    cp = build_checkpoint(pesanti, summarizer=lambda _: "## Fatti\n" + "ok " * 40,
                          recent_messages=8)
    assert pending_messages(pesanti, cp) == pesanti[6:]
    assert not should_checkpoint(pesanti, context_size=8192, recent_messages=8,
                                 min_messages=12, checkpoint=cp)
    # una storia modificata invalida il checkpoint: si riparte da tutta
    edited = [{"role": "user", "content": "altro"}] + pesanti[1:]
    assert not checkpoint_matches(edited, cp)
    assert pending_messages(edited, cp) == edited


def test_model_summary_citing_unknown_identifiers_is_rejected():
    history = _history(16)
    inventato = ("## Fatti\nModificato `devin/core/inventato.py` e lanciato `pytest -k fantasma`;"
                 " toccato anche file_3.py. " + "dettaglio " * 10)
    cp = build_checkpoint(history, summarizer=lambda _: inventato, recent_messages=8)
    assert cp["generation"] == "deterministic"
    assert "devin/core/inventato.py" in cp["rejected_reason"]
    assert "pytest -k fantasma" in cp["rejected_reason"]
    assert "file_3.py" not in cp["rejected_reason"]

    fedele = "## Fatti\nIl file `file_3.py` e file_5.py sono citati. " + "dettaglio " * 10
    ok = build_checkpoint(history, summarizer=lambda _: fedele, recent_messages=8)
    assert ok["generation"] == "model"
    assert ok["rejected_reason"] is None


def test_summary_over_the_limit_or_failing_is_said_not_truncated():
    lungo = build_checkpoint(_history(16), summarizer=lambda _: "## Fatti\n" + "z" * 900,
                             recent_messages=8, summary_max_chars=500)
    assert lungo["generation"] == "deterministic"
    assert "oltre il limite" in lungo["rejected_reason"]

    def rotto(_):
        raise RuntimeError("tetto raggiunto")

    fallito = build_checkpoint(_history(16), summarizer=rotto, recent_messages=8)
    assert "tetto raggiunto" in fallito["rejected_reason"]


def test_cache_mode_sends_only_the_instruction():
    prompts = []
    build_checkpoint(_history(16), recent_messages=8, transcript_in_prompt=False,
                     summarizer=lambda p: prompts.append(p) or "## Fatti\n" + "a " * 60)
    assert "primi 8 messaggi" in prompts[0]
    assert "message 0:" not in prompts[0]
    assert "Copia ESATTAMENTE" in prompts[0]


def test_unsupported_identifiers_ignores_prose():
    assert unsupported_identifiers("e.g. input/output, v0.4.8, ecc.", "") == []
    assert unsupported_identifiers("vedi https://x.io/a.", "") == ["https://x.io/a"]


def test_fallback_after_a_good_checkpoint_keeps_the_previous_handoff():
    first = build_checkpoint(_history(16), summarizer=lambda _: "## Fatti\nPRIMO HANDOFF " + "a " * 50,
                             recent_messages=8)
    second = build_checkpoint(_history(24), existing=first, summarizer=lambda _: None,
                              recent_messages=8)
    assert second["generation"] == "deterministic"
    assert "PRIMO HANDOFF" in second["summary"]


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

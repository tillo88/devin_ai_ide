"""Tests for the strict training quality gate (2026-07-15).

Covers the fix for the false "ok 3" mini bench: real test discovery/execution
in the scaffold quality gate and case-specific semantic validators that can
downgrade auto_success to auto_failure.
"""

import json
import time
from pathlib import Path

import pytest

from devin.core.orchestrator import Orchestrator
from devin.engine.syntax_critic import check_text
from devin.training.adapters import (
    build_mbpp_gold_test,
    download_mbpp,
    mbpp_cache_path,
    mbpp_rows_to_cases,
)
from devin.training.benchmarks import get_builtin_cases
from devin.training.store import TrainingStore
from devin.training.validators import validate_case, decision_reason


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bare_orchestrator(project_path: Path) -> Orchestrator:
    """Orchestrator senza __init__ (niente modelli/AIClient): bastano i campi
    usati dal quality gate."""
    orch = object.__new__(Orchestrator)
    orch.project_path = str(project_path)
    return orch


def _steam_case(**overrides):
    case = {
        "case_id": "case_test",
        "title": "Official API only",
        "task": "Build a Steam Profile Checker MVP",
        "tags": ["api", "official_sources", "steam"],
        "expected_signals": ["no_invented_endpoint", "tests_or_mocks"],
        "metadata": {"allowed_url_prefixes": ["https://api.steampowered.com/"]},
    }
    case.update(overrides)
    return case


# ---------------------------------------------------------------------------
# test discovery + pytest gate (orchestrator)
# ---------------------------------------------------------------------------

def test_discover_test_files_finds_pytest_style(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    sub = tmp_path / "tests"
    sub.mkdir()
    (sub / "test_more.py").write_text("def test_y():\n    assert True\n", encoding="utf-8")

    orch = _bare_orchestrator(tmp_path)
    found = orch._discover_test_files(tmp_path)
    assert "test_app.py" in found
    assert any(f.startswith("tests") for f in found)


def test_quality_gate_runs_pytest_and_passes(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["calc.py", "test_calc.py"])
    assert gate["tests_run"] is True
    assert gate["test_command"] in {"pytest", "unittest"}
    assert gate["status"] == "verified_success", gate


def test_quality_gate_fails_on_red_suite(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["calc.py", "test_calc.py"])
    assert gate["tests_run"] is True
    assert gate["status"] == "verified_failure"
    assert gate["errors"]


def test_quality_gate_without_tests_is_syntax_only(tmp_path):
    (tmp_path / "app.py").write_text("value = 42\n", encoding="utf-8")
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["app.py"])
    assert gate["tests_run"] is False
    assert gate["status"] == "syntax_only"


# ---------------------------------------------------------------------------
# semantic validators
# ---------------------------------------------------------------------------

def test_validator_flags_invented_endpoint(tmp_path):
    (tmp_path / "checker.py").write_text(
        'URL = "https://api.steamchecker.io/v1/profile"\n', encoding="utf-8"
    )
    result = {"files_written": ["checker.py"], "quality_gate": {"tests_run": False, "errors": []}}
    validation = validate_case(_steam_case(), result, str(tmp_path))
    assert validation["signals"]["no_invented_endpoint"]["verdict"] == "fail"
    assert validation["overall"] == "fail"
    assert "steamchecker" in decision_reason(validation)


def test_validator_accepts_official_endpoint_with_mocks(tmp_path):
    (tmp_path / "checker.py").write_text(
        'URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"\n',
        encoding="utf-8",
    )
    (tmp_path / "test_checker.py").write_text(
        "from unittest.mock import patch\n\ndef test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    result = {
        "files_written": ["checker.py", "test_checker.py"],
        "quality_gate": {"tests_run": True, "errors": [], "test_command": "pytest"},
    }
    validation = validate_case(_steam_case(), result, str(tmp_path))
    assert validation["signals"]["no_invented_endpoint"]["verdict"] == "pass"
    assert validation["signals"]["tests_or_mocks"]["verdict"] == "pass"
    assert validation["overall"] == "pass"


def test_validator_requires_tests_when_expected(tmp_path):
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    case = {
        "case_id": "case_add",
        "expected_signals": ["file_created", "tests_pass"],
        "tags": ["python"],
        "metadata": {},
    }
    # scaffold "riuscito" ma senza alcun test eseguito -> tests_pass deve fallire
    result = {"files_written": ["mod.py"], "quality_gate": {"tests_run": False, "errors": []}}
    validation = validate_case(case, result, str(tmp_path))
    assert validation["signals"]["file_created"]["verdict"] == "pass"
    assert validation["signals"]["tests_pass"]["verdict"] == "fail"
    assert validation["overall"] == "fail"


def test_validator_tag_fallback_allowlist(tmp_path):
    # caso gia' seedato SENZA metadata: deve valere l'allowlist di default per tag steam
    (tmp_path / "checker.py").write_text(
        'URL = "https://steamcommunity.com/id/test"\n', encoding="utf-8"
    )
    case = _steam_case(metadata={})
    result = {"files_written": ["checker.py"], "quality_gate": {"tests_run": False, "errors": []}}
    validation = validate_case(case, result, str(tmp_path))
    assert validation["signals"]["no_invented_endpoint"]["verdict"] == "pass"


def test_validator_mock_word_in_comment_does_not_pass(tmp_path):
    """FIX (2026-07-18): la parola "mock" in un commento/TODO non vale piu'
    come evidenza per tests_or_mocks — serve un import/uso reale di
    unittest.mock in un file .py."""
    (tmp_path / "checker.py").write_text(
        "# TODO: add mocks later\n"
        'URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"\n',
        encoding="utf-8",
    )
    result = {"files_written": ["checker.py"],
              "quality_gate": {"tests_run": False, "errors": []}}
    validation = validate_case(_steam_case(), result, str(tmp_path))
    assert validation["signals"]["tests_or_mocks"]["verdict"] == "fail"
    assert "unittest.mock" in validation["signals"]["tests_or_mocks"]["detail"]
    assert validation["overall"] == "fail"


def test_validator_mock_word_in_readme_does_not_pass(tmp_path):
    """Variante doc-only: README che parla di mock ma nessun .py li usa."""
    (tmp_path / "README.md").write_text(
        "This client is fully tested with mock responses.\n", encoding="utf-8")
    (tmp_path / "checker.py").write_text(
        'URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"\n',
        encoding="utf-8",
    )
    result = {"files_written": ["README.md", "checker.py"],
              "quality_gate": {"tests_run": False, "errors": []}}
    validation = validate_case(_steam_case(), result, str(tmp_path))
    assert validation["signals"]["tests_or_mocks"]["verdict"] == "fail"


def test_validator_real_unittest_mock_usage_passes(tmp_path):
    """Controparte positiva: vero `from unittest.mock import patch` + uso in
    un file .py -> tests_or_mocks passa anche senza test eseguiti."""
    (tmp_path / "checker.py").write_text(
        'URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"\n',
        encoding="utf-8",
    )
    (tmp_path / "test_checker.py").write_text(
        "from unittest.mock import patch\n\n"
        "def test_ok():\n"
        "    with patch('checker.URL', 'https://api.steampowered.com/'):\n"
        "        assert True\n",
        encoding="utf-8",
    )
    result = {"files_written": ["checker.py", "test_checker.py"],
              "quality_gate": {"tests_run": False, "errors": []}}
    validation = validate_case(_steam_case(), result, str(tmp_path))
    assert validation["signals"]["tests_or_mocks"]["verdict"] == "pass"
    assert validation["overall"] == "pass"


def test_validator_unknown_signal_left_to_reviewer(tmp_path):
    case = {
        "case_id": "case_dbg",
        "expected_signals": ["tests_fail_first"],
        "tags": [],
        "metadata": {},
    }
    result = {"files_written": [], "quality_gate": {}}
    validation = validate_case(case, result, str(tmp_path))
    assert validation["signals"]["tests_fail_first"]["verdict"] == "not_machine_checkable"
    assert validation["overall"] == "unknown"


def test_validator_fails_on_written_but_unreadable_files(tmp_path):
    """File dichiarati scritti ma mancanti/illeggibili: prima sparivano in
    silenzio (file_created passava sul sottoinsieme leggibile). Ora sono
    evidenza esplicita e fanno fallire file_created."""
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    case = {
        "case_id": "case_partial",
        "expected_signals": ["file_created"],
        "tags": ["python"],
        "metadata": {},
    }
    result = {"files_written": ["mod.py", "ghost.py"], "quality_gate": {}}
    validation = validate_case(case, result, str(tmp_path))
    assert validation["signals"]["file_created"]["verdict"] == "fail"
    assert "ghost.py" in validation["signals"]["file_created"]["detail"]
    assert validation["skipped_files"] == ["ghost.py"]
    assert validation["overall"] == "fail"


# ---------------------------------------------------------------------------
# add_attempt status validation (2026-07-18)
# ---------------------------------------------------------------------------

def test_add_attempt_rejects_unknown_status(tmp_path):
    """Un typo ("auto_succes") prima veniva accettato e l'attempt spariva da
    review_queue e summary. Ora add_attempt valida come add_review."""
    store = TrainingStore(tmp_path)
    with pytest.raises(ValueError, match="unsupported attempt status"):
        store.add_attempt(case_id="manual", status="auto_succes")
    assert store.list_attempts() == []  # niente record scritto


def test_add_attempt_accepts_all_canonical_statuses(tmp_path):
    from devin.training.store import ATTEMPT_STATUSES
    store = TrainingStore(tmp_path)
    assert ATTEMPT_STATUSES == {
        "verified_success", "human_confirmed",
        "verified_failure", "failed", "needs_correction",
        "auto_success", "auto_failure", "runner_error", "pending_review",
    }
    for status in sorted(ATTEMPT_STATUSES):
        attempt = store.add_attempt(case_id="manual", status=status)
        assert attempt["status"] == status
    assert len(store.list_attempts()) == len(ATTEMPT_STATUSES)


# ---------------------------------------------------------------------------
# Teacher review queue
# ---------------------------------------------------------------------------

def test_review_queue_lists_only_unreviewed_auto_attempts(tmp_path):
    store = TrainingStore(tmp_path)
    case = store.add_case(task="Build X", title="Case X",
                          expected_signals=["tests_pass"], source="devin-mini")
    a1 = store.add_attempt(
        case_id=case["case_id"], status="auto_success", run_id="run_1",
        tests={
            "quality_gate": {"status": "verified_success", "tests_run": True,
                             "test_command": "pytest", "errors": []},
            "validators": {"overall": "pass",
                           "signals": {"tests_pass": {"verdict": "pass", "detail": "ok"}}},
        },
    )
    a2 = store.add_attempt(case_id=case["case_id"], status="auto_failure",
                           error_reason="validatori caso: endpoint fuori allowlist")
    a3 = store.add_attempt(case_id=case["case_id"], status="verified_success")
    store.add_review(attempt_id=a2["attempt_id"], status="verified_failure",
                     rationale="confermato a mano")

    queue = store.review_queue()
    ids = [item["attempt_id"] for item in queue]
    assert a1["attempt_id"] in ids
    assert a2["attempt_id"] not in ids  # gia' reviewato: fuori coda
    assert a3["attempt_id"] not in ids  # non auto_*: fuori coda

    entry = next(item for item in queue if item["attempt_id"] == a1["attempt_id"])
    assert entry["gate"]["test_command"] == "pytest"
    assert entry["validators"]["signals"]["tests_pass"] == "pass"
    assert entry["expected_signals"] == ["tests_pass"]
    assert entry["title"] == "Case X"


def test_review_queue_newest_first_and_includes_runner_error(tmp_path):
    store = TrainingStore(tmp_path)
    a1 = store.add_attempt(case_id="manual", status="runner_error", error_reason="boom")
    a2 = store.add_attempt(case_id="manual", status="auto_success")
    queue = store.review_queue()
    assert [item["attempt_id"] for item in queue] == [a2["attempt_id"], a1["attempt_id"]]


def test_review_queue_keeps_attempt_with_pending_review_review(tmp_path):
    """FIX (2026-07-18): una review pending_review (presa in carico, NESSUN
    verdetto) non svuota piu' la coda — l'attempt resta in attesa di un
    vero verdetto."""
    store = TrainingStore(tmp_path)
    attempt = store.add_attempt(case_id="manual", status="auto_success")
    store.add_review(attempt_id=attempt["attempt_id"], status="pending_review",
                     rationale="presa in carico, verdict in arrivo")
    ids = [item["attempt_id"] for item in store.review_queue()]
    assert attempt["attempt_id"] in ids


def test_review_queue_verdict_review_clears_attempt(tmp_path):
    """Controparte: pending_review seguita da una review con verdetto ->
    l'attempt esce dalla coda."""
    store = TrainingStore(tmp_path)
    attempt = store.add_attempt(case_id="manual", status="auto_success")
    store.add_review(attempt_id=attempt["attempt_id"], status="pending_review")
    store.add_review(attempt_id=attempt["attempt_id"], status="verified_success",
                     rationale="verificato a mano")
    ids = [item["attempt_id"] for item in store.review_queue()]
    assert attempt["attempt_id"] not in ids


# ---------------------------------------------------------------------------
# gold tests + supersede al reseed
# ---------------------------------------------------------------------------

def test_builtin_cases_carry_gold_tests():
    cases = get_builtin_cases("devin-mini")
    assert len(cases) == 3
    for case in cases:
        gold = case.get("gold_tests") or {}
        assert gold, f"caso senza gold tests: {case['title']}"
        for name, content in gold.items():
            assert name.startswith("test_gold_") and name.endswith(".py")
            compile(content, name, "exec")  # deve essere Python valido


def test_seed_supersedes_same_title_new_task(tmp_path):
    store = TrainingStore(tmp_path)
    first = store.seed_cases([{"title": "T", "prompt": "v1"}], source="local")
    again = store.seed_cases([{"title": "T", "prompt": "v1"}], source="local")
    assert len(first) == 1 and not again  # idempotente come prima

    updated = store.seed_cases(
        [{"title": "T", "prompt": "v2",
          "gold_tests": {"test_gold_t.py": "def test_t():\n    assert True\n"}}],
        source="local")
    assert len(updated) == 1

    active_t = [c for c in store.list_cases() if c.get("title") == "T"]
    assert [c["task"] for c in active_t] == ["v2"]  # il v1 e' ritirato
    assert active_t[0]["metadata"]["gold_tests"]

    all_t = [c for c in store.list_cases(include_retired=True) if c.get("title") == "T"]
    assert len(all_t) == 2  # lo storico resta


def test_gold_test_catches_wrong_implementation(tmp_path):
    case = get_builtin_cases("devin-mini")[1]  # Fix off-by-one
    for name, content in case["gold_tests"].items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    # implementazione SBAGLIATA: include l'endpoint
    (tmp_path / "ranges.py").write_text(
        "def count_up_to(n):\n    return list(range(n + 1))\n", encoding="utf-8")
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["ranges.py"])
    assert gate["tests_run"] is True
    assert gate["status"] == "verified_failure"


def test_gold_test_passes_correct_implementation(tmp_path):
    case = get_builtin_cases("devin-mini")[0]  # add
    for name, content in case["gold_tests"].items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "mymath.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["mymath.py"])
    assert gate["status"] == "verified_success", gate


# ---------------------------------------------------------------------------
# adapter MBPP (senza rete)
# ---------------------------------------------------------------------------

def test_mbpp_conversion_and_gold_integration(tmp_path):
    rows = [{
        "task_id": 2,
        "text": "Find similar elements from the given two tuple lists.",
        "test_list": ["assert similar_elements((1, 2), (2, 3)) == (2,)"],
        "test_setup_code": "",
    }]
    cases = mbpp_rows_to_cases(rows, limit=5)
    assert len(cases) == 1
    case = cases[0]
    assert case["tags"] == ["python", "mbpp"]
    assert case["expected_signals"] == ["tests_pass"]
    assert "Reference tests" in case["prompt"]
    (name, content), = case["gold_tests"].items()
    compile(content, name, "exec")

    # integrazione: implementazione giusta -> gate verde coi soli gold test
    (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "sol.py").write_text(
        "def similar_elements(a, b):\n    return tuple(sorted(set(a) & set(b)))\n",
        encoding="utf-8")
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["sol.py"])
    assert gate["status"] == "verified_success", gate


def test_mbpp_download_uses_valid_cache_without_network(tmp_path):
    cache = mbpp_cache_path(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_text("\n".join(
        json.dumps({"task_id": i, "text": f"t{i}", "test_list": ["assert True"]})
        for i in range(950)), encoding="utf-8")
    info = download_mbpp(tmp_path)  # cache valida: niente rete
    assert info["downloaded"] is False
    assert info["rows"] == 950


# ---------------------------------------------------------------------------
# cross-chat (epic Progetti): guardare le altre conversazioni su richiesta
# ---------------------------------------------------------------------------

def _make_chat(ps, title, messages):
    import json as _json
    ps.chats_dir.mkdir(parents=True, exist_ok=True)
    cid = f"chat_{title.lower()}"
    (ps.chats_dir / f"{cid}.json").write_text(
        _json.dumps({"title": title, "history": messages}), encoding="utf-8")
    return cid


def test_search_chats_finds_relevant_snippets(tmp_path):
    from devin.core.project_space import ProjectSpace
    ps = ProjectSpace(str(tmp_path))
    _make_chat(ps, "Auth", [{"role": "user", "content": "usiamo JWT per il login e refresh token"}])
    _make_chat(ps, "UI", [{"role": "user", "content": "il bottone va spostato a destra"}])
    hits = ps.search_chats("come gestiamo il login jwt")
    assert hits and hits[0]["chat_title"] == "Auth"
    assert "JWT" in hits[0]["snippet"]
    # una query non pertinente non pesca nulla
    assert ps.search_chats("ricetta della carbonara") == []


def test_search_chats_excludes_current(tmp_path):
    from devin.core.project_space import ProjectSpace
    ps = ProjectSpace(str(tmp_path))
    cur = _make_chat(ps, "Corrente", [{"role": "user", "content": "parliamo di database sqlite"}])
    _make_chat(ps, "Altra", [{"role": "user", "content": "database sqlite indicizzato"}])
    hits = ps.search_chats("database sqlite", exclude_chat_id=cur)
    assert all(h["chat_id"] != cur for h in hits)
    assert any(h["chat_title"] == "Altra" for h in hits)


def test_cross_chat_context_and_intent(tmp_path):
    from devin.core.project_space import ProjectSpace
    from devin.ui.fast_app import _wants_cross_chat
    ps = ProjectSpace(str(tmp_path))
    _make_chat(ps, "Deploy", [{"role": "assistant", "content": "il deploy usa docker compose sul rig"}])
    ctx = ps.build_cross_chat_context("cosa avevamo detto sul deploy docker")
    assert "ALTRE CHAT" in ctx and "docker compose" in ctx
    assert _wants_cross_chat("cosa avevamo detto nell'altra chat?") is True
    assert _wants_cross_chat("scrivi una funzione add") is False


# ---------------------------------------------------------------------------
# fetch smart (crawl4ai-first + escalation) e youtube transcript
# ---------------------------------------------------------------------------

def test_fetch_page_smart_escalation(monkeypatch):
    import devin.ai.web_search as ws
    order = []
    # crawl4ai torna troppo poco -> deve scendere a requests
    monkeypatch.setattr(ws, "_fetch_crawl4ai", lambda u, m: order.append("crawl") or "shell")
    monkeypatch.setattr(ws, "fetch_page_text",
                        lambda u, max_chars=2500, timeout=10: order.append("req") or ("X" * 400))
    text = ws.fetch_page_smart("https://docs.example.com")
    assert order == ["crawl", "req"] and len(text) >= 200


def test_fetch_page_smart_uses_crawl4ai_when_rich(monkeypatch):
    import devin.ai.web_search as ws
    monkeypatch.setattr(ws, "_fetch_crawl4ai", lambda u, m: "Y" * 500)
    called = {"req": False}
    monkeypatch.setattr(ws, "fetch_page_text",
                        lambda *a, **k: called.__setitem__("req", True) or "")
    text = ws.fetch_page_smart("https://docs.example.com")
    assert len(text) >= 200 and called["req"] is False  # niente fallback se crawl ricco


def test_youtube_extract_id():
    from devin.ai.youtube_tools import extract_video_id
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=10") == "dQw4w9WgXcQ"
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("non-un-video") == ""


def test_youtube_transcript_failsoft_without_pkg(monkeypatch):
    import devin.ai.youtube_tools as yt
    monkeypatch.setattr(yt, "youtube_transcript_available", lambda: False)
    out = yt.get_transcript("https://youtu.be/dQw4w9WgXcQ")
    assert out["text"] == "" and "non installato" in out["error"]
    assert out["video_id"] == "dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# docs cache locale (anti endpoint inventati)
# ---------------------------------------------------------------------------

def test_docs_cache_add_match_and_context(tmp_path):
    from devin.core.docs_cache import DocsCache
    cache = DocsCache(tmp_path)
    cache.add_doc(
        title="Steam Web API",
        content="Base URL: https://api.steampowered.com\nGetPlayerSummaries v0002",
        keys=["steam", "getplayersummaries"],
        source_url="https://developer.valvesoftware.com/wiki/Steam_Web_API",
    )
    # match per chiave presente nel task
    assert cache.match("build a steam profile checker")
    assert not cache.match("sort a list of integers")
    ctx = cache.build_context("steam profile checker mvp")
    assert "api.steampowered.com" in ctx
    assert "DOCUMENTAZIONE UFFICIALE" in ctx
    # persistenza: nuova istanza vede la doc
    assert len(DocsCache(tmp_path).list_docs()) == 1


def test_docs_cache_remove_and_empty_context(tmp_path):
    from devin.core.docs_cache import DocsCache
    cache = DocsCache(tmp_path)
    entry = cache.add_doc(title="Foo Lib", content="foo() does bar", keys=["foolib"])
    assert cache.build_context("uso foolib qui") != ""
    assert cache.remove_doc(entry["slug"]) is True
    assert cache.build_context("uso foolib qui") == ""
    assert cache.remove_doc("inesistente") is False


def test_docs_cache_internet_first_live_fetch(tmp_path):
    # cache vuota + fetcher live: deve scaricare, cachare (TTL) e usare
    from devin.core.docs_cache import DocsCache
    cache = DocsCache(tmp_path)
    calls = {"n": 0}

    def fake_fetch(q):
        calls["n"] += 1
        return "Base URL ufficiale: https://api.example.com/v1"

    ctx = cache.resolve_context("build a widget client", web_fetcher=fake_fetch)
    assert "api.example.com" in ctx and calls["n"] == 1
    # seconda volta: cache fresca, NIENTE re-fetch
    ctx2 = cache.resolve_context("build a widget client", web_fetcher=fake_fetch)
    assert "api.example.com" in ctx2 and calls["n"] == 1


def test_docs_cache_offline_falls_back_to_pinned(tmp_path):
    from devin.core.docs_cache import DocsCache
    cache = DocsCache(tmp_path)
    cache.add_doc(title="Steam Web API", content="https://api.steampowered.com",
                  keys=["steam"], source="pinned")

    def boom(q):
        raise AssertionError("non deve fetchare quando allow_web=False")

    ctx = cache.resolve_context("steam profile checker", web_fetcher=boom, allow_web=False)
    assert "api.steampowered.com" in ctx


def test_docs_cache_prunes_expired_web(tmp_path):
    import time as _t
    from devin.core.docs_cache import DocsCache
    cache = DocsCache(tmp_path)
    cache.add_doc(title="Old Web Doc", content="stantio", keys=["oldweb"], source="web")
    cache.add_doc(title="Pinned Doc", content="curato", keys=["pinnedk"], source="pinned")
    # ttl 0 -> la web scade subito, la pinned resta
    pruned = cache.prune_expired(ttl_s=0)
    assert pruned == 1
    slugs = {d["slug"] for d in cache.list_docs()}
    assert "pinned-doc" in slugs and "old-web-doc" not in slugs


# ---------------------------------------------------------------------------
# loop runner (goal + verifica + stop, roadmap "loop mode")
# ---------------------------------------------------------------------------

def test_loop_stops_on_success_streak():
    from devin.core.loop_runner import run_loop, VerifyResult
    calls = {"n": 0}

    def action(i, last):
        calls["n"] = i
        return i

    # verde dalla 3a iterazione, serve streak 1
    def verifier(res):
        return VerifyResult(res >= 3, f"val={res}")

    outcome = run_loop(action, verifier, max_iterations=5, success_streak=1)
    assert outcome.success and outcome.reason == "streak_reached"
    assert outcome.iterations == 3 and calls["n"] == 3


def test_loop_respects_max_iterations():
    from devin.core.loop_runner import run_loop, VerifyResult
    outcome = run_loop(lambda i, last: i, lambda r: VerifyResult(False, "mai"),
                       max_iterations=4)
    assert not outcome.success and outcome.reason == "max_iterations"
    assert outcome.iterations == 4 and len(outcome.steps) == 4


def test_loop_streak_requires_consecutive():
    from devin.core.loop_runner import run_loop, VerifyResult
    seq = iter([True, False, True, True])  # streak 2 solo alla fine

    def verifier(_):
        return VerifyResult(next(seq))

    outcome = run_loop(lambda i, last: i, verifier, max_iterations=4, success_streak=2)
    assert outcome.success and outcome.iterations == 4


def test_loop_cooperative_stop():
    from devin.core.loop_runner import run_loop, VerifyResult
    outcome = run_loop(lambda i, last: i, lambda r: VerifyResult(False),
                       max_iterations=10, should_stop=lambda: True)
    assert not outcome.success and outcome.reason == "stopped"


def test_loop_time_budget_stop():
    from devin.core.loop_runner import run_loop, VerifyResult
    calls = {"n": 0}

    def slow_action(i, last):
        calls["n"] += 1
        time.sleep(0.05)
        return i

    outcome = run_loop(slow_action, lambda r: VerifyResult(False),
                       max_iterations=100, time_budget_s=0.01)
    assert not outcome.success and outcome.reason == "time_budget"
    assert calls["n"] == 1  # budget controllato TRA un giro e l'altro


def test_loop_clamps_and_reports_steps():
    from devin.core.loop_runner import run_loop, VerifyResult
    outcome = run_loop(lambda i, last: i, lambda r: VerifyResult(True, "ok"),
                       max_iterations=0, success_streak=0)  # clampati a 1
    assert outcome.success and outcome.iterations == 1
    assert len(outcome.steps) == 1 and outcome.steps[0].ok and outcome.steps[0].detail == "ok"


def test_loop_on_step_exception_tolerated():
    from devin.core.loop_runner import run_loop, VerifyResult

    def bad_callback(step):
        raise RuntimeError("callback rotta")

    outcome = run_loop(lambda i, last: i, lambda r: VerifyResult(True),
                       max_iterations=3, on_step=bad_callback)
    assert outcome.success  # la telemetria rotta non deve mai fermare il loop


def test_loop_action_receives_previous_verify():
    from devin.core.loop_runner import run_loop, VerifyResult
    seen = []

    def action(i, last):
        seen.append(last.detail if last else None)
        return i

    outcome = run_loop(action, lambda r: VerifyResult(r >= 2, f"giro-{r}"),
                       max_iterations=3)
    assert seen == [None, "giro-1"]  # feedback del giro precedente, poi streak stop
    assert outcome.success and outcome.iterations == 2


def test_scaffold_heal_loop_fixes_red_suite(tmp_path):
    # implementazione SBAGLIATA + test del "modello": il gate parte rosso,
    # il coder finto la corregge alla 1a iterazione del self-heal.
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
    orch = _bare_orchestrator(tmp_path)
    orch._should_stop = False
    orch._log = lambda *a, **k: None

    class FakeCoder:
        def generate_file(self, fname, spec, project_context=""):
            return "def add(a, b):\n    return a + b\n"  # corretta

    orch.coder = FakeCoder()
    initial = orch._scaffold_quality_gate(["calc.py", "test_calc.py"])
    assert initial["status"] == "verified_failure"
    healed = orch._scaffold_heal_loop(
        initial, ["calc.py"], {"calc.py": "somma"}, "", max_iterations=2)
    assert healed["status"] == "verified_success", healed
    assert (tmp_path / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


# ---------------------------------------------------------------------------
# repo map (firme per il contesto 8K, roadmap)
# ---------------------------------------------------------------------------

def test_repo_map_lists_signatures_and_respects_budget():
    from devin.core.repo_map import build_repo_map_from_files
    files = [
        {"rel_path": "calc.py",
         "content": "class Calculator:\n    def press(self, key):\n        pass\n\n"
                    "def main():\n    pass\n"},
        {"rel_path": "util.py", "content": "def helper(a, b=1):\n    return a\n"},
        {"rel_path": "README.md", "content": "# doc"},
    ]
    text = build_repo_map_from_files(files)
    assert "REPO MAP" in text
    assert "class Calculator(press)" in text
    assert "def main()" in text
    assert "def helper(a, b=1)" in text
    assert "README.md" in text
    long_files = [{"rel_path": f"m{i}.py", "content": f"def f{i}(x):\n    pass\n"} for i in range(300)]
    capped = build_repo_map_from_files(long_files, max_chars=500)
    assert len(capped) < 700 and "troncata" in capped


def test_context_engine_prepends_repo_map(tmp_path):
    from devin.core.context_engine import ContextEngine
    (tmp_path / "alpha.py").write_text("def alpha_fn(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def beta_fn(y):\n    return y\n", encoding="utf-8")
    engine = ContextEngine()
    context = engine.build(str(tmp_path))
    assert "REPO MAP" in context
    assert "alpha_fn" in context and "beta_fn" in context


# ---------------------------------------------------------------------------
# security critic offline (bandit, roadmap punto 2)
# ---------------------------------------------------------------------------

def test_security_critic_flags_shell_injection(tmp_path):
    pytest.importorskip("bandit")
    from devin.engine.security_critic import scan_python_files
    (tmp_path / "danger.py").write_text(
        "import subprocess\n\n"
        "def run(cmd):\n"
        "    return subprocess.call(cmd, shell=True)\n",
        encoding="utf-8",
    )
    warnings = scan_python_files(tmp_path, ["danger.py"])
    assert warnings, "shell=True dovrebbe produrre un finding MEDIUM+"
    assert any("danger.py" in w for w in warnings)


def test_security_warnings_do_not_fail_gate(tmp_path):
    pytest.importorskip("bandit")
    (tmp_path / "danger.py").write_text(
        "import subprocess\n\n"
        "def run(cmd):\n"
        "    return subprocess.call(cmd, shell=True)\n",
        encoding="utf-8",
    )
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["danger.py"])
    # policy anti-rumore: warning allegati come evidenza, status NON bocciato
    assert gate["security_warnings"]
    assert gate["status"] == "syntax_only"


def test_security_critic_fails_open_without_bandit(monkeypatch, tmp_path):
    import devin.engine.security_critic as sc
    monkeypatch.setattr(sc, "_AVAILABLE", False)
    assert sc.scan_python_files(tmp_path, ["x.py"]) == []


def test_security_critic_skips_our_gold_tests(tmp_path):
    pytest.importorskip("bandit")
    # gold test NOSTRO che usa exec: non deve produrre finding (falso positivo)
    (tmp_path / "test_gold_mbpp_9.py").write_text(
        "def test_g():\n    exec('assert 1==1', {})\n", encoding="utf-8")
    from devin.engine.security_critic import scan_python_files
    assert scan_python_files(tmp_path, ["test_gold_mbpp_9.py"]) == []


# ---------------------------------------------------------------------------
# syntax critic multi-linguaggio (tree-sitter, roadmap punto 1)
# ---------------------------------------------------------------------------

def test_syntax_critic_python_and_json_native():
    assert check_text("ok.py", "x = 1\n") == {"language": "python", "checked": True, "errors": []}
    bad_py = check_text("bad.py", "def broken(:\n")
    assert bad_py["checked"] and bad_py["errors"]
    assert check_text("ok.json", '{"a": 1}')["errors"] == []
    bad_json = check_text("bad.json", '{"a": }')
    assert bad_json["checked"] and bad_json["errors"]


def test_syntax_critic_unknown_language_fails_open():
    verdict = check_text("data.xyz", "@@@non-un-linguaggio@@@")
    assert verdict["checked"] is False
    assert verdict["errors"] == []


def test_quality_gate_catches_broken_json(tmp_path):
    # niente dipendenza tree-sitter: il json e' verificato nativamente
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "config.json").write_text('{"port": 8000,,}\n', encoding="utf-8")
    orch = _bare_orchestrator(tmp_path)
    gate = orch._scaffold_quality_gate(["app.py", "config.json"])
    assert gate["status"] == "verified_failure"
    assert any("config.json" in e for e in gate["errors"])


def test_syntax_critic_javascript_when_available():
    pytest.importorskip("tree_sitter_language_pack")
    good = check_text("app.js", "function add(a, b) { return a + b; }\n")
    assert good["checked"] is True and good["errors"] == []
    bad = check_text("app.js", "function add(a, b { return a + b; }\n")
    assert bad["checked"] is True and bad["errors"], bad


def test_mbpp_gold_builder_handles_setup_code():
    content = build_mbpp_gold_test(7, "import math", ["assert math.floor(1.5) == 1"])
    compile(content, "test_gold_mbpp_7.py", "exec")
    assert "exec('import math', ns)" in content or 'exec("import math", ns)' in content


# ---------------------------------------------------------------------------
# close_cleanup: auto-stop del backend (opt-in, mai col rig self-hosted)
# ---------------------------------------------------------------------------

def _cleanup_env(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from devin.ui import fast_app

    class FakeLauncher:
        def get_status(self):
            return SimpleNamespace(local_running={}, model_source="unavailable")

        def shutdown_all(self):
            pass

    fired = {}

    class FakeTimer:
        def __init__(self, interval, fn, args=None):
            fired["scheduled"] = (interval, fn)

        def start(self):
            fired["started"] = True

    monkeypatch.setattr(fast_app, "_get_launcher", lambda: FakeLauncher())
    monkeypatch.setattr(fast_app, "_known_local_model_servers", lambda: {})
    monkeypatch.setattr(fast_app, "_shutdown_known_local_model_servers", lambda: [])
    monkeypatch.setattr(fast_app.threading, "Timer", FakeTimer)
    monkeypatch.delenv("DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS", raising=False)
    return asyncio, fast_app, fired


def test_close_cleanup_backend_stop_is_optin(monkeypatch):
    asyncio, fast_app, fired = _cleanup_env(monkeypatch)
    monkeypatch.setattr(fast_app, "_rig_self_hosted", lambda: False)

    # senza opt-in (default, sviluppo/pytest): il backend resta VIVO
    monkeypatch.delenv("DEVIN_DESKTOP_CLOSE_STOPS_BACKEND", raising=False)
    result = asyncio.run(fast_app.api_desktop_close_cleanup())
    assert result["backend"] == "kept"
    assert "started" not in fired

    # con opt-in (launcher desktop): il backend si spegne dopo la risposta
    monkeypatch.setenv("DEVIN_DESKTOP_CLOSE_STOPS_BACKEND", "1")
    result = asyncio.run(fast_app.api_desktop_close_cleanup())
    assert result["backend"] == "stopping"
    assert fired.get("started")


def test_close_cleanup_never_stops_rig_selfhosted_backend(monkeypatch):
    asyncio, fast_app, fired = _cleanup_env(monkeypatch)
    monkeypatch.setattr(fast_app, "_rig_self_hosted", lambda: True)
    monkeypatch.setenv("DEVIN_DESKTOP_CLOSE_STOPS_BACKEND", "1")
    result = asyncio.run(fast_app.api_desktop_close_cleanup())
    assert result["backend"] == "kept"
    assert "started" not in fired

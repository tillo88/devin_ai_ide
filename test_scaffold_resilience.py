import pytest
from types import SimpleNamespace

from devin.agents.planner import Planner, _extract_file_plan
from devin.core.orchestrator import Orchestrator, _is_test_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            'Premessa\n```json\n[{"filename":"app.py","spec":"entrypoint"}]\n```\nFine',
            [{"filename": "app.py", "spec": "entrypoint"}],
        ),
        (
            '{"files":[{"path":"src/main.py","description":"main module"}]}',
            [{"filename": "src/main.py", "spec": "main module"}],
        ),
        (
            "[{'filename': 'README.md', 'purpose': 'docs',}]",
            [{"filename": "README.md", "spec": "docs"}],
        ),
    ],
)
def test_extract_file_plan_accepts_common_local_llm_formats(raw, expected):
    assert _extract_file_plan(raw) == expected


@pytest.mark.parametrize("filename", ["../escape.py", "/tmp/escape.py", "C:\\escape.py", "."])
def test_extract_file_plan_rejects_unsafe_paths(filename):
    raw = f'[{{"filename":{filename!r},"spec":"unsafe"}}]'
    assert _extract_file_plan(raw) == []


def test_plan_scaffold_retries_with_strategy_change():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def local(self, messages, mode, timeout):
            self.calls.append((messages, mode, timeout))
            if len(self.calls) == 1:
                return "I would create app.py and README.md."
            return '[{"filename":"app.py","spec":"minimal entrypoint"}]'

    client = FakeClient()
    planner = Planner(client)
    assert planner.plan_scaffold("create a tiny app") == [
        {"filename": "app.py", "spec": "minimal entrypoint"}
    ]
    assert len(client.calls) == 2
    assert client.calls[0][2] == client.calls[1][2] == 150
    assert [item["attempt"] for item in planner.last_scaffold_attempts] == [1, 2]


def test_serialized_swap_releases_peer_then_ensures_needed(monkeypatch):
    events = []

    class Launcher:
        def release_alias(self, alias):
            events.append(("release", alias))
            return True

        def ensure_alias(self, alias):
            events.append(("ensure", alias))
            return True

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.serialize_vram = True
    orchestrator._degraded_mode = True
    orchestrator.vram_swap_threshold_mb = 2048
    orchestrator.model_launcher = Launcher()
    orchestrator._log = lambda *args, **kwargs: None

    monkeypatch.setattr("devin.core.orchestrator.get_vram_status", lambda: {"free_mb": 9000})
    monkeypatch.setattr("devin.core.orchestrator.time.sleep", lambda _: None)

    assert orchestrator._check_vram_and_swap("coder", "planner") is True
    assert events == [("release", "planner"), ("ensure", "coder")]


def test_planner_diagnostics_are_redacted_and_truncated():
    text = "api_key=super-secret\n" + ("x" * 2000)
    excerpt = Orchestrator._planner_diagnostic_excerpt(text, max_chars=80)
    assert "super-secret" not in excerpt
    assert "<redacted>" in excerpt
    assert "\n" not in excerpt
    assert len(excerpt) <= 81


def test_scaffold_quality_gate_blocks_failed_generated_tests(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests.py").write_text("raise SystemExit(1)\n", encoding="utf-8")

    class FailedRunner:
        def run(self, *args, **kwargs):
            return type("Result", (), {"success": False, "error": "1 failed"})()

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_path = str(tmp_path)
    orchestrator.runner = FailedRunner()

    quality = orchestrator._scaffold_quality_gate(["app.py", "tests.py"])
    assert quality["status"] == "verified_failure"
    assert quality["tests_run"] is True
    assert "1 failed" in quality["errors"][0]


def test_scaffold_quality_gate_marks_no_tests_syntax_only(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_path = str(tmp_path)
    orchestrator.runner = object()

    quality = orchestrator._scaffold_quality_gate(["app.py"])
    assert quality["status"] == "syntax_only"
    assert quality["tests_run"] is False


def test_scaffold_event_status_reflects_evidence_tier():
    """La timeline distingue success verificato da consegna senza test."""
    from devin.ui.fast_app import _scaffold_event_status

    assert _scaffold_event_status({"success": False}) == "failed"
    assert _scaffold_event_status(
        {"success": False, "status": "awaiting_approval", "verified": True}
    ) == "awaiting_approval"
    assert _scaffold_event_status(
        {"success": True, "quality_gate": {"status": "verified_success"}}
    ) == "verified_success"
    assert _scaffold_event_status(
        {"success": True, "quality_gate": {"status": "syntax_only"}}
    ) == "syntax_only"
    # quality_gate mancante del tutto -> mai spacciare per verificato
    assert _scaffold_event_status({"success": True}) == "syntax_only"


def test_unverified_scaffold_is_not_stored_in_memory(tmp_path):
    class Memory:
        def store(self, *args, **kwargs):
            raise AssertionError("unverified memory must not be stored")

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_path = str(tmp_path)
    orchestrator.memory_client = Memory()
    assert orchestrator._remember_scaffold_outcome(
        "task", {"status": "syntax_only", "errors": []}, ["app.py"]
    ) == "not_recorded"


def test_verified_failure_memory_keeps_negative_status(tmp_path):
    captured = {}

    class Memory:
        def store_local(self, content, **kwargs):
            captured["content"] = content
            captured.update(kwargs)
            return "local_stored"

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_path = str(tmp_path / "Steam Profile Checker")
    orchestrator.memory_client = Memory()
    outcome = orchestrator._remember_scaffold_outcome(
        "build checker",
        {"status": "verified_failure", "errors": ["3 tests failed"]},
        ["main.py"],
    )
    assert outcome == "local_stored"
    assert "do not repeat" in captured["content"]
    assert "status:verified_failure" in captured["tags"]
    assert "polarity:negative" in captured["tags"]
    assert "visibility:local" in captured["tags"]
    assert "promotion:manual_required" in captured["tags"]


def test_fallback_memory_envelope_preserves_failure_semantics():
    from devin.ai.hybrid_memory_client import _fallback_envelope

    text = _fallback_envelope(
        "wrong API choice",
        ["status:verified_failure", "polarity:negative", "evidence:test_failure"],
    )
    assert "status=verified_failure" in text
    assert "polarity=negative" in text
    assert "wrong API choice" in text


def test_official_source_request_is_fail_closed():
    from devin.ui.fast_app import _requires_verified_web_sources

    assert _requires_verified_web_sources(
        "Usa esclusivamente documentazione ufficiale e non inventare endpoint"
    )
    assert not _requires_verified_web_sources("crea una calcolatrice locale")



def test_local_memory_store_recalls_verified_failure_but_not_pending(tmp_path):
    from devin.ai.hybrid_memory_client import LocalMemoryStore

    store = LocalMemoryStore({"local_memory": {"path": str(tmp_path / "mem.jsonl")}})
    store.store(
        "Steam checker used GitHub instead of Steam official docs",
        tags=["project:steam", "status:verified_failure", "polarity:negative"],
        importance=0.9,
    )
    store.store(
        "Unreviewed draft should stay hidden",
        tags=["project:steam", "status:pending_review"],
        importance=1.0,
    )

    memories = store.recall("Steam official docs", tags=["project:steam"], limit=5)
    assert len(memories) == 1
    assert "status=verified_failure" in memories[0]
    assert "GitHub instead of Steam" in memories[0]
    assert "Unreviewed" not in memories[0]


def test_hybrid_store_local_does_not_call_shared_backends(tmp_path):
    from devin.ai.hybrid_memory_client import HybridMemoryClient

    client = HybridMemoryClient({
        "local_memory": {"path": str(tmp_path / "mem.jsonl")},
        "understory": {"enabled": False},
        "automem": {"enabled": False},
    })
    assert client.store_local(
        "local only",
        tags=["status:verified_success", "visibility:local"],
    ) == "local_stored"
    assert client.local.count() == 1



def test_runtime_diagnostics_detects_cuda_fallback(monkeypatch, tmp_path):
    from devin.ai import local_model_launcher as lml

    (tmp_path / "llama-server-coder.log").write_text(
        "ggml_cuda_init: failed to initialize CUDA: CUDA driver version is insufficient\n"
        "warning: no usable GPU found, --gpu-layers option will be ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lml, "LOG_DIR", tmp_path)
    diag = lml.get_runtime_diagnostics("coder")
    assert diag["gpu_acceleration"] is False
    assert diag["gpu_layers_effective"] == 0
    assert "fallback CPU" in diag["warning"]


# ---------------------------------------------------------------------------
# orchestrator coverage slice 4 (2026-07-18): heal-loop support edges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("tests.py", True),            # script legacy, sempre test
        ("TESTS.py", True),            # case-insensitive
        ("test_app.py", True),         # pytest-style prefix
        ("app_test.py", True),         # pytest-style suffix
        ("tests/test_x.py", True),     # decide il basename, non la dir
        ("contest_test.py", True),     # endswith("_test.py") vince
        ("app.py", False),
        ("test.py", False),            # ne' test_* ne' *_test ne' tests.py
        ("contest.py", False),         # "test" interno non basta
        ("README.md", False),
    ],
)
def test_is_test_filename_classifier(rel_path, expected):
    """_is_test_filename (L49-51) decide quali file scritti NON vanno al heal
    loop come implementazione (L733): una misclassificazione cambia cosa
    viene rigenerato e cosa conta come 'verificato'."""
    assert _is_test_filename(rel_path) is expected


def _swap_ready_orchestrator(launcher):
    orch = Orchestrator.__new__(Orchestrator)
    orch.serialize_vram = True
    orch._degraded_mode = True
    orch.vram_swap_threshold_mb = 2048
    orch.model_launcher = launcher
    orch._log = lambda *args, **kwargs: None
    return orch


def test_vram_swap_fast_path_without_serialization():
    """serialize_vram=False -> True immediato, launcher mai toccato (L251-252)."""
    orch = Orchestrator.__new__(Orchestrator)
    orch.serialize_vram = False
    orch._degraded_mode = True
    orch.model_launcher = None
    orch._log = lambda *args, **kwargs: None
    assert orch._check_vram_and_swap("coder") is True


def test_serialized_swap_ensure_failure_returns_false(monkeypatch):
    """ensure_alias False -> il metodo ritorna False (L284-286), nessun raise."""
    class Launcher:
        def release_alias(self, alias):
            return True

        def ensure_alias(self, alias):
            return False

    monkeypatch.setattr("devin.core.orchestrator.get_vram_status", lambda: {"free_mb": 100})
    monkeypatch.setattr("devin.core.orchestrator.time.sleep", lambda _: None)

    orch = _swap_ready_orchestrator(Launcher())
    assert orch._check_vram_and_swap("coder", "planner") is False


def test_serialized_swap_exception_returns_false(monkeypatch):
    """Eccezione nel release -> caught, False (L287-289), ensure mai tentato."""
    class Launcher:
        def release_alias(self, alias):
            raise RuntimeError("kill ko")

        def ensure_alias(self, alias):
            raise AssertionError("ensure non deve essere raggiunto")

    monkeypatch.setattr("devin.core.orchestrator.get_vram_status", lambda: {"free_mb": 100})
    monkeypatch.setattr("devin.core.orchestrator.time.sleep", lambda _: None)

    orch = _swap_ready_orchestrator(Launcher())
    assert orch._check_vram_and_swap("coder", "planner") is False


def _self_heal_orchestrator(degraded=True, serialize=True):
    """Bare orchestrator per _self_heal: web-ref spenta per ermeticita'."""
    orch = Orchestrator.__new__(Orchestrator)
    orch._degraded_mode = degraded
    orch.serialize_vram = serialize
    orch._log = lambda *args, **kwargs: None
    orch._maybe_web_reference = lambda error: ""
    return orch


def test_self_heal_swaps_to_planner_and_restores_with_feedback():
    """Degraded + serialize_vram (L200-212): swap planner<-coder PRIMA del
    Critic, feedback del Critic ritornato, restore coder<-planner nel finally."""
    orch = _self_heal_orchestrator()
    swaps = []
    orch._check_vram_and_swap = (
        lambda needed, release=None: swaps.append((needed, release)) or True
    )
    orch.critic = SimpleNamespace(
        analyze=lambda error, patch, context, sandbox_files=None:
            SimpleNamespace(feedback="usa il plus, non il minus")
    )

    out = orch._self_heal("coder", "raw error", patch="p", context="c")

    assert out == "usa il plus, non il minus"
    assert swaps == [("planner", "coder"), ("coder", "planner")], \
        "swap verso planner prima del Critic, restore verso coder nel finally"


def test_self_heal_failed_planner_swap_returns_raw_error():
    """Swap planner fallito -> RuntimeError interno (L202-203) catturato dal
    fallback generico: errore raw ritornato, Critic MAI chiamato, nessun
    restore (swapped_for_critic resta False)."""
    orch = _self_heal_orchestrator()
    logs = []
    orch._log = lambda msg, level="info": logs.append(msg)
    swaps = []
    orch._check_vram_and_swap = (
        lambda needed, release=None: swaps.append((needed, release)) or False
    )

    def forbidden_analyze(*args, **kwargs):
        raise AssertionError("il Critic non deve essere chiamato senza planner")

    orch.critic = SimpleNamespace(analyze=forbidden_analyze)

    out = orch._self_heal("coder", "raw error", patch="p", context="c")

    assert out == "raw error"
    assert swaps == [("planner", "coder")], "nessun restore dopo uno swap fallito"
    assert any("planner locale non disponibile" in m for m in logs)


def test_verified_success_memory_keeps_positive_polarity(tmp_path):
    """_remember_scaffold_outcome (L554-587) lato VERDE: polarity positiva,
    evidence exit_zero, kind eval_result, nessun failure_type — specchio del
    test negative gia' esistente."""
    captured = {}

    class Memory:
        def store_local(self, content, **kwargs):
            captured["content"] = content
            captured.update(kwargs)
            return "local_stored"

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_path = str(tmp_path / "Steam Profile Checker")
    orchestrator.memory_client = Memory()
    outcome = orchestrator._remember_scaffold_outcome(
        "build checker",
        {"status": "verified_success", "errors": []},
        ["main.py", "test_main.py"],
    )
    assert outcome == "local_stored"
    assert "tested successful approach" in captured["content"]
    assert "tests.py passed" in captured["content"]  # errors vuoti -> frase default
    assert "status:verified_success" in captured["tags"]
    assert "polarity:positive" in captured["tags"]
    assert "evidence:tests.py_exit_zero" in captured["tags"]
    assert "kind:eval_result" in captured["tags"]
    assert not any(t.startswith("failure_type:") for t in captured["tags"])

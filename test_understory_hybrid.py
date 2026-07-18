import json

from devin.ai.hybrid_memory_client import HybridMemoryClient
from devin.ai.understory_client import UnderstoryClient


class _Response:
    def __init__(self, body, content_type):
        self.text = body
        self.content = body.encode()
        self.headers = {"content-type": content_type}

    def json(self):
        return json.loads(self.text)


def test_understory_decodes_json_and_sse():
    assert UnderstoryClient._decode_response(
        _Response('{"result":{"ok":true}}', "application/json")
    )["result"]["ok"] is True
    assert UnderstoryClient._decode_response(
        _Response('event: message\ndata: {"result":{"ok":true}}\n\n', "text/event-stream")
    )["result"]["ok"] is True


def test_hybrid_falls_back_to_automem():
    client = HybridMemoryClient({})

    class _Understory:
        enabled = True
        def recall(self, *args, **kwargs): return []
        def store(self, *args, **kwargs): return "failed"

    class _AutoMem:
        enabled = True
        def recall(self, *args, **kwargs): return ["fallback"]
        def store(self, *args, **kwargs): return "queued"

    client.understory = _Understory()
    client.automem = _AutoMem()
    assert client.recall("query") == ["fallback"]
    assert client.store("fact") == "queued"


def test_remote_recall_filters_marker_beyond_first_line():
    """Il recall semantico di Understory prefissa gli hit con "[path] ": il
    marker STRUCTURED_MEMORY finisce oltre la prima riga e gli stati esclusi
    (quarantine/pending/...) devono essere filtrati comunque."""
    client = HybridMemoryClient({})

    quarantined = (
        "[agents/devin/notes.md] nota operativa\n"
        "[STRUCTURED_MEMORY status=quarantine polarity=negative evidence=rumor]\n"
        "ipotesi non verificata che non deve tornare nel contesto"
    )
    verified = (
        "[shared/lesson.md] lezione\n"
        "[STRUCTURED_MEMORY status=verified_success polarity=positive evidence=e2e]\n"
        "lezione verificata richiamabile"
    )
    unmarked = "appunto manuale dell'utente senza marker"

    class _Understory:
        enabled = True
        def recall(self, *args, **kwargs):
            return [quarantined, verified, unmarked]

    class _AutoMem:
        enabled = False
        def recall(self, *args, **kwargs): return []

    client.understory = _Understory()
    client.automem = _AutoMem()
    recalled = client.recall("query", limit=5)

    assert any("quarantine" not in m for m in recalled)
    assert not any("ipotesi non verificata" in m for m in recalled), \
        "memoria in quarantena ha bypassato il filtro (marker oltre la prima riga)"
    assert any("lezione verificata" in m for m in recalled)
    assert any("appunto manuale" in m for m in recalled), \
        "le memorie non marcate (appunti manuali) devono restare ammissibili"


def test_remote_memory_status_parser():
    from devin.ai.hybrid_memory_client import _remote_memory_status

    assert _remote_memory_status(
        "[STRUCTURED_MEMORY status=pending_review polarity=neutral]\nfatto"
    ) == "pending_review"
    assert _remote_memory_status(
        "prefisso\n[STRUCTURED_MEMORY status=superseded]\nvecchio"
    ) == "superseded"
    # Nessun marker -> None (ammissibile)
    assert _remote_memory_status("testo libero") is None
    # Marker senza attributo status -> None (ammissibile, come prima)
    assert _remote_memory_status("[STRUCTURED_MEMORY polarity=positive]\nx") is None
    # Il valore e' un token intero: niente match per sottostringa
    assert _remote_memory_status(
        "[STRUCTURED_MEMORY status=verified_failure]\nx"
    ) == "verified_failure"


def test_understory_store_scopes_project_path():
    client = UnderstoryClient({"understory": {"enabled": True}})
    captured = {}
    client._call_tool = lambda name, arguments: captured.update(
        {"name": name, "arguments": arguments}) or "ok"
    assert client.store("remember this", tags=[
        "devin", "project:My Project", "source:devin",
        "domain:software-engineering", "status:human_confirmed",
    ]) == "stored"
    assert captured["name"] == "memory_add"
    assert captured["arguments"]["suggested_path"] == "/shared/software-engineering/devin-my-project.md"
    assert "Do not create links" in captured["arguments"]["content"]
    assert "memory_id: mem-" in captured["arguments"]["content"]
    assert "evidence: explicit_user_save" in captured["arguments"]["content"]
    assert "confidence: 1.0" in captured["arguments"]["content"]


def test_understory_unverified_store_goes_to_agent_quarantine():
    client = UnderstoryClient({"understory": {"enabled": True, "agent_id": "devin"}})
    captured = {}
    client._call_tool = lambda name, arguments: captured.update(arguments) or "ok"
    assert client.store("tentative", tags=["project:demo"]) == "stored"
    assert captured["suggested_path"] == "/agents/devin/quarantine/demo.md"
    assert "status: pending_review" in captured["content"]


def test_semantic_recall_excludes_raw_and_quarantine(tmp_path, monkeypatch):
    (tmp_path / "shared").mkdir()
    (tmp_path / "agents" / "devin" / "raw").mkdir(parents=True)
    (tmp_path / "agents" / "devin" / "quarantine").mkdir(parents=True)
    (tmp_path / "shared" / "lesson.md").write_text("verified lesson")
    (tmp_path / "agents" / "devin" / "raw" / "raw.md").write_text("raw secret")
    (tmp_path / "agents" / "devin" / "quarantine" / "maybe.md").write_text("uncertain")

    indexed = []
    class FakeVectorStore:
        def index_project(self, project_path, files, cache_path=None):
            indexed.extend(f["path"] for f in files)
        def search_semantic(self, *args, **kwargs):
            return []

    import devin.memory.vector_store as vector_module
    monkeypatch.setattr(vector_module, "VectorStore", FakeVectorStore)
    client = UnderstoryClient({"understory": {
        "enabled": True, "bundle_path": str(tmp_path), "agentic_recall": False,
    }})
    client.recall("lesson")
    assert any(path.endswith("lesson.md") for path in indexed)
    assert not any(path.endswith("raw.md") or path.endswith("maybe.md") for path in indexed)



def test_memory_taxonomy_documents_recall_safety_contract():
    from devin.memory.taxonomy import (
        build_memory_tags,
        is_recallable_status,
        tag_value,
        validate_memory_tags,
    )

    hypothesis_tags = build_memory_tags(kind='hypothesis', status='pending_review')
    failure_tags = build_memory_tags(
        kind='failure_lesson',
        status='verified_failure',
        polarity='negative',
        evidence='test',
        failure_type='chat_only_output',
    )

    assert validate_memory_tags(hypothesis_tags) == []
    assert validate_memory_tags(failure_tags) == []
    assert not is_recallable_status(tag_value(hypothesis_tags, 'status', ''))
    assert is_recallable_status(tag_value(failure_tags, 'status', ''))


def test_local_memory_schema_separates_hypotheses_from_verified_failures(tmp_path):
    from devin.memory.taxonomy import build_memory_tags

    path = tmp_path / 'local_memories.jsonl'
    client = HybridMemoryClient({'local_memory': {'path': str(path)}})
    client.understory.enabled = False
    client.automem.enabled = False

    client.store_local(
        'Hypothesis: maybe an external Steam tracker can help later.',
        tags=build_memory_tags(
            kind='hypothesis',
            status='pending_review',
            project='Steam Profile Checker',
            memory_key='hypothesis_tracker',
        ) + ['topic:steam'],
        importance=0.3,
    )
    client.store_local(
        'Steam chat-only output is a verified failure; write files and run tests instead.',
        tags=build_memory_tags(
            kind='failure_lesson',
            status='verified_failure',
            polarity='negative',
            evidence='human_review',
            project='Steam Profile Checker',
            failure_type='chat_only_output',
            memory_key='verified_chat_only',
        ) + ['topic:steam'],
        importance=0.9,
    )

    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert records[-1]['schema_version'] == 'memory.v1'
    assert records[-1]['kind'] == 'failure_lesson'
    assert records[-1]['failure_type'] == 'chat_only_output'

    recalled = client.recall('Steam chat-only output', tags=['topic:steam'], limit=5)
    assert len(recalled) == 1
    assert 'status=verified_failure' in recalled[0]
    assert 'Hypothesis' not in recalled[0]


def test_seed_core_memory_is_idempotent(tmp_path):
    from scripts.seed_core_memory import seed_core_memory

    config = {'local_memory': {'path': str(tmp_path / 'seed.jsonl')}}
    first = seed_core_memory(config)
    second = seed_core_memory(config)

    assert len(first['stored']) == 4
    assert second['stored'] == []
    assert sorted(second['skipped']) == sorted(first['stored'])




def test_eval_recorder_detects_chat_only_operational_failure():
    from devin.memory.eval_recorder import detect_chat_only_output

    finding = detect_chat_only_output(
        "Crea un'applicazione Steam Profile Checker MVP con tests.py e file reali",
        "Ecco il codice:\n```python\nprint('hello')\n```",
    )
    assert finding is not None
    assert finding['status'] == 'verified_failure'
    assert finding['failure_type'] == 'chat_only_output'


def test_eval_recorder_ignores_explanatory_chat():
    from devin.memory.eval_recorder import detect_chat_only_output

    assert detect_chat_only_output(
        "Che ne pensi di questa architettura?",
        "Potresti fare cosi':\n```python\nprint('example')\n```",
    ) is None


def test_record_eval_result_is_idempotent_and_structured(tmp_path):
    from devin.ai.hybrid_memory_client import HybridMemoryClient
    from devin.memory.eval_recorder import record_eval_result

    path = tmp_path / 'eval_mem.jsonl'
    client = HybridMemoryClient({'local_memory': {'path': str(path)}})
    client.understory.enabled = False
    client.automem.enabled = False

    first = record_eval_result(
        client,
        project_path=str(tmp_path / 'Steam Profile Checker'),
        task="Crea un'applicazione MVP con tests.py",
        eval_name='chat_only_output_detector',
        status='verified_failure',
        failure_type='chat_only_output',
        reason='snippet only',
        evidence='test',
        retry_rule='write files and run tests',
    )
    second = record_eval_result(
        client,
        project_path=str(tmp_path / 'Steam Profile Checker'),
        task="Crea un'applicazione MVP con tests.py",
        eval_name='chat_only_output_detector',
        status='verified_failure',
        failure_type='chat_only_output',
        reason='snippet only',
        evidence='test',
        retry_rule='write files and run tests',
    )

    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert first == 'local_stored'
    assert second == 'duplicate'
    assert len(records) == 1
    assert records[0]['kind'] == 'failure_lesson'
    assert records[0]['failure_type'] == 'chat_only_output'
    assert 'eval:chat_only_output_detector' in records[0]['tags']


def test_scaffold_request_routes_strong_operational_requests_for_non_empty_project(tmp_path):
    from devin.ui.fast_app import _is_scaffold_request

    (tmp_path / 'existing.py').write_text('print(1)\n', encoding='utf-8')
    assert _is_scaffold_request(
        "Crea un'applicazione Steam Profile Checker MVP con tests.py e file reali",
        str(tmp_path),
    )
    assert not _is_scaffold_request("Che ne pensi di questo progetto?", str(tmp_path))



def test_codex_app_shell_is_local_first_and_wired():
    from pathlib import Path
    import asyncio
    from devin.ui import fast_app

    html = Path('devin/ui/templates/codex_app.html').read_text(encoding='utf-8')
    diagnostics = Path('devin/ui/templates/codex_diagnostics.html').read_text(encoding='utf-8')
    diagnostics_js = Path('devin/ui/static/js/codex_diagnostics.js').read_text(encoding='utf-8')
    js = Path('devin/ui/static/js/codex_app.js').read_text(encoding='utf-8')

    # Pannello destro "Attività" (2026-07-16): avanzamento + cartella di
    # lavoro + contesto, al posto dei quick-link stile menu web.
    assert 'Attività' in html
    assert 'workdir-box' in html
    assert 'context-tags' in html
    assert 'pipeline-steps' in html
    assert '/app/diagnostics' in html
    assert '/static/js/codex_app.js' in html
    assert 'fonts.googleapis.com' not in html
    assert '/api/mind/status' in js
    assert '/api/runs' not in js
    assert '/api/runs' in diagnostics_js
    assert '/events/stream' in js
    assert '/api/chat' in js
    assert '/api/chat/document' in js
    assert 'chat-file' in html
    assert '/api/workspace/projects' in js
    assert 'project_path' in js
    assert 'project-list' in html
    assert 'chat-list' in html
    assert '/api/chat/history' in js
    assert '/api/project/chats/new' in js
    assert 'chat_id' in js
    assert '/api/diff/preview' in js
    assert '/api/diff/apply' in js
    assert 'window.confirm' in js
    assert 'diff-input' in html
    assert 'diff-apply-button' in html
    assert '/api/terminal/output' in js
    assert 'run-log-output' not in html
    assert 'run-list' not in html
    assert 'timeline' not in html
    # Home Claude-like (2026-07-16): hero di benvenuto al posto del finto
    # messaggio assistant e del banner runs; il link a Diagnostics resta
    # (asserito sopra), la diff preview e' collassabile.
    assert 'chat-hero' in html
    assert 'collapsible-panel' in html
    assert 'Runs' in diagnostics
    assert '/static/js/codex_diagnostics.js' in diagnostics
    assert '/api/training/overview' in diagnostics_js
    assert '/api/mind/status' in diagnostics_js
    assert '/api/models/info' in diagnostics_js
    assert 'data-diagnostics-tab' in diagnostics
    assert 'setActiveTab' in diagnostics_js
    assert 'chat-form' in html
    assert 'command-overlay' in html
    assert 'command-search' in html
    assert '/app/diagnostics#knowledge' in html
    # 2026-07-16: link "Project sandbox" rimosso dalla home (la sandbox
    # diventa trasparente via cartella di lavoro del progetto). L'endpoint e
    # la sezione Diagnostics restano, solo il quick-link sparisce.
    assert 'open-command-palette' in html
    assert 'setupCommandPalette' in js
    assert 'commandActions' in js
    assert 'crawlUrlIntoKnowledge' in js
    assert '/api/project/knowledge/crawl' in js
    assert 'diagnosticsUrl("training")' in js
    assert 'event.key.toLowerCase() === "k"' in js
    assert 'command-shell' not in html
    assert 'EventSource' in js
    assert 'data-run-log' in diagnostics_js
    assert 'timeline' not in html

    response = asyncio.run(fast_app.codex_app_page(type('Req', (), {})()))
    assert response.template.name == 'codex_app.html'


def test_mind_status_exposes_codex_like_contract(monkeypatch):
    from devin.ui import fast_app

    class FakeAI:
        config = {"web_search": {"provider": "tinyfish"}}
        def health(self):
            return {"ok": True}

    class FakeMemory:
        def status(self):
            return {
                "backend": "understory",
                "reachable": True,
                "local": {"enabled": True, "records": 4, "path": "/tmp/mem.jsonl"},
            }

    monkeypatch.setattr(fast_app, "_get_ai_client", lambda: FakeAI())
    monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
    monkeypatch.setattr(fast_app, "_get_automem", lambda: FakeMemory())
    monkeypatch.setattr(fast_app, "_get_vram_info", lambda: {"total_mb": 16, "used_mb": 4})

    status = fast_app._build_mind_status()
    assert status["agent"]["name"] == "DEVIN"
    assert status["agent"]["desktop_shell_target"] == "Tauri"
    assert "verify" in status["loop"]
    assert "chat_only_output_detector" in status["evals"]["active_detectors"]
    assert "verified_failure" in status["memory"]["recall_safe_statuses"]
    assert "hypothesis" in status["memory"]["review_only_statuses"]
    assert status["ui"]["panels"] == ["workspace", "conversation/work-stream", "mind/context"]



def test_run_event_store_records_typed_timeline(tmp_path):
    from devin.core.run_events import RunEventStore

    store = RunEventStore(tmp_path)
    store.start("run_1", mode="scaffold", task="build app", project_path="/tmp/project")
    store.append_log("run_1", "Planner analyzing...", level="info")
    store.append_log("run_1", "Quality gate superato: tests.py + sintassi OK", level="success")
    store.finish("run_1", status="success", mode="scaffold")

    events = store.list("run_1")
    assert [event["seq"] for event in events] == [0, 1, 2, 3]
    assert events[0]["type"] == "run_started"
    assert events[1]["type"] == "plan"
    assert events[2]["type"] == "quality_gate_passed"
    assert events[3]["type"] == "run_finished"
    assert store.list("run_1", after_seq=1)[0]["seq"] == 2


def test_run_event_store_rejects_unsafe_run_id(tmp_path):
    from devin.core.run_events import RunEventStore

    store = RunEventStore(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        store.append("../escape", "run_started")


def test_run_events_api_returns_structured_events(tmp_path, monkeypatch):
    from devin.core.run_events import RunEventStore
    from devin.ui import fast_app
    import asyncio

    store = RunEventStore(tmp_path)
    store.start("run_api", mode="maintenance", task="fix bug", project_path="/tmp/project")
    monkeypatch.setattr(fast_app, "_run_events", store)

    result = asyncio.run(fast_app.api_run_events("run_api"))
    assert result["run_id"] == "run_api"
    assert result["events"][0]["type"] == "run_started"
def test_tauri_desktop_shell_targets_workspace_app():
    import json
    from pathlib import Path

    package = json.loads(Path("package.json").read_text(encoding="utf-8"))
    config = json.loads(Path("src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    capability = json.loads(Path("src-tauri/capabilities/default.json").read_text(encoding="utf-8"))
    docs = Path("docs/TAURI_DESKTOP.md").read_text(encoding="utf-8")

    assert package["scripts"]["desktop:dev"] == "node_modules\\\\.bin\\\\tauri.cmd dev"
    assert package["scripts"]["desktop:build"] == "node_modules\\\\.bin\\\\tauri.cmd build"
    assert package["scripts"]["desktop:info"] == "node_modules\\\\.bin\\\\tauri.cmd info"
    assert package["scripts"]["desktop:launch"].endswith("./scripts/devin-tauri-dev.ps1")
    assert package["scripts"]["backend:headless"].endswith("./scripts/devin-tauri-dev.ps1 -SkipTauri")
    assert package["scripts"]["desktop:preflight"].endswith("./scripts/check-tauri-env.ps1")
    assert package["scripts"]["desktop:open"].endswith("./scripts/devin-tauri-dev.ps1 -BrowserFallback")
    assert package["scripts"]["desktop:windows-host"].endswith("./scripts/launch-windows-desktop-host.ps1 -BrowserFallback")
    assert package["scripts"]["desktop:prepare-host"].endswith("./scripts/prepare-windows-desktop-host.ps1")
    assert package["scripts"]["desktop:windows-info"].endswith("./scripts/launch-windows-desktop-host.ps1 -Info -SkipNpmInstall")
    assert Path("scripts/run-tauri-desktop.ps1").exists()
    assert Path("scripts/DEVIN Desktop.cmd").exists()
    assert Path("scripts/prepare-windows-desktop-host.ps1").exists()
    assert Path("scripts/launch-windows-desktop-host.ps1").exists()
    assert Path("scripts/start-fastapi-headless.sh").exists()
    assert Path("src-tauri/icons/icon.ico").exists()

    launcher = Path("scripts/devin-tauri-dev.ps1").read_text(encoding="utf-8")
    start_script = Path("scripts/start-fastapi-headless.sh").read_text(encoding="utf-8")
    host_launcher = Path("scripts/launch-windows-desktop-host.ps1").read_text(encoding="utf-8")
    host_prepare = Path("scripts/prepare-windows-desktop-host.ps1").read_text(encoding="utf-8")
    desktop_cmd = Path("scripts/DEVIN Desktop.cmd").read_text(encoding="utf-8")

    # 2026-07-16: il backend headless esporta l'opt-in per l'auto-stop alla
    # chiusura della GUI (vedi /api/desktop/close_cleanup).
    assert "venv/bin/python devin/ui/fast_app.py" in start_script
    assert "DEVIN_DESKTOP_CLOSE_STOPS_BACKEND=1" in start_script
    assert "logs/fast_app_headless.log" in launcher
    assert "scripts/start-fastapi-headless.sh" in launcher
    assert "-WindowStyle Hidden" in launcher
    assert "--cd" in launcher
    assert "Get-NpmCommand" in launcher
    assert "desktop-host" in host_launcher
    assert "$Info" in host_launcher
    assert "$tauriCommand = \"info\"" in host_launcher
    assert "desktop-launch.log" in host_launcher
    assert "tauri-dev.log" in host_launcher
    assert "Get-NodeCommand" in host_launcher
    assert "@tauri-apps\\cli\\tauri.js" in host_launcher
    assert "tauri output follows" in host_launcher
    assert "& $node $tauriJs $tauriCommand" in host_launcher
    assert "prepare-windows-desktop-host.ps1" in host_launcher
    assert "src-tauri" in host_prepare
    assert "package-lock.json" in host_prepare
    assert "start-fastapi-headless.sh" in host_prepare
    assert "DEVIN Desktop.cmd" in host_prepare
    assert "nativeLauncher" in host_prepare
    assert "prepare-windows-desktop-host.ps1" in desktop_cmd
    assert r"%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd" in desktop_cmd
    assert "start """ in desktop_cmd
    assert "/api/health" in launcher

    html = Path("devin/ui/templates/codex_app.html").read_text(encoding="utf-8")
    css = Path("devin/ui/static/css/codex_app.css").read_text(encoding="utf-8")
    assert "topbar-command" in html
    assert "active-scope-label" in html
    assert "Modern desktop polish layer" in css
    assert config["build"]["devUrl"] == "http://127.0.0.1:5000/app"
    assert config["build"]["frontendDist"] == "http://127.0.0.1:5000/app"
    assert config["app"]["windows"][0]["url"] == "http://127.0.0.1:5000/app"
    assert capability["permissions"] == ["core:default"]
    assert "brownfield" in docs
    assert "sidecar" in docs
    assert "headless" in docs.lower()
    assert "DEVIN Desktop.cmd" in docs
    assert "Windows-native" in docs
    main_rs = Path("src-tauri/src/main.rs").read_text(encoding="utf-8")
    fast_app_text = Path("devin/ui/fast_app.py").read_text(encoding="utf-8")
    models_desktop_text = Path("devin/ui/routers/models_desktop.py").read_text(encoding="utf-8")
    status_text = Path("devin/ui/routers/status.py").read_text(encoding="utf-8")
    assert "/api/desktop/close_cleanup" in main_rs
    assert "CloseRequested" in main_rs
    assert "CLOSE_CLEANUP_SENT" in main_rs
    # close_cleanup vive nel router models_desktop, readiness nel router status
    # (split plan fette 5-6); gli helper restano in fast_app.
    assert "/api/desktop/close_cleanup" in models_desktop_text
    assert "/api/desktop/readiness" in status_text
    assert "_known_local_model_servers" in fast_app_text
    assert "DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS" in models_desktop_text



def test_desktop_readiness_contract(monkeypatch):
    import asyncio
    from devin.ui import fast_app

    monkeypatch.setattr(fast_app, "_known_local_model_servers", lambda: {"coder": {"port": 8000}})
    result = asyncio.run(fast_app.api_desktop_readiness())
    assert result["desktop_host"]["launcher"].endswith("DEVIN Desktop.cmd")
    assert result["close_cleanup"]["remote_rig_safe"] is True
    assert result["local_model_servers"] == {"coder": {"port": 8000}}
    assert result["validation_links"]["sandbox"] == "/app/diagnostics#sandbox"

def test_desktop_close_cleanup_only_kills_local_models(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from devin.ui import fast_app

    calls = {"shutdown": 0}

    class FakeLauncher:
        def get_status(self):
            return SimpleNamespace(local_running={"coder": {"alias": "coder"}}, model_source="local")

        def shutdown_all(self):
            calls["shutdown"] += 1

    monkeypatch.setattr(fast_app, "_get_launcher", lambda: FakeLauncher())
    monkeypatch.setattr(fast_app, "_known_local_model_servers", lambda: {})
    monkeypatch.setattr(fast_app, "_shutdown_known_local_model_servers", lambda: [])
    monkeypatch.delenv("DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS", raising=False)

    result = asyncio.run(fast_app.api_desktop_close_cleanup())
    assert result["status"] == "killed"
    assert result["local_models"] == ["coder"]
    assert calls["shutdown"] == 1


def test_desktop_close_cleanup_skips_when_no_local_models(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from devin.ui import fast_app

    calls = {"shutdown": 0}

    class FakeLauncher:
        def get_status(self):
            return SimpleNamespace(local_running={}, model_source="unavailable")

        def shutdown_all(self):
            calls["shutdown"] += 1

    monkeypatch.setattr(fast_app, "_get_launcher", lambda: FakeLauncher())
    monkeypatch.setattr(fast_app, "_known_local_model_servers", lambda: {})
    monkeypatch.setattr(fast_app, "_shutdown_known_local_model_servers", lambda: [])
    result = asyncio.run(fast_app.api_desktop_close_cleanup())
    assert result["status"] == "skipped"
    assert result["reason"] == "no_local_models"
    assert calls["shutdown"] == 0


def test_desktop_close_cleanup_kills_known_local_ports(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from devin.ui import fast_app

    calls = {"shutdown": 0, "known": 0}

    class FakeLauncher:
        def get_status(self):
            return SimpleNamespace(local_running={}, model_source="unavailable")

        def shutdown_all(self):
            calls["shutdown"] += 1

    def fake_shutdown_known():
        calls["known"] += 1
        return ["coder", "planner"]

    monkeypatch.setattr(fast_app, "_get_launcher", lambda: FakeLauncher())
    monkeypatch.setattr(fast_app, "_known_local_model_servers", lambda: {"coder": {"port": 8000}, "planner": {"port": 8001}})
    monkeypatch.setattr(fast_app, "_shutdown_known_local_model_servers", fake_shutdown_known)

    result = asyncio.run(fast_app.api_desktop_close_cleanup())
    assert result["status"] == "killed"
    assert result["local_models"] == ["coder", "planner"]
    assert calls["shutdown"] == 1
    assert calls["known"] == 1

def test_chat_upload_formats_binary_metadata():
    from devin.ui.fast_app import _format_chat_upload_for_context

    text = _format_chat_upload_for_context("tool.exe", b"MZ\x00\x01binary")
    assert "tool.exe" in text
    assert "sha256=" in text
    assert "File binario" in text
    assert "```hex" in text


def test_project_space_warns_when_index_cache_not_deletable(tmp_path, capsys):
    """Se la cache dell'indice non si puo' eliminare, la knowledge stale deve
    essere SEGNALATA — prima il fallimento spariva in silenzio."""
    from devin.core.project_space import ProjectSpace

    ps = ProjectSpace(str(tmp_path))
    # Una directory al posto del file cache: unlink() solleva IsADirectoryError
    ps.index_cache.mkdir(parents=True)
    result = ps.add_knowledge("notes.txt", b"hello knowledge")
    assert result["ok"] is True  # l'operazione resta fail-soft
    out = capsys.readouterr().out
    assert "cache indice non eliminabile" in out


def test_automem_flush_warns_on_unreadable_outbox(tmp_path, monkeypatch, capsys):
    """Outbox illeggibile = memorie accodate mai sincronizzate: deve restare
    traccia del problema, non un ritorno silenzioso a 0."""
    import devin.ai.automem_client as automem_module
    from devin.ai.automem_client import AutoMemClient

    outbox_dir = tmp_path / "outbox_as_dir"
    outbox_dir.mkdir()  # read_text su una directory solleva IsADirectoryError
    monkeypatch.setattr(automem_module, "OUTBOX_PATH", outbox_dir)

    client = AutoMemClient({"automem": {"enabled": True}})
    assert client.flush_outbox() == 0
    out = capsys.readouterr().out
    assert "outbox illeggibile" in out


def test_project_knowledge_accepts_unknown_binary_extension(tmp_path):
    from devin.core.project_space import ProjectSpace

    ps = ProjectSpace(str(tmp_path))
    result = ps.add_knowledge("sample.weirdbin", b"\x00\x01\x02payload")
    assert result["ok"] is True
    extracted = tmp_path / ".devin" / "knowledge" / "_extracted" / "sample.weirdbin.txt"
    content = extracted.read_text(encoding="utf-8")
    assert "sha256" in content
    assert "hex_preview" in content





def test_structured_contracts_are_instructor_ready():
    from devin.ai.structured_contracts import MethodTrace, TrainingReviewDecision, CrawlKnowledgeRecord

    decision = TrainingReviewDecision(
        attempt_id="attempt_123",
        status="verified_failure",
        rationale="tests failed",
        method=MethodTrace(
            hypothesis="official API only",
            checks_run=["pytest", "endpoint audit"],
            evidence=["invented endpoint found"],
            failure_mode="hallucinated endpoint",
            next_action="add URL allowlist validator",
        ),
        lesson_candidate="Never invent API endpoints; verify against official docs.",
        tags=["steam", "api"],
    )
    payload = decision.to_store_payload(reviewer="teacher")
    assert payload["status"] == "verified_failure"
    assert "endpoint audit" in payload["method_trace"]
    assert payload["lesson_candidate"].startswith("Never invent")

    record = CrawlKnowledgeRecord(url="https://example.com/docs", markdown="# Docs", source="mock")
    text = record.as_knowledge_markdown()
    assert "# Fonte" in text
    assert "source: mock" in text


def test_crawl_ingestion_basic_mode_uses_fetcher_without_network():
    import asyncio
    from devin.ai.crawl_ingestion import crawl_url_to_knowledge

    def fake_fetch(url, max_chars, timeout):
        assert url == "https://example.com"
        assert max_chars >= 1000
        return "Example documentation"

    record = asyncio.run(crawl_url_to_knowledge("https://example.com", mode="basic", max_chars=1200, basic_fetcher=fake_fetch))
    assert record.source == "basic_fetch"
    assert "Example documentation" in record.markdown


def test_structured_training_review_endpoint_validates_and_stores(tmp_path, monkeypatch):
    import asyncio
    from devin.ui import fast_app
    from devin.ui.routers import training as training_router

    # Split 2026-07-18: l'handler vive nel router training; il monkeypatch va
    # sul modulo dove il nome viene RISOLTO a call-time (il router), non su
    # fast_app (che non ospita piu' l'handler). TrainingStore si importa dalla
    # sorgente canonica (fetta 15: fast_app non lo re-esporta piu').
    from devin.training.store import TrainingStore
    store = TrainingStore(tmp_path / "training")
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    case = store.add_case("Use official API only")
    attempt = store.add_attempt(case["case_id"], prompt="Build checker", status="auto_failure")

    class FakeRequest:
        async def json(self):
            return {
                "reviewer": "colibri",
                "decision": {
                    "attempt_id": attempt["attempt_id"],
                    "status": "verified_failure",
                    "rationale": "invented endpoint",
                    "method": {
                        "hypothesis": "endpoint audit",
                        "checks_run": ["grep api hosts"],
                        "evidence": ["non api.steampowered.com host found"],
                        "failure_mode": "hallucinated endpoint",
                        "next_action": "enforce allowlist",
                    },
                    "lesson_candidate": "Only documented Steam Web API hosts are allowed.",
                    "tags": ["structured_review"],
                },
            }

    result = asyncio.run(training_router.api_training_reviews_structured(FakeRequest()))
    assert result["validated"] is True
    assert result["review"]["reviewer"] == "colibri"
    assert "grep api hosts" in result["review"]["method_trace"]

def test_project_sandbox_prepares_isolated_copy_with_manifest(tmp_path):
    import json
    from pathlib import Path
    from devin.engine.project_sandbox import ProjectSandboxPolicy, prepare_project_sandbox

    source = tmp_path / "ForgeStudio"
    source.mkdir()
    (source / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (source / "venv").mkdir()
    (source / "venv" / "pyvenv.cfg").write_text("home=/python\n", encoding="utf-8")
    (source / "model.gguf").write_bytes(b"fake")

    manifest = prepare_project_sandbox(source, sandbox_root=tmp_path / "sandboxes", policy=ProjectSandboxPolicy())
    sandbox = Path(manifest["sandbox_path"])
    assert (sandbox / "app.py").exists()
    assert not (sandbox / ".env").exists()
    assert not (sandbox / "venv").exists()
    assert not (sandbox / "model.gguf").exists()
    assert manifest["promotion_policy"]["auto_apply_to_source"] is False
    assert manifest["promotion_policy"]["requires_diff_review"] is True
    reasons = {item["reason"] for item in manifest["skipped"]}
    assert "secret_pattern_skipped" in reasons
    assert "venv_skipped_by_default" in reasons
    assert "large_binary_pattern_skipped" in reasons

    manifest_file = sandbox / ".devin_sandbox_manifest.json"
    saved = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "project_sandbox_v1"


def test_project_sandbox_can_link_venv_as_lightweight_reference(tmp_path):
    from pathlib import Path
    from devin.engine.project_sandbox import ProjectSandboxPolicy, prepare_project_sandbox

    source = tmp_path / "linked_project"
    source.mkdir()
    (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "pyvenv.cfg").write_text("home=/python\n", encoding="utf-8")

    manifest = prepare_project_sandbox(
        source,
        sandbox_root=tmp_path / "sandboxes",
        policy=ProjectSandboxPolicy(link_venv=True),
    )
    sandbox = Path(manifest["sandbox_path"])
    linked_venv = sandbox / ".venv"
    assert linked_venv.is_symlink()
    assert linked_venv.resolve() == (source / ".venv").resolve()
    assert manifest["policy"]["link_venv"] is True
    assert manifest["linked"][0]["kind"] == "venv_symlink"
    assert manifest["execution_policy"]["do_not_pip_install_into_linked_venv"] is True


def test_project_sandbox_can_include_venv_when_explicit(tmp_path):
    from pathlib import Path
    from devin.engine.project_sandbox import ProjectSandboxPolicy, prepare_project_sandbox

    source = tmp_path / "project"
    source.mkdir()
    (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "pyvenv.cfg").write_text("home=/python\n", encoding="utf-8")

    manifest = prepare_project_sandbox(source, sandbox_root=tmp_path / "sandboxes", policy=ProjectSandboxPolicy(include_venv=True))
    sandbox = Path(manifest["sandbox_path"])
    assert (sandbox / ".venv" / "pyvenv.cfg").exists()
    assert manifest["policy"]["include_venv"] is True

def test_training_store_records_failure_correction_and_exports_verified_rows(tmp_path):
    from pathlib import Path
    from devin.training.store import TrainingStore

    store = TrainingStore(tmp_path / "training")
    case = store.add_case("Fix a bug", title="Bugfix", tags=["debug"])
    attempt = store.add_attempt(case["case_id"], prompt="Fix a bug", status="auto_failure", error_reason="test failed")
    review = store.add_review(
        attempt["attempt_id"],
        "verified_failure",
        rationale="pytest failed",
        method_trace="hypothesis -> ran pytest -> failure proves branch missing -> add tested branch",
        failure_mode="missing tested branch",
        next_action="add regression test before patch",
        lesson_candidate="Always run the project test command before marking success.",
        reviewer="teacher",
        tags=["validated"],
    )
    store.add_correction(attempt["attempt_id"], "Use the tested branch", corrected_solution="def fix(): return True", tags=["verified_success"])
    summary = store.summary()
    assert summary["cases"] == 1
    assert summary["auto_failure"] == 1
    assert summary["reviews"] == 1
    assert summary["review_verified_failure"] == 1
    assert review["promotion"] == "manual_required"
    assert "ran pytest" in review["method_trace"]
    assert review["lesson_candidate"].startswith("Always run")
    assert store.list_attempts()[0]["status"] == "auto_failure"
    assert store.latest_reviews_by_attempt()[attempt["attempt_id"]]["status"] == "verified_failure"
    exported = store.export_sft_dataset()
    assert exported["rows"] == 1
    text = Path(exported["path"]).read_text(encoding="utf-8")
    assert "def fix" in text
    assert "verified_failure" not in text

    teacher_packet = store.export_teacher_packet()
    assert teacher_packet["rows"] == 1
    packet_text = Path(teacher_packet["path"]).read_text(encoding="utf-8")
    assert "teacher_review_v1" in packet_text
    assert "auto_promote" in packet_text
    assert "verified_failure" in packet_text
    assert "known_reviews" in packet_text
    assert "method_trace" in packet_text
    assert "lesson_candidate" in packet_text
    exports = store.list_exports()
    assert exports[0]["format"] == "teacher_review_v1"
    assert any(item["format"] == "sft_messages_jsonl" for item in exports)


def test_log_retention_keeps_recent_and_opened_logs(tmp_path):
    import os
    from devin.core.log_retention import LogRetentionPolicy, cleanup_logs, mark_log_opened

    now = 2_000_000_000.0
    old_ts = now - (30 * 24 * 60 * 60)
    old_log = tmp_path / "run_old.log"
    opened_log = tmp_path / "run_opened.log"
    recent_log = tmp_path / "run_recent.log"
    for path in (old_log, opened_log, recent_log):
        path.write_text("status: success\n", encoding="utf-8")
    os.utime(old_log, (old_ts, old_ts))
    os.utime(opened_log, (old_ts, old_ts))
    os.utime(recent_log, (now, now))
    mark_log_opened(tmp_path, opened_log.name, now=now)

    policy = LogRetentionPolicy(enabled=True, retention_days=14, keep_recent_runs=1)
    preview = cleanup_logs(tmp_path, policy=policy, now=now, dry_run=True)
    candidate_names = {item["file"] for item in preview["candidates"]}
    assert old_log.name in candidate_names
    assert opened_log.name not in candidate_names
    assert recent_log.name not in candidate_names

    result = cleanup_logs(tmp_path, policy=policy, now=now, dry_run=False)
    assert result["deleted"] == 1
    assert not old_log.exists()
    assert opened_log.exists()
    assert recent_log.exists()


def test_training_ui_and_api_scaffold_present():
    from pathlib import Path
    from devin.training.benchmarks import get_builtin_cases, list_builtin_benchmarks

    html = Path("devin/ui/templates/codex_app.html").read_text(encoding="utf-8")
    diagnostics = Path("devin/ui/templates/codex_diagnostics.html").read_text(encoding="utf-8")
    diagnostics_js = Path("devin/ui/static/js/codex_diagnostics.js").read_text(encoding="utf-8")
    js = Path("devin/ui/static/js/codex_app.js").read_text(encoding="utf-8")
    assert "training-section" not in html
    assert "Training review" in diagnostics
    assert "Memory Audit" in diagnostics or "Memory audit" in diagnostics
    assert "renderTraining" in diagnostics_js
    assert "renderMemory" in diagnostics_js
    assert "/api/training/export_teacher_packet" in diagnostics_js
    assert "/api/training/exports" in diagnostics_js
    assert "/api/training/reviews" in diagnostics_js
    assert "/api/logs/retention" in diagnostics_js
    assert "/api/desktop/readiness" in diagnostics_js
    assert "cleanupLocalModelsNow" in diagnostics_js
    assert "runLogCleanup" in diagnostics_js
    assert "recordAttemptReview" in diagnostics_js
    assert "crawlIntoKnowledge" in diagnostics_js
    assert "prepareSandbox" in diagnostics_js
    assert "/api/sandbox/prepare" in diagnostics_js
    assert "method_trace" in diagnostics_js
    assert "lesson_candidate" in diagnostics_js
    assert "exports-list" in diagnostics
    assert "knowledge" in diagnostics
    assert "crawl-url-action" in diagnostics
    assert "sandbox" in diagnostics
    assert "sandbox-prepare-action" in diagnostics
    assert "training-run-action" in diagnostics
    assert "run-log-viewer" in diagnostics
    assert "desktop-readiness-list" in diagnostics
    assert "desktop-close-cleanup-action" in diagnostics
    assert "log-retention-panel" in diagnostics
    assert "metodo/evidenza" in diagnostics
    assert "loadTrainingOverview" in js
    assert "topbar-command" in html
    # 2026-07-16: rimosse le pillole statiche agent-card (NAME/ROLE/TARGET/SHELL,
    # rumore) dalla topbar; restano progetto + sicurezza + stato live.
    assert "active-scope-label" in html
    assert "<h2>Agent</h2>" not in html
    assert "chat-delete-button" in js
    assert "deleteChat" in js
    assert "message-delete-button" in js
    assert "deleteChatMessage" in js
    assert "Training mode salva casi" not in html
    fast_app_text = Path("devin/ui/fast_app.py").read_text(encoding="utf-8")
    assert "cleanup_logs" in fast_app_text  # startup hook: resta in fast_app
    # Split 2026-07-18 (fetta 12): la superficie read-only dei run vive nel
    # router runs_read — il path /api/logs/cleanup si legge dal suo sorgente.
    runs_read_text = Path("devin/ui/routers/runs_read.py").read_text(encoding="utf-8")
    assert "/api/logs/cleanup" in runs_read_text
    # Split 2026-07-18: gli endpoint training CRUD vivono nel router dedicato;
    # le asserzioni sul loro codice leggono il sorgente del router.
    training_router_text = Path("devin/ui/routers/training.py").read_text(encoding="utf-8")
    assert "/api/training/reviews/structured" in training_router_text
    assert "TrainingReviewDecision" in training_router_text
    # Split 2026-07-18 (fetta 11): anche il crawl knowledge vive nel router
    # projects — le asserzioni leggono il sorgente del router.
    projects_router_text = Path("devin/ui/routers/projects.py").read_text(encoding="utf-8")
    assert "/api/project/knowledge/crawl" in projects_router_text
    assert "crawl_url_to_knowledge" in projects_router_text
    assert get_builtin_cases("devin-mini")
    assert any(item["id"] == "devin-mini" for item in list_builtin_benchmarks())

def test_chat_persistence_delete_message_endpoint_logic(tmp_path):
    from devin.core.chat_persistence import ChatPersistence

    cp = ChatPersistence(str(tmp_path))
    cp.save([
        {"role": "user", "content": "keep"},
        {"role": "assistant", "content": "delete me"},
    ])
    history = cp.load()
    removed = history.pop(1)
    cp.save(history)
    assert removed["content"] == "delete me"
    assert cp.load() == [{"role": "user", "content": "keep"}]


# ---------------------------------------------------------------------------
# FUZZY RECALL (2026-07-18): fallback a trigrammi per varianti morfologiche
# e testi misti IT/EN, solo quando l'overlap esatto dei token e' zero.
# ---------------------------------------------------------------------------

def _fuzzy_store(tmp_path, **cfg):
    from devin.ai.hybrid_memory_client import LocalMemoryStore
    return LocalMemoryStore({
        "local_memory": {"path": str(tmp_path / "mem.jsonl"), **cfg}
    })


def _store_verified(store, content, importance=0.5):
    store.store(content, tags=["status:verified_failure"], importance=importance)


def test_fuzzy_recall_matches_morphological_variants(tmp_path):
    """'quarantena/memoria' (IT) vs 'Quarantine/.../memory' (EN): zero token
    esatti in comune (>=3 char), ma trigrammi simili -> recall."""
    store = _fuzzy_store(tmp_path)
    _store_verified(store, "Quarantine bypass in remote memory recall filter")
    recalled = store.recall("errore quarantena memoria")
    assert len(recalled) == 1
    assert "Quarantine bypass" in recalled[0]


def test_fuzzy_recall_rejects_unrelated_query(tmp_path):
    store = _fuzzy_store(tmp_path)
    _store_verified(store, "Quarantine bypass in remote memory recall filter")
    assert store.recall("banana telephone network") == []


def test_fuzzy_recall_can_be_disabled(tmp_path):
    store = _fuzzy_store(tmp_path, fuzzy_recall=False)
    _store_verified(store, "Quarantine bypass in remote memory recall filter")
    assert store.recall("errore quarantena memoria") == []


def test_fuzzy_threshold_is_configurable(tmp_path):
    store = _fuzzy_store(tmp_path, fuzzy_threshold=0.9)
    _store_verified(store, "Quarantine bypass in remote memory recall filter")
    assert store.recall("errore quarantena memoria") == []


def test_fuzzy_never_widens_status_filter(tmp_path):
    """Un record pending_review ad alta similarita' resta ESCLUSO: il fuzzy
    allarga il recall, mai la sicurezza (filtro status applicato prima)."""
    store = _fuzzy_store(tmp_path)
    store.store("Quarantine bypass in remote memory recall filter",
                tags=["status:pending_review"])
    assert store.recall("errore quarantena memoria") == []


def test_exact_token_match_outranks_fuzzy_match(tmp_path):
    store = _fuzzy_store(tmp_path)
    _store_verified(store, "Quarantine bypass in remote memory recall filter")
    _store_verified(store, "errore verificato: quarantena aggirata dal filtro")
    recalled = store.recall("errore quarantena memoria")
    assert len(recalled) == 2
    assert "errore verificato" in recalled[0]  # match esatto primo
    assert "Quarantine bypass" in recalled[1]  # fuzzy secondo

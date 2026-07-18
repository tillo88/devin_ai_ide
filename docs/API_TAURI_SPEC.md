# DEVIN API Specification for Tauri Desktop App
**Version**: 1.0  
**Date**: 2026-07-14  
**Purpose**: Stable API contract between Tauri frontend (React/TS) and FastAPI backend

---

## Core Principles
- All responses use JSON unless explicitly documented
- Streaming endpoints use SSE (Server-Sent Events)
- All timestamps in ISO 8601 format
- Error responses: `{"error": "message", "code": "ERROR_CODE"}`

---

## Desktop Wrapper

### Tauri 2 brownfield shell
**Purpose**: First desktop wrapper for DEVIN. The desktop window loads the already-tested workspace shell at `http://127.0.0.1:5000/app`.

**Files**:
- `package.json`
- `src-tauri/tauri.conf.json`
- `src-tauri/Cargo.toml`
- `src-tauri/src/main.rs`
- `src-tauri/capabilities/default.json`
- `scripts/devin-tauri-dev.ps1`

**Current backend model**: FastAPI is started separately in WSL `Ubuntu`; sidecar startup is deferred to the next phase.

## Web/Tauri Shell Routes

### GET /app
**Purpose**: Development shell for the Codex-like workspace before the Tauri wrapper. It renders the local-first three-panel SPA (`workspace`, `conversation/work-stream`, `mind/context`) and keeps legacy `/`, `/chat`, and `/history` available for rollback.

**Frontend assets**:
- `/static/css/codex_app.css`
- `/static/js/codex_app.js`

**Initial data sources**:
- `GET /api/mind/status` for the right-side Mind panel
- `GET /api/runs` for recent run cards
- `GET /api/run/{run_id}/events` and `GET /api/run/{run_id}/events/stream` for the central run timeline
- `GET /api/workspace/projects` for the workspace project picker
- `GET /api/project/overview` for per-project conversation metadata
- `GET /api/chat/history` and `POST /api/project/chats/new` for conversation history and project chat creation
- `POST /api/chat` for the MVP in-workspace chat composer (SSE parsed from `fetch` response body, with optional `project_path` and `chat_id`)
- `POST /api/chat/document` for single document attachment in the workspace composer
- `POST /api/diff/preview` for read-only diff summaries in the center work-stream
- `POST /api/diff/apply` behind explicit browser confirmation in the workspace shell
- `GET /api/terminal/output` for read-only run logs in the center work-stream


## Health & Models

### GET /api/mind/status
**Purpose**: Lightweight structured state for the Codex-like right-side Mind panel. This endpoint must not perform slow remote health checks; use `/api/health` and `/api/models/status` for deeper probes.

**Response**:
```json
{
  "agent": {
    "name": "DEVIN",
    "role": "coding/debugging agent",
    "target_experience": "Codex/Claude Desktop-like local coding workspace",
    "desktop_shell_target": "Tauri"
  },
  "loop": ["perceive", "frame", "plan", "act", "verify", "reflect", "remember", "surface"],
  "capabilities": {
    "discussion": true,
    "project_context": true,
    "operational_scaffold_routing": true,
    "quality_gate": true,
    "eval_learning": true,
    "web_search": "tinyfish|searxng|none"
  },
  "models": {
    "health": {"checked": false, "see": "/api/health"},
    "launcher_source": "rig|local|unavailable",
    "local_running": {},
    "vram": {"gpu_name": "string", "total_mb": 16384, "used_mb": 4096, "free_mb": 12288}
  },
  "memory": {
    "schema_version": "memory.v1",
    "local": {"enabled": true, "path": "string", "records": 4},
    "backend": "local-first",
    "remote_checked": false,
    "recall_safe_statuses": ["human_confirmed", "verified_failure", "verified_success"],
    "review_only_statuses": ["hypothesis", "pending_review", "quarantine"]
  },
  "evals": {
    "active_detectors": ["scaffold_quality_gate", "chat_only_output_detector", "runtime_cuda_fallback_detector"],
    "failure_policy": "Save failures as negative lessons with evidence and retry rules."
  },
  "ui": {
    "current_surface": "FastAPI/Jinja web UI",
    "next_surface": "single-page Codex-like workspace",
    "future_shell": "Tauri",
    "panels": ["workspace", "conversation/work-stream", "mind/context"]
  }
}
```

### GET /api/health
**Purpose**: Check backend and model status  
**Response**:
```json
{
  "backend": "ok",
  "rig_available": true,
  "local_available": true,
  "model_source": "rig|local",
  "vram_usage_mb": 8500,
  "vram_free_mb": 7500
}
```

### GET /api/models/info
**Purpose**: Detailed model information  
**Response**:
```json
{
  "rig_available": true,
  "rig_host": "192.168.111.177",
  "rig_ports": [8080],
  "local_running": {
    "coder": {"port": 8000, "model": "qwen2.5-coder-7b"},
    "reasoning": {"port": 8001, "model": "qwen3-14b"}
  },
  "model_source": "rig",
  "vram": {
    "total_mb": 16384,
    "used_mb": 8500,
    "free_mb": 7884
  }
}
```

---

## Chat API

### POST /api/chat
**Purpose**: Send message to DEVIN (streaming response)  
**Request**:
```json
{
  "message": "string",
  "mode": "auto|chat|reasoning",
  "project_path": "string (optional)",
  "use_web_search": false,
  "chat_id": "string (optional, for project mode)"
}
```

**Response**: SSE stream with chunks  
**Events**: `message`, `done`, `error`

### GET /api/chat/history
**Purpose**: Load chat history for a project  
**Query**: `project_path`, `chat_id`  
**Response**:
```json
{
  "history": [
    {"role": "user|assistant", "content": "string", "timestamp": "ISO8601"}
  ],
  "updated_at": "ISO8601"
}
```

### POST /api/chat/history/clear
**Purpose**: Clear chat history  
**Request**: `{"project_path": "string", "chat_id": "string"}`

### POST /api/chat/search
**Purpose**: Force web search for a query  
**Request**: Same as /api/chat (forces web search)

---

## Run & Scaffold API

### POST /api/run
**Purpose**: Execute maintenance task on existing code  
**Request**:
```json
{
  "path": "string (project path)",
  "task": "string",
  "entrypoint": "string (optional)",
  "max_attempts": 3,
  "max_seconds": 300
}
```

**Response**:
```json
{
  "run_id": "uuid",
  "status": "started",
  "mode": "maintenance"
}
```

### POST /api/chat/scaffold
**Purpose**: Create new project from scratch (Zero-Shot Scaffolding)  
**Request**:
```json
{
  "message": "string (task description)",
  "project_path": "string (where to create)",
  "use_web_search": false
}
```

**Response**:
```json
{
  "run_id": "uuid",
  "status": "started",
  "mode": "scaffold"
}
```

### POST /api/chat/realize
**Purpose**: Apply chat conversation to project (skip planner)  
**Request**:
```json
{
  "project_path": "string",
  "chat_id": "string"
}
```

**Response**: Same as /api/run

### POST /api/stop
**Purpose**: Stop a running task  
**Request**: `{"run_id": "string"}`  
**Response**: `{"status": "stopped"}`

### GET /api/runs/active
**Purpose**: List currently active runs  
**Response**:
```json
{
  "active_run_ids": ["uuid1", "uuid2"]
}
```

### GET /api/runs
**Purpose**: List historical runs (last 50)  
**Response**:
```json
[
  {
    "run_id": "uuid",
    "project_path": "string",
    "task": "string",
    "status": "success|failed|running",
    "started_at": "ISO8601",
    "duration_seconds": 45
  }
]
```

### GET /api/run/{run_id}/events
**Purpose**: Return the structured JSON timeline for a run. This is the preferred data source for the Codex-like work stream and Mind panel.

**Query**:
- `after_seq` optional integer cursor
- `limit` optional integer, capped to 1000

**Response**:
```json
{
  "run_id": "run_20260715_...",
  "events": [
    {
      "seq": 0,
      "ts": "ISO8601",
      "run_id": "run_20260715_...",
      "type": "run_started|plan|act|verify|quality_gate_passed|memory|run_finished",
      "level": "info|warning|error|success",
      "message": "string",
      "data": {}
    }
  ]
}
```

### GET /api/run/{run_id}/events/stream
**Purpose**: SSE stream for structured run events. Keeps legacy `/stream/{run_id}` for plain-text log streaming.

**Events**: each SSE `data:` payload is one event record from `/api/run/{run_id}/events`; completion emits either `type=run_finished` or an SSE `done` event.

### GET /api/run/{run_id}/log
**Purpose**: Get run log  
**Query**: `download=1` for file download  
**Response**: Text log or file download

### GET /stream/{run_id}
**Purpose**: SSE stream for run progress  
**Events**: `log`, `file_written`, `error`, `done`

---

## Project API

### GET /api/project/overview
**Purpose**: Project metadata and structure  
**Query**: `project_path`  
**Response**:
```json
{
  "description": "string",
  "instructions": "string",
  "pins": ["file1.py", "file2.py"],
  "chats": [
    {"id": "uuid", "name": "string", "updated_at": "ISO8601"}
  ],
  "knowledge": [
    {"filename": "string", "size_bytes": 1234, "added_at": "ISO8601"}
  ]
}
```

### POST /api/project/instructions
**Purpose**: Set project instructions  
**Request**: `{"project_path": "string", "instructions": "string"}`

### POST /api/project/description
**Purpose**: Set project description  
**Request**: `{"project_path": "string", "description": "string"}`

### POST /api/project/pins/add
**Purpose**: Pin a file to always be in context  
**Request**: `{"project_path": "string", "file_path": "string"}`

### POST /api/project/pins/remove
**Purpose**: Remove a pinned file  
**Request**: `{"project_path": "string", "file_path": "string"}`

### POST /api/project/knowledge/upload
**Purpose**: Upload document to project knowledge  
**Request**: FormData with `project_path` and `file`

### POST /api/project/knowledge/from_url
**Purpose**: Add URL content to knowledge  
**Request**: `{"project_path": "string", "url": "string"}`

### POST /api/project/knowledge/delete
**Purpose**: Remove knowledge file  
**Request**: `{"project_path": "string", "filename": "string"}`

### POST /api/project/chats/new
**Purpose**: Create new chat in project  
**Request**: `{"project_path": "string", "name": "string"}`  
**Response**: `{"chat_id": "uuid"}`

### POST /api/project/chats/rename
**Purpose**: Rename a project chat  
**Request**: `{"project_path": "string", "chat_id": "string", "name": "string"}`

### POST /api/project/chats/delete
**Purpose**: Delete a project chat  
**Request**: `{"project_path": "string", "chat_id": "string"}`

### GET /api/project/last_run
**Purpose**: Get last run status for project  
**Query**: `project_path`  
**Response**:
```json
{
  "run_id": "uuid",
  "status": "success|failed",
  "task": "string",
  "started_at": "ISO8601",
  "duration_seconds": 45
}
```

---

## Memory API

### POST /api/project/memory/store
**Purpose**: Manually store to shared memory  
**Request**:
```json
{
  "project_path": "string",
  "content": "string",
  "tags": ["tag1", "tag2"],
  "importance": 0.8
}
```

**Response**:
```json
{
  "stored": true,
  "backend": "understory|automem",
  "id": "uuid"
}
```

### GET /api/memory/status
**Purpose**: Memory system status  
**Response**:
```json
{
  "enabled": true,
  "reachable": true,
  "backend": "understory",
  "local": {
    "enabled": true,
    "records": 5
  },
  "outbox": 0
}
```

---

## File Explorer API

### GET /api/explore
**Purpose**: List files in project directory  
**Query**: `path` (relative to project)  
**Response**:
```json
{
  "path": "string",
  "files": [
    {"name": "file.py", "type": "file", "size": 1234},
    {"name": "src", "type": "dir", "items": 5}
  ]
}
```

### GET /api/file
**Purpose**: Read file content  
**Query**: `path` (relative to project)  
**Response**:
```json
{
  "path": "string",
  "content": "string",
  "encoding": "utf-8"
}
```

### POST /api/file/save
**Purpose**: Save file content  
**Request**:
```json
{
  "path": "string",
  "content": "string"
}
```

---

## Workspace API

### GET /api/workspace/projects
**Purpose**: List all projects in workspace  
**Response**:
```json
{
  "projects": [
    {"name": "project1", "path": "/path/to/project1"}
  ],
  "workspace": "/path/to/workspace"
}
```

### POST /api/workspace/pick_folder
**Purpose**: Open folder picker dialog (Windows)  
**Response**:
```json
{
  "path": "\\wsl.localhost\\Ubuntu\\path\\to\\folder"
}
```

---

## Diff API

### POST /api/diff/preview
**Purpose**: Preview diff without applying it  
**Request**:
```json
{
  "project_path": "string",
  "patch_text": "string (unified diff format)"
}
```

**Response**:
```json
{
  "success": true,
  "files_affected": {
    "file.py": {
      "exists": true,
      "is_new": false,
      "additions": 5,
      "deletions": 2,
      "hunks_count": 1
    }
  },
  "total_files": 1,
  "total_additions": 5,
  "total_deletions": 2,
  "patch_lines": 15
}
```

### POST /api/diff/apply
**Purpose**: Apply diff to project  
**Request**: Same as /api/diff/preview  
**Response**:
```json
{
  "success": true,
  "tool": "git apply",
  "strip_level": 1,
  "applied": 1,
  "failed": []
}
```

---

## Plan API

### GET /api/plan/current
**Purpose**: Get current plan steps for active or recent run  
**Query**: `run_id`  
**Response**:
```json
{
  "run_id": "uuid",
  "status": "running|paused|completed",
  "plan": ["step 1", "step 2", "step 3"],
  "current_step": 0,
  "total_steps": 3,
  "task": "task description (truncated)"
}
```

### POST /api/plan/step/skip
**Purpose**: Skip a specific plan step  
**Request**:
```json
{
  "run_id": "uuid",
  "step_index": 1
}
```

**Response**:
```json
{
  "success": false,
  "error": "step skip not yet implemented - requires orchestrator refactoring"
}
```

**Note**: Currently placeholder - requires orchestrator refactoring for step-by-step execution

---

## Terminal API

### GET /api/terminal/output
**Purpose**: Get terminal output for a run (from log file)  
**Query**: `run_id`, `lines` (default: 100)  
**Response**:
```json
{
  "run_id": "uuid",
  "output": "last N lines from log",
  "total_lines": 500,
  "lines_returned": 100
}
```

### POST /api/terminal/input
**Purpose**: Send input to running terminal  
**Request**:
```json
{
  "run_id": "uuid",
  "input": "string"
}
```

**Response**:
```json
{
  "success": false,
  "error": "terminal input not yet implemented - requires runner refactoring"
}
```

**Note**: Currently placeholder - requires runner refactoring for process tracking and stdin injection

---

## Missing API (To Add)

### POST /api/plan/step/skip
**Purpose**: Skip a plan step  
**Status**: PLACEHOLDER - requires orchestrator refactoring

### POST /api/terminal/input
**Purpose**: Send input to running terminal  
**Status**: PLACEHOLDER - requires runner refactoring

---

## WebSocket Channels (Future)

### /ws/run/{run_id}
**Purpose**: Real-time run updates (alternative to SSE)  
**Status**: NOT IMPLEMENTED

### /ws/chat/{chat_id}
**Purpose**: Real-time chat updates  
**Status**: NOT IMPLEMENTED

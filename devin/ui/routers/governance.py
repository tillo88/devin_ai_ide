"""Read-only inventory for the cockpit governance workspace.

This endpoint deliberately reports only surfaces that exist in this backend.
Built-in HTTP capabilities are not presented as MCP servers, and an empty MCP
registry stays visibly unconfigured instead of being inferred as available.
"""

from fastapi import APIRouter

from devin.core.project_space import MAX_KNOWLEDGE_FILE_BYTES
from devin.ui.routers.explorer import (
    PROJECT_FILE_MAX_BYTES,
    PROJECT_TREE_MAX_DEPTH,
    PROJECT_TREE_MAX_FILES,
)


router = APIRouter()


@router.get("/api/tools/status")
async def api_tools_status():
    """Return the bounded, deny-by-default tool inventory without probing models."""
    return {
        "schema": "devin_tool_registry_v1",
        "policy": {
            "default": "deny",
            "scope": "registered_surfaces_only",
            "mutations": "explicit_human_action",
            "model_execution": False,
        },
        "external_mcp": {
            "status": "unconfigured",
            "registered_count": 0,
            "servers": [],
            "invocation_enabled": False,
        },
        "tools": [
            {
                "tool_id": "project_files",
                "kind": "builtin_http",
                "status": "enabled",
                "access": "read_only",
                "endpoints": ["GET /api/project/tree", "GET /api/project/file"],
                "guards": [
                    "validated_project_or_work_dir",
                    "sensitive_paths_excluded",
                    "symlinks_not_followed",
                ],
                "budgets": {
                    "max_files": PROJECT_TREE_MAX_FILES,
                    "max_depth": PROJECT_TREE_MAX_DEPTH,
                    "max_preview_bytes": PROJECT_FILE_MAX_BYTES,
                },
            },
            {
                "tool_id": "change_manifest",
                "kind": "builtin_http",
                "status": "enabled",
                "access": "review_gated_mutation",
                "endpoints": [
                    "GET /api/run/changes/{run_id}",
                    "POST /api/run/changes/apply",
                    "POST /api/run/changes/reject",
                ],
                "guards": [
                    "verified_sandbox_only",
                    "entry_digest_required_for_decision",
                    "decision_lock",
                    "stale_source_rejected",
                ],
                "budgets": {
                    "max_changed_file_bytes": 30 * 1024 * 1024,
                    "max_preview_file_bytes": 256_000,
                    "max_diff_chars": 500_000,
                },
            },
            {
                "tool_id": "run_log",
                "kind": "builtin_http",
                "status": "enabled",
                "access": "read_only",
                "endpoints": [
                    "GET /api/terminal/output",
                    "GET /api/run/{run_id}/events",
                ],
                "guards": ["safe_run_id", "contained_log_path", "tail_read_only"],
                "budgets": {"max_lines": 1000, "max_tail_bytes": 512_000},
            },
            {
                "tool_id": "knowledge_ingestion",
                "kind": "builtin_http",
                "status": "enabled",
                "access": "project_scoped_mutation",
                "endpoints": [
                    "POST /api/project/knowledge/upload",
                    "POST /api/project/knowledge/from_url",
                    "POST /api/project/knowledge/crawl",
                ],
                "guards": [
                    "validated_project",
                    "public_http_url_only",
                    "private_network_rejected",
                    "explicit_human_action",
                ],
                "budgets": {
                    "max_upload_bytes": MAX_KNOWLEDGE_FILE_BYTES,
                    "max_crawl_chars": 200_000,
                },
            },
            {
                "tool_id": "routing_plan",
                "kind": "builtin_http",
                "status": "enabled",
                "access": "plan_only",
                "endpoints": ["GET /api/routing/status", "POST /api/routing/plan"],
                "guards": ["automatic_switch_disabled", "broker_owned_model_slot"],
                "budgets": {},
            },
            {
                "tool_id": "terminal_input",
                "kind": "builtin_http",
                "status": "disabled_placeholder",
                "access": "none",
                "endpoints": ["POST /api/terminal/input"],
                "guards": ["not_exposed_by_cockpit"],
                "budgets": {},
            },
        ],
    }

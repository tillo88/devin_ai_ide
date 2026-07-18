"""Router diff: preview/apply di patch unified sul progetto.

Settimo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

Nessuna dipendenza da fast_app: `DiffRequest` e' usato solo qui e
`apply_patch` / `_parse_hunks` / `_clean_patch_text` arrivano diretti da
devin.engine.patcher. Nessun lazy import, nessuno shim.
"""

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from devin.engine.patcher import apply_patch

router = APIRouter()


class DiffRequest(BaseModel):
    project_path: str
    patch_text: str


@router.post("/api/diff/preview")
async def api_diff_preview(req: DiffRequest):
    """Preview a diff without applying it. Returns parsed hunks and affected files."""
    try:
        from devin.engine.patcher import _parse_hunks, _clean_patch_text

        project_path = Path(req.project_path).expanduser().resolve()
        if not project_path.exists():
            return {"error": "project path does not exist"}

        cleaned = _clean_patch_text(req.patch_text)
        hunks, new_files = _parse_hunks(cleaned)

        files_affected = {}
        for filepath, hunk in hunks:
            if not filepath:
                continue
            file_path = project_path / filepath
            exists = file_path.exists()
            is_new = filepath in new_files or not exists

            # Count changes
            additions = sum(1 for line in hunk if line.startswith("+"))
            deletions = sum(1 for line in hunk if line.startswith("-"))

            files_affected[filepath] = {
                "exists": exists,
                "is_new": is_new,
                "additions": additions,
                "deletions": deletions,
                "hunks_count": 1
            }

        return {
            "success": True,
            "files_affected": files_affected,
            "total_files": len(files_affected),
            "total_additions": sum(f["additions"] for f in files_affected.values()),
            "total_deletions": sum(f["deletions"] for f in files_affected.values()),
            "patch_lines": len(cleaned.splitlines())
        }
    except Exception as e:
        return {"error": f"diff preview failed: {e}"}


@router.post("/api/diff/apply")
async def api_diff_apply(req: DiffRequest):
    """Apply a diff to the project. Returns detailed result."""
    try:
        project_path = Path(req.project_path).expanduser().resolve()
        if not project_path.exists():
            return {"error": "project path does not exist"}

        result = apply_patch(req.patch_text, str(project_path))
        return result
    except Exception as e:
        return {"error": f"diff apply failed: {e}"}

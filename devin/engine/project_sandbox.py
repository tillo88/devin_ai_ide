"""Project-level sandbox preparation for risky DEVIN work.

This module creates an isolated copy of a real project plus a manifest. It is
intended for destructive/experimental agent work: run, install, mutate and test
inside the sandbox, then promote changes back only through explicit review/diff.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

DEFAULT_SANDBOX_ROOT = Path("workspace") / "_project_sandboxes"

DEFAULT_SKIP_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
    "target",
    "logs",
    "workspace",
    ".devin",
    ".devin_chat",
}

VENV_NAMES = {"venv", ".venv", "env", ".envdir"}
SECRET_PATTERNS = {
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "*secret*",
    "*token*",
    "tinyfish api.txt",
}
BINARY_HEAVY_PATTERNS = {
    "*.gguf",
    "*.safetensors",
    "*.pt",
    "*.pth",
    "*.onnx",
    "*.bin",
    "*.iso",
    "*.zip",
    "*.7z",
    "*.tar",
    "*.tar.gz",
}


@dataclass
class ProjectSandboxPolicy:
    include_venv: bool = False
    link_venv: bool = False
    include_secrets: bool = False
    include_large_binaries: bool = False
    max_file_size_mb: int = 50
    extra_skip_names: List[str] = field(default_factory=list)
    extra_skip_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "include_venv": self.include_venv,
            "link_venv": self.link_venv,
            "include_secrets": self.include_secrets,
            "include_large_binaries": self.include_large_binaries,
            "max_file_size_mb": self.max_file_size_mb,
            "extra_skip_names": list(self.extra_skip_names),
            "extra_skip_patterns": list(self.extra_skip_patterns),
        }


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:60] or "project"


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns)


def _skip_reason(path: Path, source_root: Path, policy: ProjectSandboxPolicy, sandbox_root: Path) -> str:
    name = path.name
    if path.resolve() == sandbox_root or sandbox_root in path.resolve().parents:
        return "sandbox_root_recursion"
    if path.is_symlink():
        return "symlink_skipped"
    if name in set(policy.extra_skip_names):
        return "extra_skip_name"
    if name in DEFAULT_SKIP_NAMES:
        return "default_skip_name"
    if name in VENV_NAMES and not policy.include_venv:
        return "venv_skipped_by_default"
    if _matches_any(name, policy.extra_skip_patterns):
        return "extra_skip_pattern"
    if not policy.include_secrets and _matches_any(name, SECRET_PATTERNS):
        return "secret_pattern_skipped"
    if not policy.include_large_binaries and _matches_any(name, BINARY_HEAVY_PATTERNS):
        return "large_binary_pattern_skipped"
    if path.is_file():
        max_bytes = max(1, int(policy.max_file_size_mb)) * 1024 * 1024
        try:
            if path.stat().st_size > max_bytes:
                return "file_too_large"
        except OSError:
            return "stat_failed"
    return ""


def _copy_tree(source: Path, target: Path, policy: ProjectSandboxPolicy, sandbox_root: Path) -> Dict[str, Any]:
    copied_files = 0
    copied_dirs = 0
    linked: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []

    def walk(src: Path, dst: Path) -> None:
        nonlocal copied_files, copied_dirs
        rel = str(src.relative_to(source))
        if src.is_dir() and src.name in VENV_NAMES and policy.link_venv and not policy.include_venv:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src, target_is_directory=True)
            linked.append({
                "path": rel,
                "target": str(src),
                "kind": "venv_symlink",
                "contract": "read_only_dependency_reference",
            })
            return
        reason = _skip_reason(src, source, policy, sandbox_root)
        if reason:
            skipped.append({"path": rel, "reason": reason})
            return
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            copied_dirs += 1
            for child in sorted(src.iterdir(), key=lambda item: item.name.lower()):
                walk(child, dst / child.name)
            return
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_files += 1

    for item in sorted(source.iterdir(), key=lambda entry: entry.name.lower()):
        walk(item, target / item.name)

    return {"copied_files": copied_files, "copied_dirs": copied_dirs, "linked": linked, "skipped": skipped}


def prepare_project_sandbox(
    project_path: str | Path,
    sandbox_root: str | Path = DEFAULT_SANDBOX_ROOT,
    policy: ProjectSandboxPolicy | None = None,
) -> Dict[str, Any]:
    """Create an isolated project copy and return a manifest dict.

    The source project is never modified. Existing sandbox directories are not
    reused; each call creates a timestamped sandbox for auditability.
    """
    policy = policy or ProjectSandboxPolicy()
    source = Path(project_path).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError(f"project_path is not a directory: {source}")

    root = Path(sandbox_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    sandbox_id = f"sandbox_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    target = root / f"{_slug(source.name)}_{sandbox_id}"
    target.mkdir(parents=True, exist_ok=False)

    result = _copy_tree(source, target, policy, root)
    manifest = {
        "schema_version": "project_sandbox_v1",
        "sandbox_id": sandbox_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(source),
        "sandbox_path": str(target),
        "policy": policy.to_dict(),
        "copied_files": result["copied_files"],
        "copied_dirs": result["copied_dirs"],
        "linked": result["linked"],
        "skipped": result["skipped"],
        "execution_policy": {
            "linked_dependencies_are_read_only_contract": bool(result["linked"]),
            "do_not_pip_install_into_linked_venv": bool(result["linked"]),
            "prefer_sandbox_local_venv_for_dependency_mutations": True,
        },
        "promotion_policy": {
            "auto_apply_to_source": False,
            "requires_diff_review": True,
            "source_is_read_only_contract": True,
        },
    }
    manifest_path = target / ".devin_sandbox_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def load_sandbox_manifest(path: str | Path) -> Dict[str, Any]:
    candidate = Path(path).expanduser().resolve()
    manifest_path = candidate if candidate.name.endswith(".json") else candidate / ".devin_sandbox_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"sandbox manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))

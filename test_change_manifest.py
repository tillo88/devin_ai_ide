import json
import shutil
from pathlib import Path

import pytest

from devin.core.change_manifest import (
    ChangeManifestError,
    apply_change_manifest,
    build_change_manifest,
    load_change_manifest,
    preview_change_manifest,
    reject_change_manifest,
    rollback_change_manifest,
)


def _trees(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    sandbox = project / "workspace" / "sandboxes" / "run_test"
    project.mkdir()
    (project / "keep.txt").write_text("same\n", encoding="utf-8")
    (project / "modify.txt").write_text("before\n", encoding="utf-8")
    (project / "delete.txt").write_text("remove me\n", encoding="utf-8")
    sandbox.mkdir(parents=True)
    for source in project.iterdir():
        if source.name != "workspace":
            shutil.copy2(source, sandbox / source.name)
    (sandbox / "modify.txt").write_text("after\n", encoding="utf-8")
    (sandbox / "delete.txt").unlink()
    (sandbox / "create.txt").write_text("created\n", encoding="utf-8")
    return project, sandbox


def test_manifest_is_sorted_and_excludes_runtime_state(tmp_path):
    project, sandbox = _trees(tmp_path)
    (project / ".devin_state").mkdir()
    (project / ".devin_state" / "noise.json").write_text("{}", encoding="utf-8")

    manifest = build_change_manifest(project, sandbox, "run_test")

    assert manifest["schema"] == "change_manifest_v1"
    assert [entry["path"] for entry in manifest["entries"]] == [
        "create.txt", "delete.txt", "modify.txt"
    ]
    assert manifest["counts"] == {"create": 1, "modify": 1, "delete": 1}
    assert load_change_manifest(project, "run_test")["entry_digest"] == manifest["entry_digest"]

    preview = preview_change_manifest(project, "run_test")
    assert preview["status"] == "pending"
    assert "--- a/modify.txt" in preview["unified_diff"]
    assert "+after" in preview["unified_diff"]
    assert [item["path"] for item in preview["entries"]] == [
        "create.txt", "delete.txt", "modify.txt"
    ]


def test_apply_and_rollback_round_trip(tmp_path):
    project, sandbox = _trees(tmp_path)
    build_change_manifest(project, sandbox, "run_test")

    applied = apply_change_manifest(project, "run_test")

    assert applied["status"] == "applied"
    assert (project / "modify.txt").read_text(encoding="utf-8") == "after\n"
    assert (project / "create.txt").read_text(encoding="utf-8") == "created\n"
    assert not (project / "delete.txt").exists()

    rolled_back = rollback_change_manifest(project, "run_test")

    assert rolled_back["status"] == "rolled_back"
    assert (project / "modify.txt").read_text(encoding="utf-8") == "before\n"
    assert (project / "delete.txt").read_text(encoding="utf-8") == "remove me\n"
    assert not (project / "create.txt").exists()


def test_apply_refuses_stale_source_without_partial_changes(tmp_path):
    project, sandbox = _trees(tmp_path)
    build_change_manifest(project, sandbox, "run_test")
    (project / "modify.txt").write_text("user edit\n", encoding="utf-8")

    with pytest.raises(ChangeManifestError, match="source changed after verification"):
        apply_change_manifest(project, "run_test")

    assert (project / "modify.txt").read_text(encoding="utf-8") == "user edit\n"
    assert not (project / "create.txt").exists()
    assert (project / "delete.txt").exists()


def test_apply_refuses_tampered_sandbox(tmp_path):
    project, sandbox = _trees(tmp_path)
    build_change_manifest(project, sandbox, "run_test")
    (sandbox / "modify.txt").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ChangeManifestError, match="sandbox changed after verification"):
        apply_change_manifest(project, "run_test")


def test_reject_is_terminal(tmp_path):
    project, sandbox = _trees(tmp_path)
    build_change_manifest(project, sandbox, "run_test")

    rejected = reject_change_manifest(project, "run_test")

    assert rejected["status"] == "rejected"
    with pytest.raises(ChangeManifestError, match="not pending"):
        apply_change_manifest(project, "run_test")


def test_manifest_integrity_rejects_entry_edits(tmp_path):
    project, sandbox = _trees(tmp_path)
    build_change_manifest(project, sandbox, "run_test")
    path = project / ".devin_state" / "pending_changes" / "run_test" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["path"] = "../escape"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ChangeManifestError, match="integrity"):
        load_change_manifest(project, "run_test")


def test_manifest_never_promotes_env_or_private_key_files(tmp_path):
    project, sandbox = _trees(tmp_path)
    (project / ".env").write_text("TOKEN=old\n", encoding="utf-8")
    (sandbox / ".env").write_text("TOKEN=stolen\n", encoding="utf-8")
    (sandbox / "signing.key").write_text("private\n", encoding="utf-8")

    manifest = build_change_manifest(project, sandbox, "run_sensitive")

    paths = {entry["path"] for entry in manifest["entries"]}
    assert ".env" not in paths
    assert "signing.key" not in paths


def test_manifest_rejects_changed_files_over_promotion_limit(tmp_path):
    project, sandbox = _trees(tmp_path)
    (sandbox / "large.bin").write_bytes(b"x" * 65)

    with pytest.raises(ChangeManifestError, match="exceed promotion limit"):
        build_change_manifest(
            project, sandbox, "run_large", max_file_bytes=64
        )


def test_decision_lock_blocks_concurrent_decisions_portably(tmp_path):
    """Regression migrazione Windows 2026-07-21: il lock decisionale deve
    funzionare sull'OS corrente (msvcrt su nt, fcntl altrove) e respingere
    una seconda decisione concorrente con errore pulito, senza import fcntl
    a livello modulo."""
    from devin.core import change_manifest as cm

    project, sandbox = _trees(tmp_path)
    build_change_manifest(project, sandbox, "run_test")

    with cm._decision_lock(project, "run_test"):
        with pytest.raises(ChangeManifestError, match="already in progress"):
            with cm._decision_lock(project, "run_test"):
                pass  # pragma: no cover

    # Rilasciato il lock, una decisione reale deve procedere.
    applied = apply_change_manifest(project, "run_test")
    assert applied["status"] == "applied"

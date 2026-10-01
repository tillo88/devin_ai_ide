import asyncio
import io
import json
import stat
import zipfile

import pytest

from devin.ui.routers import local_workspace as bridge


def _zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return output.getvalue()


def test_snapshot_extracts_only_safe_relative_files(tmp_path):
    raw = _zip([("src/main.py", b"print('ok')\n"), ("README.md", b"hello\n")])
    target = tmp_path / "snapshot"

    counts = bridge._extract_snapshot(raw, target)

    assert counts == {"files": 2, "bytes": 18}
    assert (target / "src" / "main.py").read_bytes() == b"print('ok')\n"
    assert (target / "README.md").read_bytes() == b"hello\n"


@pytest.mark.parametrize(
    "name",
    ["../escape.txt", "C:/escape.txt", ".git/config", ".env", ".npmrc", ".ssh/config", "keys/operator.pem", "node_modules/x.js"],
)
def test_snapshot_rejects_escape_generated_and_sensitive_members(tmp_path, name):
    with pytest.raises(bridge.LocalWorkspaceError):
        bridge._extract_snapshot(_zip([(name, b"secret")]), tmp_path / "snapshot")


def test_snapshot_rejects_symlinks(tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("src/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../outside")

    with pytest.raises(bridge.LocalWorkspaceError, match="symlink"):
        bridge._extract_snapshot(output.getvalue(), tmp_path / "snapshot")


def test_exclusion_bench_detects_a_deliberate_filter_mutation(monkeypatch):
    """Mutation proof: removing the exact guard must make the bad member pass."""
    with pytest.raises(bridge.LocalWorkspaceError):
        bridge._safe_member(".git/config")

    mutated = set(bridge.EXCLUDED_PARTS)
    mutated.remove(".git")
    monkeypatch.setattr(bridge, "EXCLUDED_PARTS", mutated)
    assert ".git" not in bridge.EXCLUDED_PARTS  # substitution really applied
    assert bridge._safe_member(".git/config").as_posix() == ".git/config"


def test_bridge_and_digest_identifiers_are_strict():
    assert bridge._bridge_id("12345678-1234-4234-9234-123456789abc")
    assert bridge._digest("ab" * 32) == "ab" * 32
    with pytest.raises(bridge.LocalWorkspaceError):
        bridge._bridge_id("not-a-uuid")
    with pytest.raises(bridge.LocalWorkspaceError):
        bridge._digest("abc")


def test_direct_registration_keeps_only_opaque_metadata(tmp_path, monkeypatch):
    from devin.ui import fast_app

    class Request:
        async def json(self):
            return {
                "bridge_id": "12345678-1234-4234-9234-123456789abc",
                "display_name": "Windows project",
                "project_path": "",
            }

    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path)
    result = asyncio.run(bridge.api_local_workspace_register(Request()))

    assert result["status"] == "registered"
    assert result["local_workspace"]["mode"] == "direct"
    assert result["local_workspace"]["snapshot_digest"] == ""
    assert result["local_workspace"]["files"] == 0
    metadata_path = tmp_path / "Windows project" / ".devin" / "local_workspace.json"
    persisted = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert persisted["bridge_id"] == "12345678-1234-4234-9234-123456789abc"
    assert "local_path" not in persisted
    assert not (tmp_path / "_local_mirrors").exists()


def test_public_metadata_defaults_legacy_records_to_snapshot(tmp_path):
    metadata = tmp_path / ".devin" / "local_workspace.json"
    metadata.parent.mkdir()
    metadata.write_text(json.dumps({
        "schema": bridge.SNAPSHOT_SCHEMA,
        "bridge_id": "12345678-1234-4234-9234-123456789abc",
        "display_name": "legacy",
    }), encoding="utf-8")

    assert bridge._public_metadata(tmp_path)["mode"] == "snapshot"

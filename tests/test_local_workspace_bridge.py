import io
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

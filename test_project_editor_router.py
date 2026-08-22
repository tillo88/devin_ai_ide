"""Security and API contracts for the C3.1 read-only project editor."""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from devin.core.project_space import ProjectSpace
from devin.ui import fast_app
from devin.ui.routers import explorer


def test_project_editor_routes_are_registered_without_scoped_writer():
    routes = {
        (route.path, method)
        for route in explorer.router.routes
        for method in getattr(route, "methods", set())
    }

    assert ("/api/project/tree", "GET") in routes
    assert ("/api/project/file", "GET") in routes
    assert not any(path == "/api/project/file/save" for path, _method in routes)


def test_project_tree_is_relative_bounded_and_excludes_runtime_and_secrets(tmp_path: Path):
    root = tmp_path / "source"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".devin").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / ".github" / "workflows" / "test.yml").write_text("name: test\n", encoding="utf-8")
    (root / ".git" / "config").write_text("secret-ish\n", encoding="utf-8")
    (root / ".devin" / "memory.json").write_text("{}\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=never-expose\n", encoding="utf-8")
    (root / "private.pem").write_text("never-expose\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    files, truncated = explorer._scan_project_tree(root)
    paths = {item["path"] for item in files}

    assert not truncated
    assert paths == {".github/workflows/test.yml", "logo.png", "src/main.py"}
    assert all("full_path" not in item for item in files)
    assert all(not Path(item["path"]).is_absolute() for item in files)
    assert next(item for item in files if item["path"] == "src/main.py")["is_text"] is True
    assert next(item for item in files if item["path"] == "logo.png")["is_text"] is False


def test_project_tree_hard_caps_results(tmp_path: Path):
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")

    files, truncated = explorer._scan_project_tree(tmp_path, max_files=2)

    assert len(files) == 2
    assert truncated is True


def test_execution_root_routes_to_separately_validated_work_dir(monkeypatch, tmp_path: Path):
    project = tmp_path / "metadata-project"
    work_dir = tmp_path / "linked-source"
    project.mkdir()
    work_dir.mkdir()
    ProjectSpace(str(project)).set_work_dir(str(work_dir))
    validated = []

    def allow(path, allow_general=False):
        assert allow_general is False
        validated.append(str(Path(path).resolve()))
        return str(Path(path).resolve())

    monkeypatch.setattr(fast_app, "_validated_project_path", allow)

    root, scope = explorer._resolve_project_execution_root(str(project))

    assert root == work_dir.resolve()
    assert scope == "work_dir"
    assert validated == [str(project.resolve()), str(work_dir.resolve())]


def test_project_file_rejects_traversal_absolute_sensitive_and_binary(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "ok.txt").write_text("hello\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=nope\n", encoding="utf-8")
    binary = root / "binary.dat"
    binary.write_bytes(b"abc\x00def")

    for unsafe in ("../outside.txt", "/etc/passwd", "C:\\Windows\\win.ini", ".env"):
        with pytest.raises(HTTPException) as exc_info:
            explorer._safe_project_relative_file(root, unsafe)
        assert exc_info.value.status_code in (400, 403)

    target, relative = explorer._safe_project_relative_file(root, "ok.txt")
    assert target == (root / "ok.txt").resolve()
    assert relative == "ok.txt"

    with pytest.raises(HTTPException) as exc_info:
        explorer._read_project_text_file(binary)
    assert exc_info.value.status_code == 415


def test_project_file_rejects_symlink_escape_when_supported(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available to this Windows user")

    with pytest.raises(HTTPException) as exc_info:
        explorer._safe_project_relative_file(root, "escape.txt")
    assert exc_info.value.status_code == 403


def test_project_file_api_is_bounded_utf8_and_read_only(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    payload = "a" * (explorer.PROJECT_FILE_MAX_BYTES + 50)
    (source / "large.py").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(
        explorer,
        "_resolve_project_execution_root",
        lambda _project_path: (source.resolve(), "work_dir"),
    )

    result = asyncio.run(explorer.api_project_file("metadata-project", "large.py"))

    assert result["path"] == "large.py"
    assert result["scope"] == "work_dir"
    assert result["read_only"] is True
    assert result["truncated"] is True
    assert len(result["content"].encode("utf-8")) == explorer.PROJECT_FILE_MAX_BYTES
    assert "full_path" not in result


def test_project_file_truncation_does_not_split_valid_utf8(tmp_path: Path):
    target = tmp_path / "unicode.txt"
    target.write_bytes(b"a" * (explorer.PROJECT_FILE_MAX_BYTES - 1) + "€".encode("utf-8") + b"tail")

    content, truncated, size = explorer._read_project_text_file(target)

    assert truncated is True
    assert content == "a" * (explorer.PROJECT_FILE_MAX_BYTES - 1)
    assert size > explorer.PROJECT_FILE_MAX_BYTES


def test_project_tree_api_does_not_leak_root_path(monkeypatch, tmp_path: Path):
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    monkeypatch.setattr(
        explorer,
        "_resolve_project_execution_root",
        lambda _project_path: (tmp_path.resolve(), "project"),
    )

    result = asyncio.run(explorer.api_project_tree("metadata-project"))

    assert result["root_name"] == tmp_path.name
    assert result["files"][0]["path"] == "README.md"
    assert str(tmp_path.resolve()) not in str(result)

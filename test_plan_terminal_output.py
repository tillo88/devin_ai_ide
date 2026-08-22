import asyncio
import os

import pytest


def _set_log_dir(monkeypatch, tmp_path):
    from devin.ui import fast_app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(fast_app, "LOG_DIR", log_dir)
    return log_dir


def test_terminal_output_returns_bounded_tail(monkeypatch, tmp_path):
    from devin.ui.routers.plan_terminal import api_terminal_output

    log_dir = _set_log_dir(monkeypatch, tmp_path)
    (log_dir / "run_tail.log").write_text(
        "".join(f"line {index}\n" for index in range(12)),
        encoding="utf-8",
    )

    result = asyncio.run(api_terminal_output("run_tail", lines=3))

    assert result["schema"] == "devin_terminal_tail_v2"
    assert result["output"].splitlines() == ["line 9", "line 10", "line 11"]
    assert result["lines_returned"] == 3
    assert result["truncated"] is True
    assert result["tail_bytes"] == result["file_size"]


def test_terminal_output_rejects_unsafe_run_id(monkeypatch, tmp_path):
    from devin.ui.routers.plan_terminal import api_terminal_output

    _set_log_dir(monkeypatch, tmp_path)

    result = asyncio.run(api_terminal_output("../escape", lines=10))

    assert result == {"error": "invalid run_id or lines", "output": ""}


def test_terminal_output_caps_bytes_before_decoding(monkeypatch, tmp_path):
    from devin.ui.routers.plan_terminal import api_terminal_output

    log_dir = _set_log_dir(monkeypatch, tmp_path)
    (log_dir / "run_large.log").write_bytes(
        (b"ignored-prefix\n" * 10_000) + (b"recent\n" * 80_000)
    )

    result = asyncio.run(api_terminal_output("run_large", lines=1000))

    assert result["tail_bytes"] <= 512_000
    assert result["lines_returned"] == 1000
    assert result["truncated"] is True
    assert len(result["output"].encode("utf-8")) < 512_000


def test_terminal_output_rejects_symlink_escape_when_supported(monkeypatch, tmp_path):
    from devin.ui.routers.plan_terminal import api_terminal_output

    log_dir = _set_log_dir(monkeypatch, tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text("secret\n", encoding="utf-8")
    link = log_dir / "run_link.log"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink non disponibile: {exc}")

    result = asyncio.run(api_terminal_output("run_link", lines=10))

    assert result == {"error": "log file not found", "output": ""}

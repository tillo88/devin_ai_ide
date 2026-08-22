"""Regression tests for DEVIN's no-NVML-by-default runtime policy."""

from __future__ import annotations

from types import SimpleNamespace

from devin.ai import local_model_launcher as launcher


def test_nvml_queries_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DEVIN_ALLOW_NVML_TELEMETRY", raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("nvidia-smi must not run without explicit opt-in")

    monkeypatch.setattr(launcher.subprocess, "run", forbidden)

    assert launcher.nvml_telemetry_allowed() is False
    assert launcher._get_vram_mb() is None
    assert launcher._get_vram_used_percent() is None
    assert launcher.get_vram_status()["is_critical"] is False


def test_nvml_query_requires_explicit_local_opt_in(monkeypatch):
    monkeypatch.setenv("DEVIN_ALLOW_NVML_TELEMETRY", "1")
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if "memory.free" in argv[1]:
            return SimpleNamespace(stdout="4096\n")
        return SimpleNamespace(stdout="2048, 8192\n")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    assert launcher._get_vram_mb() == 4096
    assert launcher._get_vram_used_percent() == 25.0
    assert len(calls) == 2


def test_rig_primary_vetoes_watchdog_even_with_environment_opt_in(monkeypatch):
    monkeypatch.setenv("DEVIN_ALLOW_NVML_TELEMETRY", "true")
    monkeypatch.setattr(launcher, "_vram_watchdog_thread", None)

    assert launcher.nvml_telemetry_allowed({"rig_primary": True}) is False
    assert launcher.start_vram_watchdog(models_cfg={"rig_primary": True}) is False
    assert launcher._vram_watchdog_thread is None


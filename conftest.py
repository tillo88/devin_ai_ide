"""Impostazioni comuni dei test."""
import pytest


@pytest.fixture(autouse=True)
def _richieste_in_tmp(tmp_path, monkeypatch):
    # AIClient registra su disco ogni richiesta al modello: nei test, mai nella home vera.
    monkeypatch.setenv("DEVIN_RICHIESTE_DIR", str(tmp_path / "richieste"))

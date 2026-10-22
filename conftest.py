"""Impostazioni comuni dei test."""
import pytest


@pytest.fixture(autouse=True)
def _richieste_in_tmp(tmp_path, monkeypatch):
    # AIClient registra su disco ogni richiesta al modello: nei test, mai nella home vera.
    monkeypatch.setenv("DEVIN_RICHIESTE_DIR", str(tmp_path / "richieste"))
    # Il backend all'avvio riscalda la cache del modello (21/10): nei test mai.
    monkeypatch.setenv("DEVIN_RISCALDAMENTO", "0")
    # nei test nessuno riavvierebbe il processo: il riavvio col codice nuovo resta spento
    monkeypatch.setenv("DEVIN_RIAVVIO_AUTOMATICO", "0")
    # lezioni e richieste di miglioria: nei test mai nella home vera
    monkeypatch.setenv("DEVIN_LEZIONI_DIR", str(tmp_path / "lezioni"))

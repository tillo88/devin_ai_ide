"""Test del bootstrap settings.json dal template (config per-macchina non tracciata)."""

from __future__ import annotations

import json
from pathlib import Path

from devin.core.settings_bootstrap import ensure_settings


def test_crea_da_template_se_manca(tmp_path: Path):
    (tmp_path / "settings.example.json").write_text('{"a": 1}', encoding="utf-8")
    cfg = tmp_path / "settings.json"
    assert not cfg.exists()
    ensure_settings(cfg)
    assert cfg.exists()
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"a": 1}


def test_no_op_se_esiste(tmp_path: Path):
    (tmp_path / "settings.example.json").write_text('{"a": 1}', encoding="utf-8")
    cfg = tmp_path / "settings.json"
    cfg.write_text('{"a": 999}', encoding="utf-8")  # config locale personalizzata
    ensure_settings(cfg)
    # NON deve sovrascrivere la config della macchina (es. rig_self_hosted sul rig)
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"a": 999}


def test_niente_template_non_crasha(tmp_path: Path):
    cfg = tmp_path / "settings.json"
    ensure_settings(cfg)  # nessun example accanto -> non crea, non solleva
    assert not cfg.exists()


def test_repo_example_esiste_e_combacia():
    # Nel repo: settings.example.json esiste ed e' JSON valido.
    root = Path(__file__).resolve().parent
    example = root / "config" / "settings.example.json"
    assert example.exists()
    data = json.loads(example.read_text(encoding="utf-8"))
    assert "models" in data  # ha la struttura attesa

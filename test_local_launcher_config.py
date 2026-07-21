"""Regression per _apply_models_config (profilo LOCALE Windows, 2026-07-21).

Su nt hanno precedenza le chiavi *_windows; solo path esistenti sovrascrivono
i default; il cambio di local_models_dir ri-seleziona coder/planner in base
ai file realmente presenti (primario Ornith o fallback Qwen).
"""
import importlib

import devin.ai.local_model_launcher as lml


def _reload():
    return importlib.reload(lml)


def test_windows_keys_take_precedence_and_missing_paths_are_ignored(tmp_path):
    mod = _reload()
    original_bin = mod.LLAMA_SERVER_BIN

    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"")

    mod._apply_models_config(
        {
            "llama_server_path": "/percorso/wsl/inesistente/llama-server",
            "llama_server_path_windows": str(server),
        },
        platform_name="nt",
    )
    assert mod.LLAMA_SERVER_BIN == server

    # Chiave presente ma path inesistente: il default non viene toccato.
    mod = _reload()
    assert mod.LLAMA_SERVER_BIN == original_bin
    mod._apply_models_config(
        {"llama_server_path_windows": str(tmp_path / "manca.exe")},
        platform_name="nt",
    )
    assert mod.LLAMA_SERVER_BIN == original_bin


def test_posix_ignores_windows_keys(tmp_path):
    mod = _reload()
    original_bin = mod.LLAMA_SERVER_BIN
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"")

    mod._apply_models_config(
        {"llama_server_path_windows": str(server)},
        platform_name="posix",
    )
    assert mod.LLAMA_SERVER_BIN == original_bin


def test_models_dir_reselects_coder_between_primary_and_fallback(tmp_path):
    mod = _reload()

    # Solo il fallback Qwen presente nella nuova dir: coder -> Qwen, jinja off.
    (tmp_path / mod.CODER_QWEN_FALLBACK.name).write_bytes(b"")
    mod._apply_models_config(
        {"local_models_dir_windows": str(tmp_path)}, platform_name="nt")
    assert mod.MODELS["coder"]["file"] == tmp_path / mod.CODER_QWEN_FALLBACK.name
    assert mod.MODELS["coder"]["jinja"] is False

    # Arriva anche Ornith: ri-applicando, coder -> Ornith, jinja on.
    (tmp_path / mod.CODER_ORNITH.name).write_bytes(b"")
    mod._apply_models_config(
        {"local_models_dir_windows": str(tmp_path)}, platform_name="nt")
    assert mod.MODELS["coder"]["file"] == tmp_path / mod.CODER_ORNITH.name
    assert mod.MODELS["coder"]["jinja"] is True

    # Ripristina lo stato del modulo per gli altri test.
    _reload()


def test_rig_health_probe(monkeypatch):
    mod = _reload()

    class _Resp:
        status_code = 200

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp())
    assert mod._rig_is_healthy({"rig_host": "192.0.2.1"}) is True

    def _boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(mod.requests, "get", _boom)
    assert mod._rig_is_healthy({"rig_host": "192.0.2.1"}) is False
    # Senza host configurato: mai True.
    assert mod._rig_is_healthy({}) is False


def test_ensure_models_skips_local_when_rig_healthy(monkeypatch):
    """Policy owner 2026-07-21: rig up -> niente locale (VRAM libera),
    rig down -> fallback locale."""
    mod = _reload()
    instance = mod.LocalModelLauncher()
    instance._models_cfg = {
        "rig_primary": True, "rig_host": "192.0.2.1", "rig_port": 8080}

    monkeypatch.setattr(mod, "_rig_is_healthy", lambda cfg, timeout=3: True)
    started = []
    monkeypatch.setattr(mod, "ensure_model_running",
                        lambda alias, cfg: started.append(alias) or True)

    status = instance.ensure_models()
    assert status.model_source == "rig"
    assert status.rig_available is True
    assert status.local_running == {}
    assert started == []  # nessun modello locale avviato

    # Rig giu': il fallback locale DEVE partire.
    monkeypatch.setattr(mod, "_rig_is_healthy", lambda cfg, timeout=3: False)
    monkeypatch.setattr(mod, "is_model_fully_loaded",
                        lambda port, alias: (False, "not loaded"))
    monkeypatch.setattr(mod, "_running_model_info", lambda alias: {"alias": alias})
    status_down = instance.ensure_models()
    assert started  # avvio locale tentato
    assert status_down.model_source == "local"
    _reload()

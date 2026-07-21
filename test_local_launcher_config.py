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

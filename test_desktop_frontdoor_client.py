"""Contracts for the Windows thin client and authenticated rig front door."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUST = ROOT / "src-tauri" / "src" / "main.rs"
BOOTSTRAP = ROOT / "devin" / "ui" / "static" / "js" / "desktop_bootstrap.js"
LAUNCHER = ROOT / "scripts" / "launch-windows-desktop-host.ps1"
PREPARE = ROOT / "scripts" / "prepare-windows-desktop-host.ps1"
CONFIGURE = ROOT / "scripts" / "configure-windows-desktop.ps1"


def test_desktop_bootstrap_is_rig_frontdoor_only():
    rust = RUST.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    assert "connect_frontdoor" in rust
    assert "DEVIN_FRONTDOOR_URL" in rust
    assert "DEVIN_FRONTDOOR_TOKEN" in rust
    assert 'join("DEVIN").join(CONFIG_FILE)' in rust
    assert '.append_pair("token", &config.access_token)' in rust
    assert ".navigate(access_url(&config))" in rust
    assert "start_local_backend" not in rust
    assert "DEVIN_BACKEND_EXE" not in rust
    assert "127.0.0.1:5000" not in rust

    assert 'tauriInvoke("connect_frontdoor")' in bootstrap
    assert "access_token" not in bootstrap
    assert "127.0.0.1:5000" not in bootstrap
    assert "start_local_backend" not in bootstrap
    assert "textContent = detail" in bootstrap


def test_windows_launcher_does_not_start_wsl_or_a_local_backend():
    launcher = LAUNCHER.read_text(encoding="utf-8")
    prepare = PREPARE.read_text(encoding="utf-8")

    for forbidden in (
        "devin-tauri-dev.ps1",
        "start-fastapi-headless.sh",
        "WslRepo",
        "BrowserFallback",
        "127.0.0.1:5000",
    ):
        assert forbidden not in launcher
        assert forbidden not in prepare

    assert "installed desktop host already synchronized" in launcher
    assert "configure-windows-desktop.ps1" in prepare
    assert '-SourceRepo "$SourceRepo"' not in prepare


def test_desktop_config_helper_protects_token_and_validates_input():
    helper = CONFIGURE.read_text(encoding="utf-8")

    assert "[Security.SecureString]$AccessToken" in helper
    assert 'Read-Host "Token del frontdoor DEVIN (non verra\' mostrato)" -AsSecureString' in helper
    assert '"/inheritance:r"' in helper
    assert "S-1-5-18" in helper
    assert 'schema = "devin_desktop_frontdoor_v1"' in helper
    assert "Write-Host $plainToken" not in helper


def test_tauri_bundle_has_no_local_backend_resource():
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "resources" not in tauri["bundle"]
    assert "rig front door" in package["description"]
    assert "BrowserFallback" not in package["scripts"]["desktop:windows-host"]
    assert "desktop:configure" in package["scripts"]

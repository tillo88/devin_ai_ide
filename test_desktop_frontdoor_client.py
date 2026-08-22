"""Contracts for the Windows thin client and authenticated rig front door."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUST = ROOT / "src-tauri" / "src" / "main.rs"
BOOTSTRAP = ROOT / "devin" / "ui" / "static" / "js" / "desktop_bootstrap.js"
LAUNCHER = ROOT / "scripts" / "launch-windows-desktop-host.ps1"
PREPARE = ROOT / "scripts" / "prepare-windows-desktop-host.ps1"
CONFIGURE = ROOT / "scripts" / "configure-windows-desktop.ps1"
BUILD = ROOT / "scripts" / "build-windows-installer.ps1"
CACHE = ROOT / "scripts" / "manage-desktop-build-cache.ps1"
BUILD_ENV = ROOT / "scripts" / "desktop-build-env.ps1"


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
    assert tauri["bundle"]["targets"] == ["nsis", "msi"]
    assert tauri["bundle"]["windows"]["allowDowngrades"] is False
    assert tauri["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"


def test_windows_release_uses_one_external_cargo_cache_and_thin_artifacts():
    build = BUILD.read_text(encoding="utf-8")
    cache = CACHE.read_text(encoding="utf-8")
    build_env = BUILD_ENV.read_text(encoding="utf-8")

    assert '"build-cache\\cargo-target"' in build_env
    assert "$env:CARGO_TARGET_DIR = $target" in build_env
    assert "la cache non puo' stare dentro il checkout" in build_env
    assert "--bundles" in build
    assert 'bundled_backend = $false' in build
    assert 'bundled_models = $false' in build
    assert 'schema = "devin_windows_release_v1"' in build
    assert "Get-FileHash" in build
    assert "desktop.json" not in build
    assert "access_token" not in build

    assert 'ValidateSet("status", "clean-cache", "clean-legacy", "clean-all")' in cache
    assert "Assert-ExactDesktopPath" in cache
    assert "git -C $SourceRepo ls-files -- src-tauri/target" in cache
    assert "Remove-Item -LiteralPath $safePath" in cache
    assert "Move-Item" not in cache
    assert "Release rifiutata" in build
    assert "source_dirty = $sourceDirty" in build

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
FRONTEND_BUILD = ROOT / "scripts" / "build-frontend-bundle.ps1"


def test_desktop_bootstrap_is_rig_frontdoor_only():
    rust = RUST.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    assert "connect_frontdoor" in rust
    assert "desktop_config_status" in rust
    assert "test_frontdoor_connection" in rust
    assert "save_frontdoor_config" in rust
    assert "DEVIN_FRONTDOOR_URL" in rust
    assert "DEVIN_FRONTDOOR_TOKEN" in rust
    assert 'join("DEVIN").join(CONFIG_FILE)' in rust
    assert '.append_pair("token", &config.access_token)' in rust
    assert ".navigate(access_url(&config))" in rust
    assert "start_local_backend" not in rust
    assert "DEVIN_BACKEND_EXE" not in rust
    assert "127.0.0.1:5000" not in rust

    assert 'tauriInvoke("connect_frontdoor")' in bootstrap
    assert 'tauriInvoke("desktop_config_status")' in bootstrap
    assert 'tauriInvoke("test_frontdoor_connection"' in bootstrap
    assert 'tauriInvoke("save_frontdoor_config"' in bootstrap
    assert "access_token" not in bootstrap
    assert "127.0.0.1:5000" not in bootstrap
    assert "start_local_backend" not in bootstrap
    assert "textContent = detail" in bootstrap
    assert "innerHTML" not in bootstrap
    assert 'token.value = ""' in bootstrap
    assert "Test senza attivare" in bootstrap
    assert 'token.autocomplete = "off"' in bootstrap
    assert "token.required = !configured" in bootstrap


def test_native_onboarding_keeps_credentials_in_rust_and_uses_atomic_acl_write():
    rust = RUST.read_text(encoding="utf-8")
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")

    assert 'Command::new("whoami.exe")' in rust
    assert 'Command::new("icacls.exe")' in rust
    assert '"*S-1-5-18:(OI)(CI)F"' in rust
    assert "MoveFileExW" in rust
    assert "MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH" in rust
    assert "create_new(true)" in rust
    assert "file.sync_all()" in rust
    assert 'reject_symlink(&path, "file")' in rust
    assert 'reject_symlink(directory, "directory")' in rust
    assert "status_snapshot_never_exposes_the_token" in rust
    assert "persists_config_atomically_without_leaving_temporary_files" in rust
    assert "applies_user_and_system_acl_to_a_temporary_directory" in rust
    assert 'windows-sys = { version = "0.61"' in cargo
    assert "println!" not in rust


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
    assert tauri["version"] == "0.2.0"
    assert package["version"] == tauri["version"]


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


def test_powershell_frontend_builder_preserves_root_pwa_assets():
    builder = FRONTEND_BUILD.read_text(encoding="utf-8")

    assert '@("sw.js", "manifest.webmanifest")' in builder
    assert 'Join-Path $output $extra' in builder

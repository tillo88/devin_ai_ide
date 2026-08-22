from pathlib import Path


ROOT = Path(__file__).resolve().parent
SEARX = ROOT / "scripts" / "rig" / "searxng"


def test_compose_is_pinned_loopback_and_not_usb_bound():
    compose = (SEARX / "docker-compose.yml").read_text()
    assert "searxng/searxng@sha256:" in compose
    assert "searxng/searxng:latest" not in compose
    assert '"127.0.0.1:8081:8080"' in compose
    assert "192.168.1." not in compose
    assert "/mnt/ai-rig-shared" not in compose


def test_control_has_one_bounded_sigterm_and_no_force_kill():
    control = (SEARX / "searxng_control.sh").read_text()
    assert control.count("docker kill --signal TERM") == 1
    assert "container survived SIGTERM" in control
    for forbidden in ("SIGKILL", "kill -9", "docker kill --signal KILL"):
        assert forbidden not in control


def test_service_and_installer_keep_install_and_activation_separate():
    unit = (SEARX / "ai-rig-searxng.service").read_text()
    installer = (SEARX / "install_searxng_service.sh").read_text()
    assert "SendSIGKILL=no" in unit
    assert "/mnt/ai-rig-shared" not in unit
    assert "service_mutation=false" in installer
    assert "--install|--activate|--check" in installer
    assert "LEGACY_SETTINGS=/mnt/ai-rig-shared/searxng/config/settings.yml" in installer
    assert "cat \"$LEGACY_SETTINGS\"" not in installer

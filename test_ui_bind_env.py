from __future__ import annotations

import pytest

from devin.ui.runtime_bind import resolve_ui_bind


def test_environment_can_move_rig_backend_to_internal_port() -> None:
    assert resolve_ui_bind(
        {"host": "0.0.0.0", "port": 5000},
        {"DEVIN_UI_HOST": "127.0.0.1", "DEVIN_UI_PORT": "5001"},
    ) == ("127.0.0.1", 5001)


def test_settings_remain_the_default_when_no_override_is_present() -> None:
    assert resolve_ui_bind({"host": "0.0.0.0", "port": 7000}, {}) == ("0.0.0.0", 7000)
    assert resolve_ui_bind({}, {}) == ("127.0.0.1", 5000)


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"DEVIN_UI_HOST": "devin.example"}, "unsupported DEVIN UI host"),
        ({"DEVIN_UI_PORT": "invalid"}, "must be an integer"),
        ({"DEVIN_UI_PORT": "80"}, "between 1024 and 65535"),
    ],
)
def test_invalid_runtime_bind_fails_closed(environment: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_ui_bind({}, environment)

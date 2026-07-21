from __future__ import annotations

import json
from datetime import datetime

from devin.core.run_events import RunEventStore
from devin.core.time_service import (
    monotonic_elapsed_ms,
    resolve_display_timezone_name,
    timestamp_bundle,
)


def test_europe_rome_applies_summer_and_winter_dst():
    summer = timestamp_bundle(
        "2026-07-21T17:22:26Z",
        display_timezone="Europe/Rome",
    )
    winter = timestamp_bundle(
        "2026-01-21T17:22:26Z",
        display_timezone="Europe/Rome",
    )

    assert summer["timestamp_utc"] == "2026-07-21T17:22:26+00:00"
    assert summer["timestamp_local"] == "2026-07-21T19:22:26+02:00"
    assert winter["timestamp_utc"] == "2026-01-21T17:22:26+00:00"
    assert winter["timestamp_local"] == "2026-01-21T18:22:26+01:00"


def test_naive_timestamp_is_not_guessed_without_explicit_policy():
    result = timestamp_bundle(
        "2026-07-21T17:22:26",
        display_timezone="Europe/Rome",
    )

    assert result["timezone_status"] == "unknown_naive"
    assert result["timestamp_utc"] is None
    assert result["timestamp_local"] is None
    assert result["source_timestamp"] == "2026-07-21T17:22:26"


def test_naive_timestamp_can_be_migrated_only_with_explicit_assumption():
    result = timestamp_bundle(
        "2026-01-21T18:22:26",
        display_timezone="Europe/Rome",
        assume_timezone="Europe/Rome",
    )

    assert result["timezone_status"] == "assumed:Europe/Rome"
    assert result["timestamp_utc"] == "2026-01-21T17:22:26+00:00"
    assert result["timestamp_local"] == "2026-01-21T18:22:26+01:00"


def test_timezone_resolution_precedence_env_then_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"time": {"display_timezone": "Europe/Rome"}}),
        encoding="utf-8",
    )

    assert resolve_display_timezone_name(
        settings_path=settings,
        environ={},
    ) == "Europe/Rome"
    assert resolve_display_timezone_name(
        settings_path=settings,
        environ={"DEVIN_DISPLAY_TIMEZONE": "UTC"},
    ) == "UTC"


def test_monotonic_duration_is_deterministic_and_non_negative():
    assert monotonic_elapsed_ms(1_000_000_000, end_ns=1_123_999_999) == 123
    assert monotonic_elapsed_ms(2_000_000_000, end_ns=1_000_000_000) == 0


def test_run_events_keep_legacy_utc_and_add_local_metadata(tmp_path):
    store = RunEventStore(tmp_path, display_timezone="Europe/Rome")
    record = store.append("run-1", "run_started", message="started")

    assert record["ts"] == record["timestamp_utc"]
    assert record["display_timezone"] == "Europe/Rome"
    assert record["timezone_status"] == "aware"

    utc_dt = datetime.fromisoformat(record["timestamp_utc"])
    local_dt = datetime.fromisoformat(record["timestamp_local"])
    assert utc_dt.utcoffset().total_seconds() == 0
    assert utc_dt.timestamp() == local_dt.timestamp()

    persisted = store.list("run-1")
    assert persisted == [record]

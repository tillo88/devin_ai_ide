"""time_service must not crash when the IANA tz database is unavailable
(Windows without the 'tzdata' package, or a PyInstaller runtime). This was the
root cause of 'Failed to fetch' on scaffold: run_events -> timestamp_bundle ->
ZoneInfo(...) raised ZoneInfoNotFoundError and 500'd the request. 2026-07-22.
"""
import builtins

import devin.core.time_service as ts


def test_timestamp_bundle_falls_back_when_zoneinfo_fails(monkeypatch):
    def _raise(_name):
        raise ts.ZoneInfoNotFoundError("no tzdata")

    # Simulate a system with no IANA tz data: every ZoneInfo() raises exactly
    # what Windows-without-tzdata raises.
    monkeypatch.setattr(ts, "ZoneInfo", _raise)

    bundle = ts.timestamp_bundle(display_timezone="Europe/Rome")
    # No exception, and it degrades to UTC instead of crashing.
    assert bundle["timestamp_utc"] is not None
    assert bundle["timestamp_local"] is not None
    assert bundle["display_timezone"] == "UTC"


def test_timestamp_bundle_normal_when_tzdata_present():
    bundle = ts.timestamp_bundle(display_timezone="Europe/Rome")
    assert bundle["timestamp_utc"] and bundle["timestamp_local"]
    assert bundle["display_timezone"] == "Europe/Rome"

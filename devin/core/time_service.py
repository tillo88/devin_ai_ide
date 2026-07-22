from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
DEFAULT_DISPLAY_TIMEZONE = "Europe/Rome"
DISPLAY_TIMEZONE_ENV = "DEVIN_DISPLAY_TIMEZONE"


def default_settings_path() -> Path:
    """Return the repository/bundle settings path used by DEVIN."""
    return Path(__file__).resolve().parents[2] / "config" / "settings.json"


def _is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    return True


def resolve_display_timezone_name(
    *,
    settings_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve the display timezone without changing UTC persistence.

    Precedence is environment, settings.json, then Europe/Rome. Invalid values
    fail closed to the safe default; if IANA timezone data is unavailable, UTC
    remains usable rather than crashing the backend.
    """
    env = os.environ if environ is None else environ
    configured = str(env.get(DISPLAY_TIMEZONE_ENV, "") or "").strip()

    if not configured:
        path = Path(settings_path) if settings_path is not None else default_settings_path()
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            configured = str((payload.get("time") or {}).get("display_timezone") or "").strip()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            configured = ""

    for candidate in (configured, DEFAULT_DISPLAY_TIMEZONE, "UTC"):
        if candidate and _is_valid_timezone(candidate):
            return candidate
    return "UTC"


def parse_timestamp(value: datetime | str) -> datetime:
    """Parse an ISO-8601 timestamp while preserving naive/aware status."""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is empty")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text)


def timestamp_bundle(
    value: datetime | str | None = None,
    *,
    display_timezone: str | None = None,
    assume_timezone: str | None = None,
) -> dict[str, Any]:
    """Return canonical UTC plus a local display timestamp.

    Naive historical timestamps are never silently guessed. They are marked as
    ``unknown_naive`` unless the caller supplies ``assume_timezone`` explicitly.
    """
    dt = datetime.now(UTC) if value is None else parse_timestamp(value)
    status = "aware"

    if dt.tzinfo is None or dt.utcoffset() is None:
        if not assume_timezone:
            return {
                "timestamp_utc": None,
                "timestamp_local": None,
                "display_timezone": display_timezone or resolve_display_timezone_name(),
                "timezone_status": "unknown_naive",
                "source_timestamp": dt.isoformat(),
            }
        if not _is_valid_timezone(assume_timezone):
            raise ValueError(f"invalid assume_timezone: {assume_timezone!r}")
        dt = dt.replace(tzinfo=ZoneInfo(assume_timezone))
        status = f"assumed:{assume_timezone}"

    zone_name = display_timezone or resolve_display_timezone_name()
    if not _is_valid_timezone(zone_name):
        zone_name = resolve_display_timezone_name(environ={})

    utc_dt = dt.astimezone(UTC)
    local_dt = utc_dt.astimezone(ZoneInfo(zone_name))
    return {
        "timestamp_utc": utc_dt.isoformat(),
        "timestamp_local": local_dt.isoformat(),
        "display_timezone": zone_name,
        "timezone_status": status,
    }


def monotonic_start_ns() -> int:
    """Start a duration measurement immune to wall-clock/NTP/DST changes."""
    return time.monotonic_ns()


def monotonic_elapsed_ms(start_ns: int, *, end_ns: int | None = None) -> int:
    """Return a non-negative monotonic duration in whole milliseconds."""
    finish = time.monotonic_ns() if end_ns is None else int(end_ns)
    return max(0, (finish - int(start_ns)) // 1_000_000)

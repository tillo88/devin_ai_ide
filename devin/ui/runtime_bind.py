"""Validated runtime bind overrides for the DEVIN HTTP backend."""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


ALLOWED_HOSTS = frozenset({"127.0.0.1", "0.0.0.0", "::1"})


def resolve_ui_bind(
    ui_config: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> tuple[str, int]:
    """Resolve a validated host/port, with environment taking precedence."""
    env = os.environ if environ is None else environ
    host = str(env.get("DEVIN_UI_HOST") or ui_config.get("host") or "127.0.0.1").strip()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"unsupported DEVIN UI host: {host}")

    raw_port = env.get("DEVIN_UI_PORT") or ui_config.get("port") or 5000
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("DEVIN UI port must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("DEVIN UI port must be between 1024 and 65535")
    return host, port

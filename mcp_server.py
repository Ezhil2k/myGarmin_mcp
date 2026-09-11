#!/usr/bin/env python3
"""Read-only MCP server for Garmin Connect."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from garminconnect import Garmin
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


def _tokenstore() -> str:
    return str(Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser())


_allowed_hosts = [
    value.strip()
    for value in os.getenv(
        "MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*"
    ).split(",")
    if value.strip()
]

server = FastMCP(
    "Garmin Connect",
    instructions=(
        "Read-only Garmin Connect data. Dates use YYYY-MM-DD. "
        "Do not expose credentials or token contents."
    ),
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
    transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts),
)

_client: Garmin | None = None
_client_lock = Lock()


def _api() -> Garmin:
    """Return a lazily authenticated Garmin client."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        email = os.getenv("GARMIN_EMAIL")
        password = os.getenv("GARMIN_PASSWORD")
        client = Garmin(email=email, password=password) if email and password else Garmin()
        client.login(_tokenstore())
        _client = client
        return client


def _json(value: Any) -> str:
    """Serialize Garmin responses as readable MCP text."""
    return json.dumps(value, indent=2, default=str)


@server.tool()
def get_user_profile() -> str:
    """Get the authenticated Garmin user profile."""
    return _json(_api().get_user_profile())


@server.tool()
def get_daily_summary(cdate: str) -> str:
    """Get daily summary metrics for a date in YYYY-MM-DD format."""
    return _json(_api().get_user_summary(cdate))


@server.tool()
def get_health_snapshot(cdate: str) -> str:
    """Get heart rate, sleep, steps, stress, and training readiness for a date."""
    api = _api()
    return _json({
        "date": cdate,
        "heart_rates": api.get_heart_rates(cdate),
        "sleep": api.get_sleep_data(cdate),
        "steps": api.get_steps_data(cdate),
        "stress": api.get_all_day_stress(cdate),
        "training_readiness": api.get_training_readiness(cdate),
    })


@server.tool()
def get_activities(start: int = 0, limit: int = 10) -> str:
    """Get recent activities. Start is a zero-based offset; limit is 1-50."""
    if start < 0:
        raise ValueError("start must be non-negative")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return _json(_api().get_activities(start, limit))


@server.tool()
def get_activities_by_date(start_date: str, end_date: str) -> str:
    """Get activities between two YYYY-MM-DD dates, inclusive."""
    return _json(_api().get_activities_by_date(start_date, end_date))


@server.tool()
def get_last_activity() -> str:
    """Get the most recent activity, if one exists."""
    return _json(_api().get_last_activity())


@server.tool()
def get_devices() -> str:
    """List Garmin devices associated with the authenticated account."""
    return _json(_api().get_devices())


@server.tool()
def get_body_battery(days: int = 7) -> str:
    """Get body-battery data for the last 1-31 days, ending today."""
    if not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")
    end = date.today()
    start = end - timedelta(days=days - 1)
    return _json(_api().get_body_battery(start.isoformat(), end.isoformat()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default=os.getenv("MCP_TRANSPORT", "stdio"),
    )
    args = parser.parse_args()
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()

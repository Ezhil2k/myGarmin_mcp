#!/usr/bin/env python3
"""Read-only probe: log in and dump a sample of Garmin Connect data.

Usage:
    GARMIN_EMAIL=you@example.com GARMIN_PASSWORD='...' .venv/bin/python probe.py

Tokens are cached in ~/.garminconnect so later runs skip the password.
Full JSON is written to your_data/probe_dump.json (gitignored).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

TOKENSTORE = str(Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser())
OUT_DIR = Path("your_data")
OUT_FILE = OUT_DIR / "probe_dump.json"


def summarize(value, max_list: int = 3):
    """Return a compact view of nested API payloads."""
    if isinstance(value, dict):
        return {k: summarize(v, max_list) for k, v in value.items()}
    if isinstance(value, list):
        preview = [summarize(v, max_list) for v in value[:max_list]]
        if len(value) > max_list:
            preview.append(f"... {len(value) - max_list} more items (total {len(value)})")
        return preview
    if isinstance(value, str) and len(value) > 180:
        return value[:177] + "..."
    return value


def call(label: str, fn, *args, **kwargs):
    print(f"\n=== {label} ===")
    try:
        data = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - probe should keep going
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(summarize(data), indent=2, default=str)[:4000])
    if len(json.dumps(data, default=str)) > 4000:
        print("  ... (truncated in console; full payload in probe_dump.json)")
    return data


def login() -> Garmin:
    try:
        garmin = Garmin()
        garmin.login(TOKENSTORE)
        print(f"Logged in with saved tokens from {TOKENSTORE}")
        return garmin
    except Exception:
        print("No valid saved tokens — logging in with credentials.")

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: ")
    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: (os.getenv("GARMIN_MFA") or input("MFA code: ")).strip(),
    )
    garmin.login(TOKENSTORE)
    print(f"Login successful. Tokens saved to {TOKENSTORE}")
    return garmin


def main() -> int:
    try:
        api = login()
    except GarminConnectAuthenticationError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    dump: dict = {
        "today": today,
        "full_name": api.get_full_name(),
        "unit_system": api.get_unit_system(),
    }
    print(f"User: {dump['full_name']}  units={dump['unit_system']}  date={today}")

    dump["user_profile"] = call("user profile", api.get_user_profile)
    dump["user_summary"] = call("today summary", api.get_user_summary, today)
    dump["heart_rates"] = call("heart rates", api.get_heart_rates, today)
    dump["sleep"] = call("sleep", api.get_sleep_data, today)
    dump["steps"] = call("steps chart", api.get_steps_data, today)
    dump["stress"] = call("all-day stress", api.get_all_day_stress, today)
    dump["body_battery"] = call("body battery (7d)", api.get_body_battery, week_ago, today)
    dump["hrv"] = call("HRV", api.get_hrv_data, today)
    dump["training_readiness"] = call("training readiness", api.get_training_readiness, today)
    dump["devices"] = call("devices", api.get_devices)
    dump["last_activity"] = call("last activity", api.get_last_activity)
    dump["recent_activities"] = call(
        "activities (last 5)", api.get_activities, 0, 5
    )
    dump["activities_by_date"] = call(
        "activities last 7 days", api.get_activities_by_date, week_ago, today
    )

    OUT_DIR.mkdir(mode=0o700, exist_ok=True)
    OUT_FILE.write_text(json.dumps(dump, indent=2, default=str))
    OUT_FILE.chmod(0o600)
    print(f"\nFull dump written to {OUT_FILE.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

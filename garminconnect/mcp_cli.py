#!/usr/bin/env python3
"""Convenience CLI for running the Garmin MCP server and OpenAI tunnel."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROFILE = "garmin-local"
TUNNEL_CLIENT = Path.home() / ".local" / "bin" / "tunnel-client"
PROFILE_PATH = Path.home() / ".config" / "tunnel-client" / f"{PROFILE}.yaml"


def load_env(root: Path) -> None:
    """Load simple KEY=VALUE entries from the project .env file."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value and value[0:1] in {"'", '"'} and value[-1:] == value[0]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def client_path() -> str:
    """Return the tunnel-client executable path."""
    if TUNNEL_CLIENT.exists():
        return str(TUNNEL_CLIENT)
    return "tunnel-client"


def run_command(args: list[str]) -> int:
    try:
        return subprocess.run(args, check=False).returncode
    except FileNotFoundError:
        print(f"Command not found: {args[0]}", file=sys.stderr)
        return 127


def require_env(*names: str) -> None:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing environment variable(s): {joined}. Fill in .env.")


def setup_profile() -> int:
    require_env("CONTROL_PLANE_API_KEY", "CONTROL_PLANE_TUNNEL_ID")
    return run_command(
        [
            client_path(),
            "init",
            "--sample",
            "sample_mcp_remote_no_auth",
            "--profile",
            PROFILE,
            "--tunnel-id",
            os.environ["CONTROL_PLANE_TUNNEL_ID"],
            "--mcp-server-url",
            f"http://{os.getenv('MCP_HOST', '127.0.0.1')}:{os.getenv('MCP_PORT', '8000')}{os.getenv('MCP_PATH', '/mcp')}",
        ]
    )


def check_url(path: str) -> bool:
    try:
        with urllib.request.urlopen(path, timeout=3) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError):
        return False


def status() -> int:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    health_port = int(os.getenv("TUNNEL_HEALTH_PORT", "8080"))
    mcp_up = False
    with socket.socket() as sock:
        sock.settimeout(1)
        try:
            sock.connect((host, port))
            mcp_up = True
        except OSError:
            pass
    tunnel_up = check_url(f"http://127.0.0.1:{health_port}/readyz")
    print(f"MCP server: {'ready' if mcp_up else 'stopped'} ({host}:{port})")
    print(f"Secure tunnel: {'ready' if tunnel_up else 'stopped'} (127.0.0.1:{health_port})")
    return 0 if mcp_up and tunnel_up else 1


def run_services(root: Path) -> int:
    require_env("CONTROL_PLANE_API_KEY", "CONTROL_PLANE_TUNNEL_ID")
    if not PROFILE_PATH.exists():
        print("Tunnel profile is missing; initializing it...")
        if setup_profile() != 0:
            return 1
    python = sys.executable
    server = subprocess.Popen(
        [python, str(root / "mcp_server.py"), "--transport", "streamable-http"]
    )
    tunnel = subprocess.Popen([client_path(), "run", "--profile", PROFILE])
    children = (server, tunnel)

    def shutdown(_signum: int, _frame: object) -> None:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print("Garmin MCP is running. Press Ctrl+C to stop both services.")
    while True:
        if server.poll() is not None or tunnel.poll() is not None:
            shutdown(signal.SIGTERM, None)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    load_env(root)
    parser = argparse.ArgumentParser(prog="garmin-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="run the MCP server and secure tunnel")
    subparsers.add_parser("status", help="show local service health")
    subparsers.add_parser("test", help="test both local health endpoints")
    subparsers.add_parser("doctor", help="run tunnel-client diagnostics")
    subparsers.add_parser("setup", help="create or refresh the local tunnel profile")
    subparsers.add_parser("dashboard", help="open the fixed-screen service dashboard")
    args = parser.parse_args()
    if args.command == "dashboard":
        from .mcp_tui import dashboard_main

        return dashboard_main(root)
    if args.command == "run":
        return run_services(root)
    if args.command in {"status", "test"}:
        return status()
    if args.command == "doctor":
        require_env("CONTROL_PLANE_API_KEY")
        return run_command([client_path(), "doctor", "--profile", PROFILE, "--explain"])
    return setup_profile()


if __name__ == "__main__":
    raise SystemExit(main())

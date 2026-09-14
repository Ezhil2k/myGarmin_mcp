"""Fixed-screen terminal dashboard for the Garmin MCP services."""

from __future__ import annotations

import curses
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .mcp_cli import PROFILE, PROFILE_PATH, client_path, load_env, require_env, setup_profile


STATE_DIR = Path.home() / ".local" / "state" / "garmin-mcp"


def _pid_path(name: str) -> Path:
    return STATE_DIR / f"{name}.pid"


def _pid(name: str) -> int | None:
    try:
        value = int(_pid_path(name).read_text(encoding="utf-8").strip())
        os.kill(value, 0)
        return value
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def _running(name: str) -> bool:
    return _pid(name) is not None


def _start(name: str, command: list[str]) -> None:
    if _running(name):
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_file = (STATE_DIR / f"{name}.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _pid_path(name).write_text(str(process.pid), encoding="utf-8")


def _stop(name: str) -> None:
    pid = _pid(name)
    if pid is None:
        _pid_path(name).unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _pid_path(name).unlink(missing_ok=True)


def start_services(root: Path) -> str:
    require_env("CONTROL_PLANE_API_KEY", "CONTROL_PLANE_TUNNEL_ID")
    if not PROFILE_PATH.exists():
        if setup_profile() != 0:
            return "Tunnel profile setup failed."
    _start(
        "mcp",
        [os.sys.executable, str(root / "mcp_server.py"), "--transport", "streamable-http"],
    )
    _start("tunnel", [client_path(), "run", "--profile", PROFILE])
    return "Services started in the background."


def stop_services() -> str:
    _stop("tunnel")
    _stop("mcp")
    return "Services stopped."


def _health(port: int, path: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError):
        return False


def service_status() -> tuple[bool, bool, bool, bool]:
    mcp_process = _running("mcp")
    tunnel_process = _running("tunnel")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    mcp_port = False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
            mcp_port = True
        except OSError:
            pass
    tunnel_ready = _health(int(os.getenv("TUNNEL_HEALTH_PORT", "8080")), "/readyz")
    return mcp_process, mcp_port, tunnel_process, tunnel_ready


def _tail(name: str, lines: int = 8) -> list[str]:
    try:
        content = (STATE_DIR / f"{name}.log").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return content.splitlines()[-lines:]


def _tunnel_connected() -> bool:
    recent = "\n".join(_tail("tunnel", 40)).lower()
    return any(
        marker in recent
        for marker in ("tunnel-client started", "forwarded command to mcp", "poll")
    )


def dashboard_main(root: Path) -> int:
    load_env(root)
    message = "Starting services..."
    try:
        message = start_services(root)
    except SystemExit as exc:
        message = str(exc)

    def draw(screen: curses.window) -> None:
        nonlocal message
        curses.curs_set(1)
        screen.timeout(80)
        screen.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
        command_buffer = ""
        show_log_lines = 100
        while True:
            screen.erase()
            mcp_proc, mcp_port, tunnel_proc, tunnel_ready = service_status()
            title = [
                "  ____    _    ____  __  __ ___ _   _    __  __  ____ ____ ",
                " / ___|  / \\  |  _ \\|  \\/  |_ _| \\ | |  |  \\/  |/ ___|  _ \\ ",
                "| |  _  / _ \\ | |_) | |\\/| || ||  \\| |  | |\\/| | |   | |_) |",
                "| |_| |/ ___ \\|  _ <| |  | || || |\\  |  | |  | | |___|  __/ ",
                " \\____/_/   \\_\\_| \\_\\_|  |_|___|_| \\_|  |_|  |_|\\____|_|    ",
            ]
            for index, line in enumerate(title):
                screen.addnstr(index, 2, line, max(1, curses.COLS - 3), curses.color_pair(3) | curses.A_BOLD)
            mcp_color = curses.color_pair(1) if mcp_port else curses.color_pair(2)
            tunnel_color = curses.color_pair(1) if tunnel_ready or (tunnel_proc and _tunnel_connected()) else curses.color_pair(2)
            screen.addstr(7, 2, "MCP server     ", curses.A_BOLD)
            screen.addstr(f"{'RUNNING' if mcp_proc else 'STOPPED':8}  ")
            screen.addstr("●", mcp_color)
            screen.addstr(8, 2, "Secure tunnel  ", curses.A_BOLD)
            screen.addstr(f"{'RUNNING' if tunnel_proc else 'STOPPED':8}  ")
            screen.addstr("●", tunnel_color)
            row = 10
            screen.addnstr(row, 2, "┌─ Recent tunnel log ", max(1, curses.COLS - 4), curses.color_pair(3) | curses.A_BOLD)
            screen.addnstr(row, 22, "─" * max(1, curses.COLS - 24) + "┐", max(1, curses.COLS - 23), curses.color_pair(3) | curses.A_BOLD)
            available_rows = max(1, curses.LINES - row - 6)
            for line in _tail("tunnel", min(show_log_lines, available_rows)):
                row += 1
                screen.addnstr(row, 2, "│ ", curses.color_pair(3))
                screen.addnstr(row, 4, line, max(1, curses.COLS - 7), curses.A_DIM)
            screen.addnstr(row + 1, 2, "└" + "─" * max(1, curses.COLS - 5) + "┘", max(1, curses.COLS - 3), curses.color_pair(3) | curses.A_BOLD)
            prompt_row = max(row + 2, curses.LINES - 3)
            screen.addnstr(prompt_row - 2, 2, "Commands:  [r] start   [x] stop   [s] status   [l] logs   [q] quit", max(1, curses.COLS - 4), curses.color_pair(3) | curses.A_BOLD)
            screen.addnstr(prompt_row - 1, 2, f"Response:  {message}", max(1, curses.COLS - 4), curses.color_pair(4))
            screen.addstr(prompt_row, 2, "Command: ", curses.A_BOLD)
            screen.addnstr(prompt_row, 11, command_buffer, max(1, curses.COLS - 12))
            screen.refresh()
            key = screen.getch()
            if key in (10, 13):
                command = command_buffer.strip().lower()
                command_buffer = ""
                if command in {"q", "quit", "exit"}:
                    return
                if command in {"r", "run", "start"}:
                    try:
                        message = start_services(root)
                    except SystemExit as exc:
                        message = str(exc)
                elif command in {"x", "stop"}:
                    message = stop_services()
                elif command in {"s", "status"}:
                    message = "Status refreshed."
                elif command in {"l", "logs"}:
                    show_log_lines = 8 if show_log_lines > 8 else 100
                    message = f"Showing {show_log_lines} recent tunnel log lines."
                elif command:
                    message = "Unknown command. Use: r, x, s, l, q."
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                command_buffer = command_buffer[:-1]
            elif 32 <= key <= 126:
                command_buffer += chr(key)

    curses.wrapper(draw)
    return 0

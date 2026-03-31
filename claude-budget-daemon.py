"""Budget daemon for Claude and Codex usage files.

Setup: pip install pexpect pyte
Usage: --once (single run), --debug (verbose), --ensure-running (idempotent start)
Install daemon: launchctl load ~/Library/LaunchAgents/com.phenly.budget-daemon.plist
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from shutil import which
from pathlib import Path
from typing import Any

import pexpect
import pyte


POLL_SECONDS = 300

HOME = Path.home()
BUDGET_DIR = HOME / ".claude" / "budget"
PID_FILE = HOME / ".claude" / "budget-daemon.pid"
CLAUDE_MD_PATH = BUDGET_DIR / "claude-budget.md"
CLAUDE_JSON_PATH = BUDGET_DIR / "claude-budget.json"
CODEX_MD_PATH = BUDGET_DIR / "codex-budget.md"
CODEX_JSON_PATH = BUDGET_DIR / "codex-budget.json"
CLI_TIMEOUT_SECONDS = 30
CLI_FALLBACK_PATHS = {
    "claude": ["/usr/local/bin/claude"],
    "codex": ["/opt/homebrew/bin/codex"],
}
DEFAULT_PATH_SEGMENTS = [
    str(HOME / ".local" / "bin"),
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
    "/bin",
]

STOP_REQUESTED = False


@dataclass
class ScrapeHealth:
    status: str
    errors: list[str]
    last_clean_scrape: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errors": self.errors,
            "last_clean_scrape": self.last_clean_scrape,
        }


class PersistentCLISession:
    def __init__(self, command: str):
        self.command = command
        self.child: Any | None = None
        self.screen: Any | None = None
        self.byte_stream: Any | None = None

    def start(self) -> None:
        self.close()
        child, screen, byte_stream = _spawn_cli(self.command)
        self.child = child
        self.screen = screen
        self.byte_stream = byte_stream

        if self.command == "claude":
            if not _wait_for_screen_text(child, screen, byte_stream, "Claude Code v", timeout=10.0):
                raise RuntimeError("claude startup banner not detected")
            if not _wait_for_screen_text(child, screen, byte_stream, "❯", timeout=10.0):
                raise RuntimeError("claude prompt not detected")
            return

        if self.command == "codex":
            if _wait_for_screen_text(child, screen, byte_stream, "5h limit", timeout=10.0):
                return
            if not _wait_for_screen_text(child, screen, byte_stream, "›", timeout=10.0):
                raise RuntimeError("codex prompt not detected")
            return

        raise RuntimeError(f"unsupported persistent CLI command: {self.command}")

    def is_alive(self) -> bool:
        return bool(self.child and self.child.isalive())

    def ensure_alive(self) -> None:
        if not self.is_alive():
            self.start()

    def close(self) -> None:
        if self.child is not None and self.child.isalive():
            self.child.close(force=True)
        self.child = None
        self.screen = None
        self.byte_stream = None


_claude_session = PersistentCLISession("claude")
_codex_session = PersistentCLISession("codex")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def ensure_dirs() -> None:
    BUDGET_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_existing_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def remove_pid_file_if_owned() -> None:
    existing_pid = read_existing_pid()
    if existing_pid == os.getpid() and PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)


def write_pid_file() -> None:
    existing_pid = read_existing_pid()
    if existing_pid is not None and existing_pid != os.getpid():
        if pid_is_alive(existing_pid):
            raise RuntimeError(f"daemon already running with pid {existing_pid}")
        PID_FILE.unlink(missing_ok=True)
    atomic_write(PID_FILE, f"{os.getpid()}\n")


def handle_signal(signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    log(f"received signal {signum}, shutting down")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def warning_block(errors: list[str]) -> str:
    details = "; ".join(errors) if errors else "unknown scrape issue"
    return f"⚠️ SCRAPE WARNING: using last known good values. Errors: {details}\n\n"


def build_scrape_health(errors: list[str], previous_json: dict[str, Any] | None, timestamp: str) -> ScrapeHealth:
    previous_clean = None
    if previous_json:
        previous_clean = previous_json.get("scrape_health", {}).get("last_clean_scrape")
    if errors:
        return ScrapeHealth(status="degraded", errors=errors, last_clean_scrape=previous_clean)
    return ScrapeHealth(status="ok", errors=[], last_clean_scrape=timestamp)


def _render_screen(screen: Any) -> str:
    lines = [line.rstrip() for line in screen.display]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _screen_contains(screen: Any, target_text: str) -> bool:
    return any(target_text in line for line in screen.display)


def _read_into_screen(child: Any, byte_stream: Any, timeout: float = 0.2) -> bool:
    try:
        data = child.read_nonblocking(size=10000, timeout=timeout)
        if data:
            byte_stream.feed(data)
            return True
    except pexpect.TIMEOUT:
        return False
    except pexpect.EOF:
        return False
    except OSError:
        return False
    return False


def _wait_for_screen_text(
    child: Any,
    screen: Any,
    byte_stream: Any,
    target_text: str,
    timeout: float,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _screen_contains(screen, target_text):
            return True
        _read_into_screen(child, byte_stream, timeout=0.2)
    return _screen_contains(screen, target_text)


def _wait_for_stable_screen(
    child: Any,
    byte_stream: Any,
    stable_for: float,
    timeout: float,
) -> None:
    deadline = time.time() + timeout
    last_activity = time.time()
    while time.time() < deadline:
        if _read_into_screen(child, byte_stream, timeout=0.2):
            last_activity = time.time()
            continue
        if time.time() - last_activity >= stable_for:
            return


def _spawn_cli(command: str) -> tuple[Any, Any, Any]:
    executable = which(command)
    if executable is None:
        for candidate in CLI_FALLBACK_PATHS.get(command, []):
            if Path(candidate).exists():
                executable = candidate
                break
    if executable is None:
        raise RuntimeError(f"{command} not found in PATH")

    screen = pyte.Screen(220, 50)
    byte_stream = pyte.ByteStream(screen)
    user_info = pwd.getpwuid(os.getuid())
    child_env = dict(os.environ)
    child_env.setdefault("HOME", str(HOME))
    child_env.setdefault("USER", user_info.pw_name)
    child_env.setdefault("LOGNAME", user_info.pw_name)
    child_env.setdefault("SHELL", user_info.pw_shell or "/bin/zsh")
    path_segments = [segment for segment in child_env.get("PATH", "").split(":") if segment]
    for segment in reversed(DEFAULT_PATH_SEGMENTS):
        if segment not in path_segments:
            path_segments.insert(0, segment)
    child_env["PATH"] = ":".join(path_segments)
    child = pexpect.spawn(
        executable,
        encoding=None,
        timeout=CLI_TIMEOUT_SECONDS,
        dimensions=(50, 220),
        env=child_env,
    )
    return child, screen, byte_stream


def initialize_sessions() -> None:
    _claude_session.ensure_alive()
    _codex_session.ensure_alive()


def parse_claude_output(raw_text: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []

    session_match = re.search(
        r"Current session.*?(\d+)%\s+used.*?Resets\s+([^\r\n]+)",
        raw_text,
        re.DOTALL,
    )
    weekly_match = re.search(
        r"Current week(?:\s+\(all models\))?.*?(\d+)%\s+used.*?Resets\s+([^\r\n]+)",
        raw_text,
        re.DOTALL,
    )

    session_used = int(session_match.group(1)) if session_match else None
    session_reset = normalize_space(session_match.group(2)) if session_match else None
    weekly_used = int(weekly_match.group(1)) if weekly_match else None
    weekly_reset = normalize_space(weekly_match.group(2)) if weekly_match else None

    if session_used is None:
        errors.append("claude.session.used_pct missing")
    if session_reset is None:
        errors.append("claude.session.resets_in missing")
    if weekly_used is None:
        errors.append("claude.weekly.used_pct missing")
    if weekly_reset is None:
        errors.append("claude.weekly.resets_in missing")

    data = {
        "session": {
            "used_pct": session_used,
            "remaining_pct": 100 - session_used if session_used is not None else None,
            "resets_in": session_reset,
        },
        "weekly": {
            "used_pct": weekly_used,
            "remaining_pct": 100 - weekly_used if weekly_used is not None else None,
            "resets_in": weekly_reset,
        },
    }
    return data, errors


def parse_codex_output(raw_text: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []

    five_hour_match = re.search(
        r"5h limit:\s*\[[^\]]*]\s*(\d+)%\s+left(?:\s*\(resets\s+([^)]+)\))?",
        raw_text,
    )
    weekly_match = re.search(
        r"Weekly limit:\s*\[[^\]]*]\s*(\d+)%\s+left(?:\s*\(resets\s+([^)]+)\))?",
        raw_text,
    )

    status_bar_match = None if five_hour_match else re.search(r"·\s*(\d+)%\s+left\s*·", raw_text)

    five_hour_remaining = int(five_hour_match.group(1)) if five_hour_match else None
    five_hour_reset = normalize_space(five_hour_match.group(2)) if five_hour_match and five_hour_match.group(2) else None
    weekly_remaining = int(weekly_match.group(1)) if weekly_match else None
    weekly_reset = normalize_space(weekly_match.group(2)) if weekly_match and weekly_match.group(2) else None

    if five_hour_remaining is None and status_bar_match:
        five_hour_remaining = int(status_bar_match.group(1))

    if five_hour_remaining is None:
        errors.append("codex.five_hour.remaining_pct missing")
    if five_hour_reset is None:
        errors.append("codex.five_hour.resets_in missing")
    if weekly_remaining is None:
        errors.append("codex.weekly.remaining_pct missing")
    if weekly_reset is None:
        errors.append("codex.weekly.resets_in missing")

    data = {
        "five_hour": {
            "remaining_pct": five_hour_remaining,
            "resets_in": five_hour_reset,
        },
        "weekly": {
            "remaining_pct": weekly_remaining,
            "resets_in": weekly_reset,
        },
    }
    return data, errors


def scrape_claude() -> tuple[dict[str, Any], list[str], str]:
    _claude_session.ensure_alive()
    child = _claude_session.child
    screen = _claude_session.screen
    byte_stream = _claude_session.byte_stream
    if child is None or screen is None or byte_stream is None:
        raise RuntimeError("claude session unavailable")

    child.send(b"/usage")
    _wait_for_screen_text(child, screen, byte_stream, "/usage", timeout=5.0)
    child.send(b"\r")
    _wait_for_screen_text(child, screen, byte_stream, "Current session", timeout=15.0)
    _wait_for_stable_screen(child, byte_stream, stable_for=0.75, timeout=3.0)
    raw_text = _render_screen(screen)
    child.send(b"\x1b")
    _wait_for_screen_text(child, screen, byte_stream, "❯", timeout=5.0)
    data, errors = parse_claude_output(raw_text)
    return data, errors, raw_text


def scrape_codex() -> tuple[dict[str, Any], list[str], str]:
    _codex_session.ensure_alive()
    child = _codex_session.child
    screen = _codex_session.screen
    byte_stream = _codex_session.byte_stream
    if child is None or screen is None or byte_stream is None:
        raise RuntimeError("codex session unavailable")

    _wait_for_stable_screen(child, byte_stream, stable_for=2.0, timeout=5.0)
    child.send(b"/status")
    _wait_for_screen_text(child, screen, byte_stream, "/status", timeout=5.0)
    child.send(b"\r")
    if not _wait_for_screen_text(child, screen, byte_stream, "5h limit", timeout=20.0):
        child.send(b"/status")
        _wait_for_screen_text(child, screen, byte_stream, "/status", timeout=5.0)
        child.send(b"\r")
        _wait_for_screen_text(child, screen, byte_stream, "5h limit", timeout=20.0)
    _wait_for_stable_screen(child, byte_stream, stable_for=0.75, timeout=3.0)
    raw_text = _render_screen(screen)
    child.send(b"\x1b")
    if not _wait_for_screen_text(child, screen, byte_stream, "›", timeout=5.0):
        child.send(b"\x03")
        _wait_for_screen_text(child, screen, byte_stream, "›", timeout=5.0)
    data, errors = parse_codex_output(raw_text)
    return data, errors, raw_text


def load_preserved_claude(previous_json: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "session": {
            "used_pct": previous_json.get("session", {}).get("used_pct") if previous_json else None,
            "remaining_pct": previous_json.get("session", {}).get("remaining_pct") if previous_json else None,
            "resets_in": previous_json.get("session", {}).get("resets_in") if previous_json else None,
        },
        "weekly": {
            "used_pct": previous_json.get("weekly", {}).get("used_pct") if previous_json else None,
            "remaining_pct": previous_json.get("weekly", {}).get("remaining_pct") if previous_json else None,
            "resets_in": previous_json.get("weekly", {}).get("resets_in") if previous_json else None,
        },
    }


def load_preserved_codex(previous_json: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "five_hour": {
            "remaining_pct": previous_json.get("five_hour", {}).get("remaining_pct") if previous_json else None,
            "resets_in": previous_json.get("five_hour", {}).get("resets_in") if previous_json else None,
        },
        "weekly": {
            "remaining_pct": previous_json.get("weekly", {}).get("remaining_pct") if previous_json else None,
            "resets_in": previous_json.get("weekly", {}).get("resets_in") if previous_json else None,
        },
    }


def fmt_or_unknown(value: Any, suffix: str = "") -> str:
    if value is None:
        return "unknown"
    return f"{value}{suffix}"


def render_claude_markdown(payload: dict[str, Any], health: ScrapeHealth, timestamp: str, stopped: bool = False) -> str:
    session = payload["session"]
    weekly = payload["weekly"]
    lines: list[str] = []
    if stopped:
        lines.append("daemon stopped")
        lines.append("")
    if health.status == "degraded":
        lines.append(warning_block(health.errors).rstrip())
        lines.append("")
    lines.extend(
        [
            "# Claude Budget",
            f"_Last updated: {timestamp} — next update in ~5 min_",
            "",
            "## Session",
            (
                f"{fmt_or_unknown(session['remaining_pct'], '%')} remaining"
                f" ({fmt_or_unknown(session['used_pct'], '%')} used) — resets in {session['resets_in'] or 'unknown'}"
            ),
            "",
            "## Weekly",
            (
                f"{fmt_or_unknown(weekly['remaining_pct'], '%')} remaining"
                f" ({fmt_or_unknown(weekly['used_pct'], '%')} used) — resets in {weekly['resets_in'] or 'unknown'}"
            ),
            "",
            "---",
            "_This file is managed by budget-daemon. Do not edit manually._",
        ]
    )
    return "\n".join(lines) + "\n"


def render_codex_markdown(payload: dict[str, Any], health: ScrapeHealth, timestamp: str, stopped: bool = False) -> str:
    five_hour = payload["five_hour"]
    weekly = payload["weekly"]

    lines: list[str] = []
    if stopped:
        lines.append("daemon stopped")
        lines.append("")
    if health.status == "degraded":
        lines.append(warning_block(health.errors).rstrip())
        lines.append("")
    lines.extend(
        [
            "# Codex Budget",
            f"_Last updated: {timestamp} — next update in ~5 min_",
            "",
            "## 5-Hour Limit",
            (
                f"{fmt_or_unknown(five_hour['remaining_pct'], '%')} remaining"
                f" — resets {five_hour['resets_in'] or 'unknown'}"
            ),
            "",
            "## Weekly Limit",
            (
                f"{fmt_or_unknown(weekly['remaining_pct'], '%')} remaining"
                f" — resets {weekly['resets_in'] or 'unknown'}"
            ),
            "",
            "---",
            "_This file is managed by budget-daemon. Do not edit manually._",
        ]
    )
    return "\n".join(lines) + "\n"


def build_claude_json(payload: dict[str, Any], health: ScrapeHealth, timestamp: str) -> dict[str, Any]:
    return {
        "last_updated": timestamp,
        "session": payload["session"],
        "weekly": payload["weekly"],
        "scrape_health": health.to_dict(),
    }


def build_codex_json(payload: dict[str, Any], health: ScrapeHealth, timestamp: str) -> dict[str, Any]:
    return {
        "last_updated": timestamp,
        "five_hour": payload["five_hour"],
        "weekly": payload["weekly"],
        "scrape_health": health.to_dict(),
    }


def process_claude_result(scraped: dict[str, Any], errors: list[str], timestamp: str) -> tuple[dict[str, Any], ScrapeHealth]:
    previous_json = read_json(CLAUDE_JSON_PATH)
    health = build_scrape_health(errors, previous_json, timestamp)
    if health.status == "degraded":
        payload = load_preserved_claude(previous_json)
        for section in ("session", "weekly"):
            for key, value in scraped.get(section, {}).items():
                if payload[section].get(key) is None and value is not None:
                    payload[section][key] = value
    else:
        payload = scraped
    return payload, health


def process_codex_result(scraped: dict[str, Any], errors: list[str], timestamp: str) -> tuple[dict[str, Any], ScrapeHealth]:
    previous_json = read_json(CODEX_JSON_PATH)
    health = build_scrape_health(errors, previous_json, timestamp)
    if health.status == "degraded":
        payload = load_preserved_codex(previous_json)
        for key in ("five_hour", "weekly"):
            for field, value in scraped.get(key, {}).items():
                if payload[key].get(field) is None and value is not None:
                    payload[key][field] = value
    else:
        payload = scraped
    return payload, health


def write_outputs(
    claude_payload: dict[str, Any],
    claude_health: ScrapeHealth,
    codex_payload: dict[str, Any],
    codex_health: ScrapeHealth,
    timestamp: str,
) -> None:
    atomic_write(CLAUDE_MD_PATH, render_claude_markdown(claude_payload, claude_health, timestamp))
    write_json(CLAUDE_JSON_PATH, build_claude_json(claude_payload, claude_health, timestamp))
    atomic_write(CODEX_MD_PATH, render_codex_markdown(codex_payload, codex_health, timestamp))
    write_json(CODEX_JSON_PATH, build_codex_json(codex_payload, codex_health, timestamp))


def write_stopped_notices() -> None:
    timestamp = now_iso()
    previous_claude = read_json(CLAUDE_JSON_PATH)
    previous_codex = read_json(CODEX_JSON_PATH)
    claude_payload = load_preserved_claude(previous_claude)
    codex_payload = load_preserved_codex(previous_codex)
    claude_health = build_scrape_health(["daemon stopped"], previous_claude, timestamp)
    codex_health = build_scrape_health(["daemon stopped"], previous_codex, timestamp)
    atomic_write(CLAUDE_MD_PATH, render_claude_markdown(claude_payload, claude_health, timestamp, stopped=True))
    atomic_write(CODEX_MD_PATH, render_codex_markdown(codex_payload, codex_health, timestamp, stopped=True))


def scrape_all(debug: bool = False) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
    try:
        claude_data, claude_errors, claude_raw = scrape_claude()
    except Exception as exc:
        claude_data = {
            "session": {"used_pct": None, "remaining_pct": None, "resets_in": None},
            "weekly": {"used_pct": None, "remaining_pct": None, "resets_in": None},
        }
        claude_errors = [f"claude scrape failed: {exc}"]
        claude_raw = str(exc)

    try:
        codex_data, codex_errors, codex_raw = scrape_codex()
    except Exception as exc:
        codex_data = {
            "five_hour": {"remaining_pct": None, "resets_in": None},
            "weekly": {"remaining_pct": None, "resets_in": None},
        }
        codex_errors = [f"codex scrape failed: {exc}"]
        codex_raw = str(exc)
    if debug:
        print(
            json.dumps(
                {
                    "claude_raw": claude_raw,
                    "codex_raw": codex_raw,
                    "claude_parsed": claude_data,
                    "codex_parsed": codex_data,
                },
                indent=2,
                ensure_ascii=False,
            ),
            flush=True,
        )
    return claude_data, codex_data, claude_errors, codex_errors


def run_once(debug: bool = False, manage_sessions: bool = True) -> int:
    ensure_dirs()
    try:
        if manage_sessions:
            initialize_sessions()

        timestamp = now_iso()
        claude_scraped, codex_scraped, claude_errors, codex_errors = scrape_all(debug=debug)

        claude_payload, claude_health = process_claude_result(claude_scraped, claude_errors, timestamp)
        codex_payload, codex_health = process_codex_result(codex_scraped, codex_errors, timestamp)
        write_outputs(claude_payload, claude_health, codex_payload, codex_health, timestamp)

        log(
            "cycle complete: "
            f"claude={claude_health.status} codex={codex_health.status}"
        )
        if claude_health.errors:
            log(f"claude scrape issues: {', '.join(claude_health.errors)}")
        if codex_health.errors:
            log(f"codex scrape issues: {', '.join(codex_health.errors)}")
        return 0 if not claude_health.errors and not codex_health.errors else 1
    finally:
        if manage_sessions:
            _claude_session.close()
            _codex_session.close()


def ensure_running() -> int:
    ensure_dirs()
    existing_pid = read_existing_pid()
    if existing_pid is not None and pid_is_alive(existing_pid):
        log(f"daemon already alive with pid {existing_pid}")
        return 0
    if existing_pid is not None and not pid_is_alive(existing_pid):
        PID_FILE.unlink(missing_ok=True)

    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log(f"started daemon pid {process.pid}")
    return 0


def poll_loop(debug: bool = False) -> int:
    write_pid_file()
    log("daemon started")
    try:
        initialize_sessions()
        while not STOP_REQUESTED:
            run_once(debug=debug, manage_sessions=False)
            slept = 0
            while slept < POLL_SECONDS and not STOP_REQUESTED:
                time.sleep(1)
                slept += 1
    finally:
        write_stopped_notices()
        _claude_session.close()
        _codex_session.close()
        remove_pid_file_if_owned()
        log("daemon stopped")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll Claude and Codex CLI budget usage into local markdown and JSON files.")
    parser.add_argument("--once", action="store_true", help="run a single scrape cycle and exit")
    parser.add_argument("--debug", action="store_true", help="print raw scrape output")
    parser.add_argument("--ensure-running", action="store_true", help="start the daemon if it is not already running")
    return parser.parse_args()


def main() -> int:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    args = parse_args()

    if args.ensure_running:
        return ensure_running()
    if args.once:
        return run_once(debug=args.debug)
    return poll_loop(debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())

"""Budget daemon for Claude and Codex usage files.

Setup: pip install playwright && playwright install chromium
Usage: --auth (first-time setup), --once (single run), --debug (verbose), --ensure-running (idempotent start)
Install daemon: launchctl load ~/Library/LaunchAgents/com.phenly.budget-daemon.plist
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


CLAUDE_URL = "https://claude.ai/settings/usage"
CODEX_URL = "https://chatgpt.com/codex/settings/usage"
POLL_SECONDS = 300

HOME = Path.home()
BUDGET_DIR = HOME / ".claude" / "budget"
PID_FILE = HOME / ".claude" / "budget-daemon.pid"
PROFILE_DIR = BUDGET_DIR / "playwright-profile"
CLAUDE_MD_PATH = BUDGET_DIR / "claude-budget.md"
CLAUDE_JSON_PATH = BUDGET_DIR / "claude-budget.json"
CODEX_MD_PATH = BUDGET_DIR / "codex-budget.md"
CODEX_JSON_PATH = BUDGET_DIR / "codex-budget.json"

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


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def ensure_dirs() -> None:
    BUDGET_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


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


def remaining_status_icon(remaining_pct: int) -> str:
    if remaining_pct >= 40:
        return "🟢"
    if remaining_pct >= 15:
        return "🟡"
    return "🔴"


def extract_first_int(text: str) -> int | None:
    match = re.search(r"(\d+)", text.replace(",", ""))
    return int(match.group(1)) if match else None


def extract_percent(text: str) -> int | None:
    match = re.search(r"(\d+)\s*%", text)
    return int(match.group(1)) if match else None


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


def js_extract_claude_progress(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        """
        () => {
          const bars = Array.from(document.querySelectorAll('[role="progressbar"]'));
          const findReset = (el) => {
            let node = el;
            for (let depth = 0; depth < 6 && node; depth += 1) {
              const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
              const match = text.match(/Resets in[^.\\n]*/i);
              if (match) return match[0].trim();
              node = node.parentElement;
            }
            return null;
          };
          return bars.map((el) => ({
            aria: el.getAttribute('aria-valuenow'),
            reset_text: findReset(el),
            context: ((el.parentElement && el.parentElement.textContent) || '').replace(/\\s+/g, ' ').trim(),
          }));
        }
        """
    )


def scrape_claude(page: Any) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector('[role="progressbar"]', timeout=30000)
    page.wait_for_timeout(2000)
    raw_entries = js_extract_claude_progress(page)
    errors: list[str] = []
    if len(raw_entries) < 2:
        errors.append(f"expected 2 Claude progress bars, found {len(raw_entries)}")

    session_used = extract_first_int(str(raw_entries[0].get("aria"))) if len(raw_entries) > 0 else None
    session_reset = raw_entries[0].get("reset_text") if len(raw_entries) > 0 else None
    weekly_used = extract_first_int(str(raw_entries[1].get("aria"))) if len(raw_entries) > 1 else None
    weekly_reset = raw_entries[1].get("reset_text") if len(raw_entries) > 1 else None

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
    return data, errors, raw_entries


def scrape_codex(page: Any) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    page.goto(CODEX_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("article", timeout=30000)
    page.wait_for_timeout(2000)
    cards = page.locator("article")
    raw_cards: list[dict[str, Any]] = []
    for index in range(cards.count()):
        text = normalize_space(cards.nth(index).inner_text())
        raw_cards.append({"index": index, "text": text})

    errors: list[str] = []
    mapped: dict[str, Any] = {
        "five_hour": None,
        "weekly": None,
        "code_review": None,
        "credits": None,
    }

    for card in raw_cards:
        lowered = card["text"].lower()
        if "5-hour limit" in lowered:
            mapped["five_hour"] = extract_percent(card["text"])
        elif "weekly limit" in lowered:
            mapped["weekly"] = extract_percent(card["text"])
        elif "code review" in lowered:
            mapped["code_review"] = extract_percent(card["text"])
        elif "credit" in lowered:
            mapped["credits"] = extract_first_int(card["text"])

    if mapped["five_hour"] is None:
        errors.append("codex.five_hour.remaining_pct missing")
    if mapped["weekly"] is None:
        errors.append("codex.weekly.remaining_pct missing")
    if mapped["code_review"] is None:
        errors.append("codex.code_review.remaining_pct missing")
    if mapped["credits"] is None:
        errors.append("codex.credits missing")

    data = {
        "five_hour": {"remaining_pct": mapped["five_hour"]},
        "weekly": {"remaining_pct": mapped["weekly"]},
        "code_review": {"remaining_pct": mapped["code_review"]},
        "credits": mapped["credits"],
    }
    return data, errors, raw_cards


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
        "five_hour": {"remaining_pct": previous_json.get("five_hour", {}).get("remaining_pct") if previous_json else None},
        "weekly": {"remaining_pct": previous_json.get("weekly", {}).get("remaining_pct") if previous_json else None},
        "code_review": {"remaining_pct": previous_json.get("code_review", {}).get("remaining_pct") if previous_json else None},
        "credits": previous_json.get("credits") if previous_json else None,
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
                f"{remaining_status_icon(session['remaining_pct']) if isinstance(session['remaining_pct'], int) else '⚪'} "
                f"**{fmt_or_unknown(session['remaining_pct'], '%')} remaining** "
                f"({fmt_or_unknown(session['used_pct'], '%')} used) — resets in {session['resets_in'] or 'unknown'}"
            ),
            "",
            "## Weekly",
            (
                f"{remaining_status_icon(weekly['remaining_pct']) if isinstance(weekly['remaining_pct'], int) else '⚪'} "
                f"**{fmt_or_unknown(weekly['remaining_pct'], '%')} remaining** "
                f"({fmt_or_unknown(weekly['used_pct'], '%')} used) — resets in {weekly['resets_in'] or 'unknown'}"
            ),
            "",
            "---",
            "_This file is managed by budget-daemon. Do not edit manually._",
        ]
    )
    return "\n".join(lines) + "\n"


def render_codex_markdown(payload: dict[str, Any], health: ScrapeHealth, timestamp: str, stopped: bool = False) -> str:
    five_hour = payload["five_hour"]["remaining_pct"]
    weekly = payload["weekly"]["remaining_pct"]
    code_review = payload["code_review"]["remaining_pct"]
    credits = payload["credits"]

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
            f"{remaining_status_icon(five_hour) if isinstance(five_hour, int) else '⚪'} **{fmt_or_unknown(five_hour, '%')} remaining**",
            "",
            "## Weekly Limit",
            f"{remaining_status_icon(weekly) if isinstance(weekly, int) else '⚪'} **{fmt_or_unknown(weekly, '%')} remaining**",
            "",
            "## Code Review",
            f"{remaining_status_icon(code_review) if isinstance(code_review, int) else '⚪'} **{fmt_or_unknown(code_review, '%')} remaining**",
            "",
            "## Credits Remaining",
            f"**{credits:,} credits** — extends beyond plan limits" if isinstance(credits, int) else "**unknown credits** — extends beyond plan limits",
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
        "code_review": payload["code_review"],
        "credits": payload["credits"],
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
        for key in ("five_hour", "weekly", "code_review"):
            if payload[key]["remaining_pct"] is None and scraped.get(key, {}).get("remaining_pct") is not None:
                payload[key]["remaining_pct"] = scraped[key]["remaining_pct"]
        if payload["credits"] is None and scraped.get("credits") is not None:
            payload["credits"] = scraped["credits"]
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
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            viewport={"width": 1440, "height": 1200},
        )
        try:
            page = context.new_page()
            try:
                claude_data, claude_errors, claude_raw = scrape_claude(page)
            except Exception as exc:
                claude_data = {
                    "session": {"used_pct": None, "remaining_pct": None, "resets_in": None},
                    "weekly": {"used_pct": None, "remaining_pct": None, "resets_in": None},
                }
                claude_errors = [f"claude scrape failed: {exc}"]
                claude_raw = {"error": str(exc)}

            try:
                codex_data, codex_errors, codex_raw = scrape_codex(page)
            except Exception as exc:
                codex_data = {
                    "five_hour": {"remaining_pct": None},
                    "weekly": {"remaining_pct": None},
                    "code_review": {"remaining_pct": None},
                    "credits": None,
                }
                codex_errors = [f"codex scrape failed: {exc}"]
                codex_raw = {"error": str(exc)}
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
        finally:
            context.close()


def run_once(debug: bool = False) -> int:
    ensure_dirs()
    timestamp = now_iso()
    try:
        claude_scraped, codex_scraped, claude_errors, codex_errors = scrape_all(debug=debug)
    except PlaywrightTimeoutError as exc:
        log(f"browser startup timeout: {exc}")
        return 1
    except Exception as exc:
        log(f"browser startup failure: {exc}")
        return 1

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


def run_auth_flow() -> int:
    ensure_dirs()
    log("opening headed Chromium for manual authentication")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 1200},
        )
        try:
            claude_page = context.new_page()
            codex_page = context.new_page()
            claude_page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=60000)
            codex_page.goto(CODEX_URL, wait_until="domcontentloaded", timeout=60000)
            log("sign in to both services, then close the browser window to finish")
            while context.pages:
                context.pages[0].wait_for_timeout(1000)
        finally:
            context.close()
    return 0


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
        while not STOP_REQUESTED:
            run_once(debug=debug)
            slept = 0
            while slept < POLL_SECONDS and not STOP_REQUESTED:
                time.sleep(1)
                slept += 1
    finally:
        write_stopped_notices()
        remove_pid_file_if_owned()
        log("daemon stopped")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Claude and Codex budget usage into local markdown and JSON files.")
    parser.add_argument("--auth", action="store_true", help="open a headed browser for first-time authentication")
    parser.add_argument("--once", action="store_true", help="run a single scrape cycle and exit")
    parser.add_argument("--debug", action="store_true", help="print raw scrape output")
    parser.add_argument("--ensure-running", action="store_true", help="start the daemon if it is not already running")
    return parser.parse_args()


def main() -> int:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    args = parse_args()

    if args.auth:
        return run_auth_flow()
    if args.ensure_running:
        return ensure_running()
    if args.once:
        return run_once(debug=args.debug)
    return poll_loop(debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())

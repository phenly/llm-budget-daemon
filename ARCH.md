# Architecture — budget-daemon

## Stack
- **Language**: Python 3.10+
- **Browser automation**: Playwright (sync API — simpler for a daemon, no async complexity)
- **Scheduling**: `time.sleep` loop inside daemon process (launchd owns restart/lifecycle)
- **Process lifecycle**: macOS launchd (KeepAlive plist) — not parent-process-tied

## Key Decisions

### launchd system daemon (not session-scoped)
**Decision**: Use launchd for lifecycle management. Drop parent-process (os.getppid) monitoring.
**Rationale**: Budget data must be available at CC session start — session-scoped daemon
has a 0-5 min warmup gap exactly when agents need it most. Orphan protection is handled
by the singleton PID guard, not parent monitoring. launchd restarts on crash automatically.

### Output path: ~/.claude/budget/ for all four files
**Decision**: All markdown and JSON output lives under ~/.claude/budget/.
**Rationale**: PRD had inconsistency between ~/.claude/ and ~/.claude/budget/. Subdirectory
keeps budget files grouped and avoids cluttering ~/.claude/ root.

### Playwright sync API
**Decision**: Use playwright.sync_api over async_api.
**Rationale**: The daemon is single-threaded with a fixed poll loop. Async adds complexity
with no benefit — no concurrent scrapes are needed.

### Single script, no package structure
**Decision**: One file at ~/scripts/claude-budget-daemon.py.
**Rationale**: PRD explicitly requires "single Python script." No pip-installable package needed.

## Daemon Architecture

```
main()
  └── parse args (--auth, --once, --debug, --ensure-running)
      ├── --auth: launch_headed_browser() → exit
      ├── --ensure-running: check_singleton() → start daemon or exit if alive
      ├── --once: scrape_all() → write_files() → exit
      └── (default): write_pid() → poll_loop()

poll_loop()
  └── every 300s:
      ├── scrape_claude()    → ClaudeData | ScrapeError
      ├── scrape_codex()     → CodexData | ScrapeError
      ├── write_claude_files(data)
      └── write_codex_files(data)

scrape_claude(page)
  → navigate to https://claude.ai/settings/usage
  → extract 2x progressbar aria-valuenow + adjacent reset text
  → return ClaudeData(session_pct_used, session_resets_in, weekly_pct_used, weekly_resets_in)

scrape_codex(page)
  → navigate to https://chatgpt.com/codex/settings/usage
  → extract 4x article cards by label text
  → return CodexData(five_hour_pct, weekly_pct, code_review_pct, credits_remaining)
```

## Scrape Health Model

Each scraper returns either a full data object or raises a partial failure. After
extraction, validate all expected fields are non-null. If any field missing:
- Set scrape_health.status = "degraded", list failed fields in errors
- Do NOT overwrite last good output files
- Append ⚠️ SCRAPE WARNING block to markdown output
- Log to stdout with timestamp

If all fields present:
- Set scrape_health.status = "ok", record last_clean_scrape timestamp
- Write normally

## File Formats

### claude-budget.md
```
# Claude Budget
_Last updated: 2026-03-30T12:00:00 — next update in ~5 min_

## Session
🟢 **85% remaining** (15% used) — resets in 2 hr 30 min

## Weekly
🟡 **32% remaining** (68% used) — resets in 3 days

---
_This file is managed by budget-daemon. Do not edit manually._
```

### codex-budget.md
```
# Codex Budget
_Last updated: 2026-03-30T12:00:00 — next update in ~5 min_

## 5-Hour Limit
🟢 **78% remaining**

## Weekly Limit
🟢 **91% remaining**

## Code Review
🟢 **100% remaining**

## Credits Remaining
**1,240 credits** — extends beyond plan limits

---
_This file is managed by budget-daemon. Do not edit manually._
```

### Status indicators
- 🟢 >= 40% remaining
- 🟡 15–39% remaining
- 🔴 < 15% remaining

## launchd Plist
Location: ~/Library/LaunchAgents/com.phenly.budget-daemon.plist
- Label: com.phenly.budget-daemon
- ProgramArguments: [python3, ~/scripts/claude-budget-daemon.py]
- KeepAlive: true
- RunAtLoad: true
- StandardOutPath: ~/Library/Logs/budget-daemon.log
- StandardErrorPath: ~/Library/Logs/budget-daemon.log

## CLAUDE.md Integration Snippet
```bash
# Budget daemon — read files written by launchd-managed daemon
# To check status: cat ~/.claude/budget/claude-budget.md
# To install daemon: launchctl load ~/Library/LaunchAgents/com.phenly.budget-daemon.plist
```
Agents read the files directly. They do NOT start the daemon — launchd owns that.

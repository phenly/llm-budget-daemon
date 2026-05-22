## last updated
agent: lead-engineer   date: 2026-05-22   session: 8

## project phase
complete — in production with macOS TCC hardening

## completed (all sessions)
- TASK-001–004: Initial scaffolding, browser scraping attempts (Playwright, camoufox) — abandoned
- TASK-005: Full rewrite to PTY approach (pexpect + pyte)
- TASK-006: Reliable PTY state machine for both CLIs (two-step send: command → echo → \r)
- TASK-007: Deployment resolved — env normalization in _spawn_cli(); launchd running since 2026-03-30 23:29
- TASK-008: Synced repo script to ~/scripts/; hardened scrape_codex() with /status retry + 20s timeout
- TASK-009: Removed status indicator emoji/thresholds from markdown output
- TASK-010: Added install.sh, README, pushed to github.com/phenly/llm-budget-daemon
- TASK-011: Stable-identity launcher bundle to stop macOS TCC re-prompting on every claude/codex version bump (2026-05-22)

## in progress
(none)

## next task
(none — monitor whether TASK-011 fully eliminates TCC prompts after the next claude auto-update)

## known follow-ups (not yet needed)
- If macOS still attributes TCC prompts to the versioned inner binary (e.g. `2.1.146`) instead of the launcher bundle after the next claude update, add an auto `codesign --force --sign - --identifier com.phenly.claude-cli <resolved-path>` step inside `PersistentCLISession.ensure_alive()` (and the equivalent for codex). Trigger only when the resolved real-path changes.

## decisions log
- 2026-03-30: output path → ~/.claude/budget/ for all 4 files (markdown + JSON)
- 2026-03-30: lifecycle → launchd user agent. Dropped parent-process monitoring.
- 2026-03-30: Python 3.9 compat confirmed — script uses `from __future__ import annotations`
- 2026-03-30: Replaced Playwright/camoufox with PTY approach (pexpect + pyte)
- 2026-03-30: Dropped Codex code_review % and credits — not available via CLI
- 2026-03-30: Codex gains reset timing for 5h and weekly (unavailable via web scraping)
- 2026-03-31: Deployment env — Option 1 chosen (env normalization in Python, not plist)
- 2026-03-31: No status indicators in output — agents assess budget sufficiency for their task scope
- 2026-03-31: Budget check integration → skill personas (lead-engineer, backend-dev, frontend-dev), not global CLAUDE.md
- 2026-05-22: macOS TCC re-prompts solved with a stable-identity .app bundle (`bundle/PhenlyBudgetDaemon.app`, id `com.phenly.budget-daemon`) installed to `~/Applications/` and ad-hoc signed in `install.sh`. The bundle's `Contents/MacOS/launcher` resolves and execs the current claude/codex versioned binary. `_spawn_cli` invokes through the launcher when present and pins `cwd` to `~/.claude/budget` so CLIs don't scan from `/` for project context (which was triggering Music/Photos/Drive prompts). `BUDGET_DAEMON_LAUNCHER=` env var disables the launcher for local debugging.

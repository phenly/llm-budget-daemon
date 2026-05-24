## last updated
agent: lead-engineer   date: 2026-05-24   session: 9

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
- TASK-011: Stable-identity launcher bundle to stop macOS TCC re-prompting on every claude/codex version bump (2026-05-22) — INSUFFICIENT, see TASK-012
- TASK-012: Stable-path hardlink launcher to fully stop TCC re-prompting (2026-05-24). TASK-011's launcher used `exec` to jump straight to the versioned inner binary (e.g. `~/.local/share/claude/versions/2.1.142`); TCC keys consent on the executable's file path, and the filename literally is the version number, so every claude/codex auto-update still read as a new "app" to TCC and the prompts continued (user reported `"2.1.142" would like to access ... OneDrive / Documents / data from other apps`). The Anthropic Developer ID signature is already stable across versions — only the path varies. Fix: launcher now maintains hardlinks at `~/.claude/budget/helpers/{claude,codex}` to whichever versioned binary is current, and exec's the hardlink. Hardlinks share the source inode so the publisher's signature/cdhash stays valid; TCC sees stable path + stable Developer ID and one grant survives all future version bumps. The launcher self-heals: each spawn compares source vs. target inode and rebuilds the hardlink if claude/codex has auto-updated.

## in progress
(none)

## next task
(none — monitor that TCC prompts stop appearing on the next claude/codex auto-update)

## known follow-ups (not yet needed)
- If macOS still re-prompts after TASK-012 across a version bump, the next escalation is to also install the helper directory under a path TCC has already granted (e.g. inside the bundle's `Contents/Helpers/`) — but adding files inside a codesigned bundle requires re-signing the whole bundle. Keep helpers outside the bundle unless this becomes necessary.

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

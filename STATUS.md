## last updated
agent: lead-engineer   date: 2026-03-31   session: 7

## project phase
complete — daemon running in production under launchd; all scrape cycles nominal

## completed this session
- TASK-007: Deployment strategy resolved — Option 1 (env normalization in _spawn_cli) confirmed working; launchd running since 2026-03-30 23:29
- TASK-008: Synced repo script to ~/scripts/claude-budget-daemon.py (installed was behind)
- TASK-009: Hardened scrape_codex() with /status retry + increased timeout (20s) via Codex

## in progress
(none)

## next task
(none — project is in production. Monitor ~/Library/Logs/budget-daemon.log for regressions)
brief: |
  Full test results changed the problem framing:

  What is proven:
  - Repo version of claude-budget-daemon.py succeeds when run directly:
    `python3 claude-budget-daemon.py --once --debug`
    produced `claude=ok codex=ok`.
  - PTY state machine for Claude and Codex is working in the direct-run path.
  - Installed launchd daemon was initially stale (old camoufox script in ~/scripts).
    It has since been updated to the repo PTY version.

  What failed in deployed-service testing:
  - Under launchd, first failure was executable resolution:
    - `claude not found in PATH`
    - `codex not found in PATH`
  - Codex added fallback paths and now works in launchd-like stripped env runs.
  - Claude remains environment-sensitive in stripped env runs:
    - instead of normal `Claude Pro` + `/usage` screen, it can start in an
      unauthenticated/alternate state (`API Usage Billing`, `Not logged in`, hook errors).
  - In a richer env test with explicit:
    - `HOME=/Users/phenly`
    - `USER=phenly`
    - `LOGNAME=phenly`
    - `SHELL=/bin/zsh`
    - `PATH=/Users/phenly/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin`
    the installed script returned `claude=ok codex=ok`.

  User concern raised during this session:
  - "I wonder if codex trying to launch claude code is creating a problem here."
  - Current working hypothesis: the scraper logic is fine; the remaining issue is the
    environment Claude inherits when launched from a non-normal shell context
    (launchd / Codex-mediated execution), not the `/usage` PTY state machine itself.

  Options for lead-engineer to evaluate:
  1. Minimal env normalization in Python
     - Pass explicit child env to `pexpect.spawn()` for both CLIs:
       `HOME`, `USER`, `LOGNAME`, `SHELL`, and a sane PATH.
     - Goal: make launchd child processes behave like an interactive user shell.
     - Pros: self-contained in script.
     - Cons: may still miss a Claude-specific env/config dependency.

  2. Configure launchd environment explicitly
     - Put required PATH and possibly other env vars in the plist instead of Python.
     - Pros: keeps script simpler.
     - Cons: more launchd-specific; may still need Python-side fallbacks.

  3. Special-case Claude invocation only
     - Keep Codex path fix; treat Claude as requiring a richer env than Codex.
     - Pros: minimal blast radius.
     - Cons: asymmetry and more implicit behavior.

  4. Reconsider daemon execution context
     - If Claude auth/session depends on the user's normal shell/session environment,
       launchd may not be the right place to spawn Claude directly without extra setup.
     - Pros: addresses root deployment assumption.
     - Cons: larger architectural change; reopens lifecycle decisions.

  Requested lead-engineer decision:
  - choose the deployment strategy for Claude under launchd
  - define the minimum acceptable production test before docs are updated
  - decide whether to keep pushing launchd compatibility or adjust runtime model

## codex entry
date: 2026-03-30
task: TASK-007 — launchd executable resolution + deployed daemon verification
result: paused for lead-engineer review
notes:
- Full test found the real remaining blocker: launchd starts the updated daemon in a stripped-down environment.
- Direct repo run succeeded, but the installed service logged:
  - `claude scrape failed: claude not found in PATH`
  - `codex scrape failed: codex not found in PATH`
- Lead-engineer recommendation: fix executable resolution in Python, not by relying on plist PATH.
- Added absolute-path fallbacks for `claude` and `codex`.
- Follow-up diagnosis: launchd-like env still broke Claude auth/session state unless `HOME`, `USER`, `LOGNAME`, `SHELL`, and a fuller `PATH` were present.
- In stripped env tests:
  - Codex could be made reliable.
  - Claude sometimes switched into a different state (`API Usage Billing`, `Not logged in`) instead of the expected `Claude Pro` `/usage` screen.
- Discussion with user clarified the concern that "Codex launching Claude Code" may itself be part of the issue because Claude appears sensitive to the inherited environment.
- Per user request, stopped short of pushing further code changes and documented findings/options here for lead-engineer evaluation.
## codex entry
date: 2026-03-30
task: TASK-006 — reliable PTY state machine for both CLIs
result: complete
notes:
- Replaced the old run_cli_capture timing approach with explicit PTY polling states.
- Claude now sends `/usage`, waits for the echoed command, then sends `\r`.
- Codex now tries the startup banner first, then falls back to `/status` using the same two-step send pattern.
- Added a short post-match stabilization read so Codex returns the full status box instead of a partial render.
- parse_codex_output() now handles single-line limit rows and falls back to the status bar `X% left` value for five_hour when needed.

debug output from `python3 claude-budget-daemon.py --once --debug`:
```json
{
  "claude_raw": "u▗ ▗   ▖ ▖  Claude Code v2.1.88\n           Sonnet 4.6 with medium effort · Claude Pro\n  ▘▘ ▝▝    ~/Code/phenly/budget-daemon\n\n❯ /usage\n\n────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────\n   Status   Config   Usage\n\n  Current session\n  ████████████████████████████████████████▌          81% used\n  Resets 1am (America/New_York)\n\n  Current week (all models)\n  ████████████████████▌                              41% used\n  Resets 12pm (America/New_York)\n\n  Extra usage\n  Extra usage not enabled · /extra-usage to enable\n\n  Esc to cancel",
  "codex_raw": "╭──────────────────────────────────────────────╮\n│ >_ OpenAI Codex (v0.117.0)                   │\n│                                              │\n│ model:     gpt-5.4 medium   /model to change │\n│ directory: ~/Code/phenly/budget-daemon       │\n╰──────────────────────────────────────────────╯\n\n  Tip: Use /feedback to send logs to the maintainers when something looks off.\n\n/status\n\n╭─────────────────────────────────────────────────────────────────────────────────╮\n│  >_ OpenAI Codex (v0.117.0)                                                     │\n│                                                                                 │\n│ Visit https://chatgpt.com/codex/settings/usage for up-to-date                   │\n│ information on rate limits and credits                                          │\n│                                                                                 │\n│  Model:                gpt-5.4 (reasoning medium, summaries auto)               │\n│  Directory:            ~/Code/phenly/budget-daemon                              │\n│  Permissions:          Custom (workspace-write, on-request)                     │\n│  Agents.md:            <none>                                                   │\n│  Account:              kfennelly@gmail.com (Plus)                               │\n│  Collaboration mode:   Default                                                  │\n│  Session:              019d41de-3fbe-7ee2-895d-9d34a9d03e80                     │\n│                                                                                 │\n│  5h limit:             [███████████████████░] 94% left (resets 03:37 on 31 Mar) │\n│  Weekly limit:         [███████████████████░] 97% left (resets 15:50 on 6 Apr)  │\n╰─────────────────────────────────────────────────────────────────────────────────╯\n\n\n› Improve documentation in @filename\n\n  gpt-5.4 medium · 100% left · ~/Code/phenly/budget-daemon",
  "claude_parsed": {
    "session": {
      "used_pct": 81,
      "remaining_pct": 19,
      "resets_in": "1am (America/New_York)"
    },
    "weekly": {
      "used_pct": 41,
      "remaining_pct": 59,
      "resets_in": "12pm (America/New_York)"
    }
  },
  "codex_parsed": {
    "five_hour": {
      "remaining_pct": 94,
      "resets_in": "03:37 on 31 Mar"
    },
    "weekly": {
      "remaining_pct": 97,
      "resets_in": "15:50 on 6 Apr"
    }
  }
}
[2026-03-30 23:09:44] cycle complete: claude=ok codex=ok
```

## blockers
(none)

## decisions log
- 2026-03-30: output path → ~/.claude/budget/ for all 4 files (markdown + JSON). Resolved PRD inconsistency.
- 2026-03-30: lifecycle → launchd system daemon. Dropped parent-process monitoring.
- 2026-03-30: Python 3.9 compat confirmed — script uses `from __future__ import annotations`.
- 2026-03-30: Replaced Playwright Chromium with camoufox — subsequently abandoned.
- 2026-03-30: Pivoted to PTY approach (pexpect + pyte). Claude working. Codex pending.
- 2026-03-30: Dropped Codex code_review % and credits — not available via CLI.
- 2026-03-30: Codex gains reset timing for 5h and weekly (was unavailable via web scraping).

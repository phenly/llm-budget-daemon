# llm-budget-daemon

A lightweight macOS background daemon that polls your Claude Code and OpenAI Codex CLIs every five minutes and writes their budget usage to local markdown and JSON files.

## The problem

Claude Code and Codex agents have no visibility into remaining budget before starting a task. A large agentic run that kicks off near a session or weekly limit wastes compute, gets cut off mid-task, and leaves you with nothing useful. The bigger the task, the more this hurts.

## The solution

A persistent daemon that reads budget data directly from the CLIs and writes it somewhere agents can check before they start work:

```
~/.claude/budget/claude-budget.md   ← read by Claude Code agents
~/.claude/budget/codex-budget.md    ← read by Codex agents
~/.claude/budget/claude-budget.json ← JSON sidecar for tooling
~/.claude/budget/codex-budget.json  ← JSON sidecar for tooling
```

Example output:

```markdown
# Claude Budget
_Last updated: 2026-03-31T09:35:56 — next update in ~5 min_

## Session
🟢 **96% remaining** (4% used) — resets in 11am (America/New_York)

## Weekly
🟡 **38% remaining** (62% used) — resets in 12pm (America/New_York)
```

Status indicators: 🟢 ≥ 40% · 🟡 15–39% · 🔴 < 15%

If a scrape fails, the daemon preserves the last known good values and prepends a `⚠️ SCRAPE WARNING` block so agents know the data is stale.

## How it works

Both CLIs are spawned in a PTY (pseudo-terminal) via `pexpect`. The daemon sends commands (`/usage` for Claude, `/status` for Codex), waits for the terminal to render the response, then reads the virtual screen with `pyte`. No browser, no web scraping, no credentials to manage.

Claude state machine:

1. Wait for `Claude Code v` in startup banner
2. Send `/usage`, wait for echo
3. Send `\r`, wait for `Current session`
4. Capture rendered screen, parse percentages and reset times

Codex state machine:

1. Check startup banner for `5h limit` (shown on first launch)
2. If not present, send `/status`, wait for echo, send `\r`
3. Retry once if `5h limit` still doesn't appear
4. Capture rendered screen, parse percentages and reset times

## Requirements

- macOS (uses launchd for persistent background execution)
- Python 3.9+
- Claude Code CLI (`claude`) and/or Codex CLI (`codex`) installed and authenticated

## Setup

```bash
pip install pexpect pyte
cp claude-budget-daemon.py ~/scripts/claude-budget-daemon.py
cp com.phenly.budget-daemon.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.phenly.budget-daemon.plist
```

## Usage

```bash
# Test a single cycle (outputs debug JSON)
python3 ~/scripts/claude-budget-daemon.py --once --debug

# Start the daemon manually
python3 ~/scripts/claude-budget-daemon.py

# Start only if not already running (safe to call repeatedly)
python3 ~/scripts/claude-budget-daemon.py --ensure-running

# View daemon logs
tail -f ~/Library/Logs/budget-daemon.log
```

## CLAUDE.md integration

Add this to your `~/.claude/CLAUDE.md` to have Claude Code check budget at session start:

```markdown
At session start, run in the background:
python3 ~/scripts/claude-budget-daemon.py --ensure-running

Before starting any large agentic task, read ~/.claude/budget/claude-budget.md.
If session remaining is below 15%, warn the user before proceeding.
```

## Credits

The PTY state machine approach — spawning CLIs in a pseudo-terminal and reading the rendered virtual screen rather than scraping the web — was inspired by [cc-usage-bar](https://github.com/lionhylra/cc-usage-bar) by [@lionhylra](https://github.com/lionhylra), a minimal macOS menu bar app that reads Claude Code usage accurately and safely. The key insight from that project: send `/usage`, wait for the echo, *then* send `\r` — doing both in one shot races the terminal renderer.

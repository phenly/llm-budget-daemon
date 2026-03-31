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
96% remaining (4% used) — resets in 11am (America/New_York)

## Weekly
38% remaining (62% used) — resets in 12pm (America/New_York)
```

Raw numbers only — no pre-baked thresholds. The agent reading the file knows what task it's about to run; it decides whether the remaining budget is sufficient for that scope.

If a scrape fails, the daemon preserves the last known good values and prepends a `⚠️ SCRAPE WARNING` block so agents can detect stale data without parsing JSON.

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
git clone https://github.com/phenly/llm-budget-daemon
cd llm-budget-daemon
./install.sh
```

The install script:
1. Checks for Python 3.9+
2. Installs `pexpect` and `pyte` via pip
3. Copies the daemon to `~/scripts/`
4. Installs and loads the launchd plist
5. Runs a smoke test (`--once --debug`) to confirm both CLIs are scraped successfully

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

## Agent integration

The budget files are plain text — any agent or sub-agent can read them at any time, not just at session start. The intended pattern:

**Session start** — ensure the daemon is running:
```markdown
At session start, run in the background:
python3 ~/scripts/claude-budget-daemon.py --ensure-running
```

**Before kicking off a task** — check remaining budget and factor it into planning:
```markdown
Before starting any multi-step task, read ~/.claude/budget/claude-budget.md
and assess whether the remaining session and weekly budget is sufficient for
the scope of the work. Adjust the plan or flag it to the user if not.
```

**Sub-agents** — because the files live on disk, any spawned sub-agent can read them independently without being passed budget context by the orchestrator. This is intentional: a sub-agent that's about to do something expensive can self-check.

**Custom UIs** — the JSON sidecars (`claude-budget.json`, `codex-budget.json`) are designed for programmatic consumption. Poll them to display budget in a status bar, dashboard, or any other interface.

## Credits

The PTY state machine approach — spawning CLIs in a pseudo-terminal and reading the rendered virtual screen rather than scraping the web — was inspired by [cc-usage-bar](https://github.com/lionhylra/cc-usage-bar) by [@lionhylra](https://github.com/lionhylra), a minimal macOS menu bar app that reads Claude Code usage accurately and safely. The key insight from that project: send `/usage`, wait for the echo, *then* send `\r` — doing both in one shot races the terminal renderer.

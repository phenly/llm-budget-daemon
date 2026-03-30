## project
name: budget-daemon
repo: local — /Users/phenly/Code/phenly/budget-daemon
stack: Python 3.10+ / Playwright / macOS launchd

## team
lead-engineer: Claude Code
codex-role: implementer (primary coder)
active-personas: backend-dev

## ways of working
- sub-agents read STATUS.md + task-relevant docs only before starting
- blockers are written to STATUS.md immediately; do not block silently
- all inter-agent communication routes through lead engineer
- handoff.md is written at 70% context pressure or task completion
- do not infer requirements not in PRD.md or ARCH.md — surface ambiguity as a blocker
- do not communicate with other agents directly — use STATUS.md
- claude.ai planning sessions consume the same CC budget as Claude Code — keep design sessions short

## output paths (canonical)
- script:       ~/scripts/claude-budget-daemon.py
- launchd plist: ~/Library/LaunchAgents/com.phenly.budget-daemon.plist
- pid file:     ~/.claude/budget-daemon.pid
- claude md:    ~/.claude/budget/claude-budget.md
- claude json:  ~/.claude/budget/claude-budget.json
- codex md:     ~/.claude/budget/codex-budget.md
- codex json:   ~/.claude/budget/codex-budget.json

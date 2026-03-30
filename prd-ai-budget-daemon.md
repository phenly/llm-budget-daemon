SUMMARY

Build a background daemon script that scrapes claude.ai and ChatGPT Codex usage pages every five minutes and writes the results to two separate budget files readable by AI agents. The daemon runs persistently in the background, authenticates via a saved browser profile, and outputs structured markdown files that Claude Code agents and Codex agents can read independently to make informed decisions about whether to start expensive agentic tasks.


BUSINESS GOALS

Claude Code and Codex agents currently have no visibility into available usage budget before starting a task. This creates waste when a large agentic run kicks off near a session or weekly limit. The budget daemon gives agents the context they need to self-regulate — deferring, scoping down, or warning the user when capacity is low. The lead-eng agent benefits from reading both files to coordinate across model providers.


ADDITIONAL CONTEXT

Claude usage page (https://claude.ai/settings/usage) exposes two progress bars via aria-valuenow attributes on [role="progressbar"] elements, reported as percent used. Each bar has an adjacent text node with reset timing ("Resets in X hr Y min").

Codex usage page (https://chatgpt.com/codex/settings/usage) exposes four cards rendered as <article> elements. Three cards (5-hour limit, weekly limit, code review) show percent remaining as a text node inside the card header and as an inline style="width: X%;" on a div inside the card. A fourth card shows raw credits remaining as a plain integer. Codex does not expose reset timing in the DOM.

Claude reports percent used. Codex reports percent remaining. The output files should normalize both to remaining percentage for consistency.

Authentication is handled via a persistent Chromium browser profile (~/.claude-browser-profile). The user authenticates once in a headed browser session; all subsequent daemon runs use the saved session headlessly. This avoids credential handling in the script.

Two output files are written independently so each agent type only reads what it needs:
- ~/.claude/claude-budget.md — consumed by Claude/CC agents
- ~/.claude/codex-budget.md — consumed by Codex agents
- The lead-eng agent reads both

A JSON sidecar is written alongside each markdown file for programmatic consumption by other tooling.


REQUIREMENTS

DAEMON BEHAVIOR
- Poll both usage pages every five minutes
- Run as a persistent background process with graceful SIGINT/SIGTERM handling
- Support --auth flag to launch a headed browser for first-time authentication
- Support --once flag to run a single scrape cycle and exit (for testing and cron use)
- Support --debug flag to print raw scrape output before writing files
- Log each write cycle to stdout with timestamp

CLAUDE SCRAPER
- Navigate to https://claude.ai/settings/usage
- Extract session usage: percent used and reset timing from the first [role="progressbar"] and adjacent text node
- Extract weekly usage: percent used and reset timing from the second [role="progressbar"] and adjacent text node
- Convert percent used to percent remaining for output consistency
- Handle scrape errors gracefully without overwriting the last known good file

CODEX SCRAPER
- Navigate to https://chatgpt.com/codex/settings/usage
- Extract the following from the four <article> cards by matching the label text in the card header:
  - 5-hour limit: percent remaining (text node)
  - Weekly limit: percent remaining (text node)
  - Code review: percent remaining (text node)
  - Credits remaining: integer value (text node)
- Handle scrape errors gracefully without overwriting the last known good file

OUTPUT — ~/.claude/budget/claude-budget.md
- Header: "# Claude Budget" with last-updated timestamp and next-update note
- Session section: percent remaining, percent used, resets-in text
- Weekly section: percent remaining, percent used, resets-in text
- Status indicator per section: green (>= 40% remaining), yellow (15–39%), red (< 15%)
- Footer noting the file is daemon-managed

OUTPUT — ~/.claude/budget/codex-budget.md
- Header: "# Codex Budget" with last-updated timestamp and next-update note
- 5-hour limit section: percent remaining with status indicator
- Weekly limit section: percent remaining with status indicator
- Code review section: percent remaining with status indicator
- Credits section: raw integer with note that credits extend beyond plan limits
- Footer noting the file is daemon-managed

SCRAPE HEALTH AND STALENESS DETECTION
- After each scrape cycle, validate that every expected field was successfully extracted (no null/None values)
- If any field is missing or unparseable, set scrape_health.status to "degraded" in the JSON sidecar and include a human-readable list of which fields failed under scrape_health.errors
- If all fields extracted cleanly, set scrape_health.status to "ok" and record the timestamp under scrape_health.last_clean_scrape
- When a scrape is degraded, do not overwrite the previously written values in the output files — preserve the last known good data and append a visible warning block at the top of the markdown file indicating which fields are stale and when they were last successfully read
- The markdown warning block should be clearly delimited (e.g. "⚠️ SCRAPE WARNING") so agents can detect it without parsing JSON
- Log scrape errors to stdout with timestamp and field names so the daemon's output stream is useful for debugging

SINGLETON AND LIFECYCLE
- On startup the daemon writes its PID to ~/.claude/budget-daemon.pid
- Before writing the PID file, check if one already exists: read the PID and test whether that process is alive (os.kill(pid, 0)). If alive, exit cleanly with a message ("daemon already running, PID XXXXX"). If the PID is dead or the file is missing, proceed and write a fresh PID file
- Support an --ensure-running flag that performs the above check and either starts the daemon or exits — this is the flag Claude Code should invoke so it never starts a duplicate
- On exit (SIGINT, SIGTERM, or normal termination) the daemon deletes the PID file and writes a final status line to both budget files indicating the daemon stopped and data should be considered stale
- The daemon must be tied to the Claude Code session that started it: when CC exits, the daemon exits. Implement this by having the daemon monitor its parent process ID (os.getppid()) and exit if the parent process is no longer alive, checking every 30 seconds alongside the normal poll loop
- CLAUDE.md integration should invoke the daemon with --ensure-running at session start, not as a raw background process

OUTPUT — JSON SIDECARS
- ~/.claude/claude-budget.json: structured object with last_updated ISO timestamp, session and weekly objects each containing used_pct, remaining_pct, resets_in, and a scrape_health object containing status ("ok" or "degraded"), errors (list of field names that failed), and last_clean_scrape timestamp
- ~/.claude/codex-budget.json: structured object with last_updated ISO timestamp, five_hour, weekly, code_review objects each containing remaining_pct, a credits integer field, and a scrape_health object with the same structure as above


INSTALLATION
- Single Python script with no dependencies beyond playwright (or similar tool)
- Setup instructions in script docstring: pip install playwright (or similar tool), playwright install chromium (or similar tool), run --auth once
- Launchd plist provided for macOS auto-start on login


CLAUDE.MD INTEGRATION
The snippet added to CLAUDE.md should instruct Claude Code to:
- At session start, run: python3 ~/scripts/claude-budget-daemon.py --ensure-running in the background. Use --ensure-running, never invoke the daemon directly, to prevent duplicate processes
# Steam Review Agent — CLAUDE.md

## Context
This is a side project that a PM is making to explore agentic workflow automation. When collaborating with the PM, explain the tradeoffs between options, your suggestion, and then allow the PM to make the final decision.

## What this project does

Agentic Python pipeline: pulls the last 7 days of Steam reviews for a given game, uses the Claude API to categorize reviews by theme and summarize sentiment, then sends an email digest via SendGrid.

```
Steam API → raw reviews → Claude (categorize + summarize) → SendGrid (email)
```

## Running the agent

Single-game mode (original):
```bash
python3 agent.py --app_id 1245620 --game_name "Elden Ring"
```

Multi-game mode (config file):
```bash
python3 agent.py --config games.json
```

Each module also has its own `__main__` block for isolated testing:

```bash
python3 steam.py --app_id 1245620
python3 analyze.py --app_id 1245620
python3 email_sender.py --app_id 1245620 --game_name "Elden Ring"
```

## Project structure

```
agent.py          # CLI entry point — orchestrates all three phases
steam.py          # Phase 1: fetches reviews from Steam public API
analyze.py        # Phase 2: sends reviews to Claude, returns structured JSON
email_sender.py   # Phase 3: formats JSON into HTML email, sends via SendGrid
trends.py         # Trend storage + week-over-week spike detection (SQLite)
data/history.db   # SQLite DB — gitignored, created automatically on first run
requirements.txt
.env.example      # copy to .env and fill in keys
```

## Environment variables

Copy `.env.example` to `.env` and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
SENDGRID_API_KEY=SG....
FROM_EMAIL=you@example.com     # must match verified SendGrid sender
TO_EMAIL=you@example.com
```

All `load_dotenv()` calls use `override=True` so `.env` always wins over shell env vars.

## Key implementation details

### steam.py
- `fetch_game_name(app_id)` calls `store.steampowered.com/api/appdetails` to resolve a display name from an App ID — used by `agent.py` when `--game_name` is omitted or a config entry has no `game_name`. Falls back to `"App ID {app_id}"` label on failure rather than aborting.
- Hits `store.steampowered.com/appreviews/{app_id}` — no auth required
- Cursor-based pagination with `filter=recent` (newest-first); stops as soon as a review timestamp falls outside the 7-day window — avoids paginating old reviews
- Caps at 500 reviews via `random.sample()` if the window exceeds that
- Deduplicates cursors to guard against Steam's occasional repeated-cursor bug
- Raises `SteamAPIError` for bad App IDs, network failures, timeouts

### analyze.py
- Uses Claude tool use with `tool_choice={"type": "tool", "name": "submit_analysis"}` to force structured output — no JSON parsing needed, output is a Python dict directly from `block.input`
- Schema: `review_count`, `overall_sentiment`, `themes[]` (name, description, review_count, sentiment, representative_quotes), `flagged_spikes[]`
- Individual reviews are truncated to 300 chars before sending to Claude
- Model: `claude-sonnet-4-6`

### email_sender.py
- Named `email_sender.py` not `email.py` — `email.py` would shadow Python's stdlib `email` package and break `requests`
- Builds inline-CSS HTML email (no external stylesheets — email client compatibility)
- Sentiment color coding: 🟢 positive, 🔴 negative, 🟡 mixed
- SendGrid expects status 202 on successful send

### trends.py
- Persists each run's theme snapshot to `data/history.db` (SQLite, stdlib `sqlite3` — no new dependency)
- Schema: one row per `(app_id, run_date)`; upserts so running the agent twice on the same day overwrites rather than duplicates
- Spike detection is **proportion-based**: compares `theme_count / total_review_count` between weeks, not raw counts — handles weeks where the review volume differs
- Threshold: ≥50% relative increase in proportion flags a spike. Chosen over 2x (too harsh) after considering that 100→175 out of 500 reviews (a meaningful real-world jump) is only a 75% relative increase, not 100%
- Floor rules: existing themes need ≥10 reviews to be considered; new themes (not present last week) need ≥15 reviews to be flagged
- First run: no trend section in the email — baseline is silently saved. Comparison starts on the second run
- `trends.py --app_id <id>` prints the last saved run for inspection

### agent.py / games.json
- `--config games.json` runs the pipeline for every game in the file and sends one consolidated digest; `--app_id` still works for single-game runs (the two flags are mutually exclusive)
- Per-game errors are skip-and-continue: a failed game is flagged in the email but doesn't abort the rest
- `games.json` is a committed JSON array of `{app_id, game_name}` objects — edit it to add/remove tracked games
- `send_multi_digest` in `email_sender.py` builds one HTML email with a card group per game plus a red "Failed Games" card when any game errors

## Upcoming Features

- Scheduled execution — add a shell script or cron job so it actually runs every Monday morning without you touching it.
- Confidence scores on themes — ask Claude to include a confidence score (0–1) per theme.


## Git workflow

- Feature branches → PR → merge to main
- Branch naming: Claude Code uses `claude/` prefix for worktree branches
- `.env` is gitignored; `.env.example` is committed with placeholder values

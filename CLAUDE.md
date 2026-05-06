# Steam Review Agent — CLAUDE.md

## Context
This is a side project that a PM is making to explore agentic workflow automation. When collaborating with the PM, explain the tradeoffs between options, your suggestion, and then allow the PM to make the final decision.

## What this project does

Agentic Python pipeline: pulls the last 7 days of Steam reviews for a given game, uses the Claude API to categorize reviews by theme and summarize sentiment, then sends an email digest via SendGrid.

```
Steam API → raw reviews → Claude (categorize + summarize) → SendGrid (email)
```

## Running the agent

```bash
python3 agent.py --app_id 1245620 --game_name "Elden Ring"
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

## Upcoming Features

- Multi-game tracking with a config file — accept a list of app IDs and game names, run the pipeline for each, send one consolidated digest
- Week-over-week trend detection — store each run's analysis in a lightweight JSON file or SQLite DB, then compare this week's theme counts to last week's. Flag when a theme like "crashes" spikes 2x.
- Scheduled execution — add a shell script or cron job so it actually runs every Monday morning without you touching it. 
- Confidence scores on themes — ask Claude to include a confidence score (0–1) per theme. 
- Game name lookup — call the Steam store API to resolve the game name from the App ID automatically, so you don't need --game_name.


## Git workflow

- Feature branches → PR → merge to main
- Branch naming: Claude Code uses `claude/` prefix for worktree branches
- `.env` is gitignored; `.env.example` is committed with placeholder values

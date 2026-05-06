# Steam Review Agent — CLAUDE.md

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

## Next feature: week-over-week trend detection

The agreed next feature is storing each run's analysis as JSON and comparing theme counts week-over-week to surface changes (e.g. "crash reports up 2x this week"). Likely approach: persist runs to a `history/` directory as `{app_id}_{date}.json`, load the previous week's file on each run, pass both to Claude or do a simple diff in code.

## Git workflow

- Feature branches → PR → merge to main
- Branch naming: Claude Code uses `claude/` prefix for worktree branches
- `.env` is gitignored; `.env.example` is committed with placeholder values

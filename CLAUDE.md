# Steam Review Agent — CLAUDE.md

## Context
This is a side project that a PM is making to explore agentic workflow automation. When collaborating with the PM, explain the tradeoffs between options, your suggestion, and then allow the PM to make the final decision.

## What this project does

Agentic Python pipeline: pulls the last 7 days of Steam reviews for a given game, uses the Claude API to categorize reviews by theme and summarize sentiment, then sends an email digest via SendGrid. A Flask web dashboard provides a public-facing view of historical analysis data with admin controls.

```
Steam API → raw reviews → Claude (categorize + summarize) → SendGrid (email)
                                                          → SQLite (history.db)
                                                          → Flask dashboard
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

Running the dashboard:
```bash
python3 app.py
# opens at http://localhost:5000
```

Each module also has its own `__main__` block for isolated testing:

```bash
python3 steam.py --app_id 1245620
python3 analyze.py --app_id 1245620
python3 email_sender.py --app_id 1245620 --game_name "Elden Ring"
```

## Project structure

```
app.py            # Flask dashboard — routes, auth, backfill/refresh jobs
agent.py          # CLI entry point — orchestrates all three pipeline phases
steam.py          # Phase 1: fetches reviews from Steam public API
analyze.py        # Phase 2: sends reviews to Claude, returns structured JSON
email_sender.py   # Phase 3: formats JSON into HTML email, sends via SendGrid
trends.py         # DB layer — games table, runs table, trend spike detection
templates/        # Jinja2 HTML templates (base, index, game, login, add_game, backfill_status)
data/history.db   # SQLite DB — gitignored, created automatically on first run
games.json        # Legacy multi-game config for CLI mode (not used by dashboard)
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

# Dashboard
FLASK_SECRET_KEY=...           # any long random string
ADMIN_PASSWORD=...             # password for the admin login
```

All `load_dotenv()` calls use `override=True` so `.env` always wins over shell env vars.

## Key implementation details

### app.py (Flask dashboard)
- Public routes: `/` (game cards), `/game/<app_id>` (detail with charts)
- Admin routes (session-gated): `/admin/add`, `/admin/game/<id>/remove`, `/admin/game/<id>/refresh-votes`, `/admin/game/<id>/reanalyze`
- Auth: single `ADMIN_PASSWORD` env var, Flask session cookie — no user system
- Long-running jobs (backfill, vote refresh, reanalyze) run in daemon threads; frontend polls `/api/status/<job_id>` every 2s until complete
- Job state is in-memory (`_jobs` dict + lock) — resets on server restart, which is fine
- Smart re-add: if a removed game is re-added and runs already exist in the DB, runs the cheap vote refresh instead of the full Claude backfill
- `init_db()` is called at module level so it runs on every startup

### dashboard UI
- **Summary cards (4):** Current Steam Rating (all-time, live from Steam API), Last 7-Day Rating (computed from latest run's vote ratio mapped to Steam's label scale), Last 7-Day AI Sentiment (Claude's `overall_sentiment`), Last Analysis Run (date + review count)
- **Last 7-Day Rating** uses a two-axis Steam model: positive % + review count bucket (1–49 / 50–499 / 500+). Shows a yellow "low sample size" warning when < 10 reviews. Returns `None` only when total = 0.
- **Rating color scale** (9-step gradient): Overwhelmingly Positive → `green-500`, Very Positive → `green-400`, Mostly Positive / Positive → `green-300`, Mixed → `yellow-400`, Mostly Negative → `orange-400`, Negative → `red-400`, Very/Overwhelmingly Negative → `red-500`
- **Sentiment Trend chart:** stacked bars of AI theme sentiment (positive/mixed/negative) per week. Raw theme sentiment counts — no scaling to vote totals. Whole-number y-axis ticks, 10% grace padding. Steam vote bars removed.
- **Top Review Themes chart:** horizontal bar chart with week-toggle buttons, sorted by `review_count` descending. Theme labels truncated to 26 chars; tooltip sorts before index lookup so full name always matches the hovered bar. Whole-number x-axis ticks, 10% grace padding.
- **AI sentiment color palette** (consistent across both charts): positive `rgba(74,222,128)`, mixed `rgba(251,146,60)`, negative `rgba(248,113,113)`. Bars render at 0.75 alpha at rest, 1.0 on hover.
- **0-themes warning:** AI Sentiment card shows a yellow warning when a run has >0 reviews but 0 themes — distinguishes analysis failures from legitimate empty results
- **Admin panel** always visible on game detail page, even before any runs exist (so newly added games can be removed immediately)
- `static/tailwind.min.css` + Chart.js v4 — no build step. Tailwind CDN was replaced with a locally-generated file to avoid Edge's Strict Tracking Prevention flagging the third-party script as "Not Secure". To regenerate after adding new Tailwind classes: `npx tailwindcss@3 -i /tmp/tailwind-input.css -o static/tailwind.min.css --minify --content "templates/**/*.html"` (where `tailwind-input.css` contains the three `@tailwind` directives)

### Admin actions (game detail page)
- **Refresh Steam ratings** — fetches 37 days of Steam thumbs up/down and updates vote counts on existing runs. No Claude calls. Aborts if Steam returns 0 reviews to prevent wiping existing vote data.
- **Re-run AI analysis** — triggers a full 30-day per-week backfill via `_run_backfill`, overwriting existing runs with fresh Claude analysis. Use to fix bad data (0 themes, wrong sentiment) without removing and re-adding the game.
- **Remove game** — removes from the `games` table only; run history in `runs` is preserved so re-adding skips the Claude backfill.

### steam.py
- `fetch_reviews(app_id, window_days=7, end_cutoff_ts=None)` — fetches reviews from `[now - window_days, end_cutoff_ts)`. When `end_cutoff_ts` is None it defaults to now, giving standard "last N days" behaviour. Pass an explicit `end_cutoff_ts` to fetch a historical slice (used by per-week backfill).
- `fetch_review_summary(app_id)` — single lightweight request returning `{review_score_desc, total_positive, total_negative, total_reviews}` for the all-time rating card
- `fetch_game_name(app_id)` — resolves display name from Steam store API
- Cursor-based pagination with `filter=recent` (newest-first); stops as soon as a review timestamp falls outside the window
- Caps at 500 reviews via `random.sample()` if the window exceeds that; early-exit at 1000 collected to avoid excessive API calls
- Deduplicates cursors to guard against Steam's occasional repeated-cursor bug
- Raises `SteamAPIError` for bad App IDs, network failures, timeouts

### analyze.py
- Uses Claude tool use with `tool_choice={"type": "tool", "name": "submit_analysis"}` to force structured output — no JSON parsing needed, output is a Python dict directly from `block.input`
- Schema: `review_count`, `overall_sentiment`, `themes[]` (name, description, review_count, sentiment, representative_quotes, confidence 0–1), `flagged_spikes[]`
- Individual reviews are truncated to 300 chars before sending to Claude
- Model: `claude-sonnet-4-6`, `max_tokens=4096`
- Raises `RuntimeError` if `stop_reason == "max_tokens"` — truncated responses fail loudly rather than silently saving partial data (e.g. 0 themes with a valid sentiment)
- **Sub-theme splitting:** when a topic has clearly opposing camps, Claude splits it into two themes using " — Praised" / " — Criticized" suffixes (e.g. "Difficulty & Challenge — Praised" and "Difficulty & Challenge — Criticized") rather than collapsing into a single mixed theme

### email_sender.py
- Named `email_sender.py` not `email.py` — `email.py` would shadow Python's stdlib `email` package and break `requests`
- Builds inline-CSS HTML email (no external stylesheets — email client compatibility)
- Sentiment color coding: 🟢 positive, 🔴 negative, 🟡 mixed
- SendGrid expects status 202 on successful send

### trends.py
- **`games` table:** `(id, app_id, game_name, added_at)` — source of truth for which games the dashboard tracks. Separate from `runs` so removing a game from the dashboard doesn't delete historical data
- **`runs` table:** one row per `(app_id, run_date)`; upserts so re-running on the same date overwrites. Columns: `review_count`, `overall_sentiment`, `themes_json`, `positive_count`, `negative_count`
- `save_run()` accepts optional `run_date` (for backfill) and `positive_count`/`negative_count` (actual Steam vote counts)
- `get_all_runs(app_id)` returns all runs oldest-first for charting
- `update_run_vote_counts()` updates only the vote columns on an existing run — used by the vote refresh job (no Claude calls)
- Spike detection is **proportion-based**: compares `theme_count / total_review_count` between weeks. Threshold: ≥50% relative increase. Floor rules: ≥10 reviews for existing themes, ≥15 for new themes
- First run: baseline saved silently; trend comparison starts on the second run
- SQLite migration handled in `init_db()` via `ALTER TABLE ADD COLUMN` wrapped in try/except for existing DBs

### agent.py / games.json
- `--config games.json` runs the pipeline for every game in the file and sends one consolidated digest; `--app_id` still works for single-game runs (mutually exclusive)
- Per-game errors are skip-and-continue: a failed game is flagged in the email but doesn't abort the rest
- `games.json` is a legacy config for CLI mode — the dashboard manages games via the `games` DB table instead
- `send_multi_digest` in `email_sender.py` builds one HTML email with a card group per game plus a red "Failed Games" card when any game errors
- Weekly runs pass `positive_count`/`negative_count` to `save_run()` so vote data is stored from CLI runs too

### schedule.sh / run_weekly.sh
- `run_weekly.sh` — wrapper invoked by cron; `cd`s to the project dir, runs `agent.py --config games.json`, appends stdout/stderr to `data/run_weekly.log`
- `schedule.sh` — manages the cron entry with `--enable` (Mondays 8 AM), `--disable`, `--status`; uses a `# steam-review-agent` marker to find/remove its own cron line without touching other jobs

## Deployment

- Deployed on Railway at `reviews.jonathanpaek.com`
- `DB_PATH` is set via Railway's environment variables panel to point at a persistent Volume — do NOT put `DB_PATH` in `.env` (it would override the Railway path locally and break local runs)
- Local runs default to `data/history.db` (the `trends.py` fallback when `DB_PATH` is unset)
- weekly-digest cron service added on Railway (currently inactive, runs python agent.py --config games.json)

## Pending decisions
- **60/90 day backfill** — the UI currently only offers a 30-day window; easy to add options, deferred to control Claude API costs

## Git workflow

- Feature branches → PR → merge to main
- Branch naming: Claude Code uses `claude/` prefix for worktree branches
- `.env` is gitignored; `.env.example` is committed with placeholder values

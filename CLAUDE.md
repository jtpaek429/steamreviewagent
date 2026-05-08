# Steam Review Agent — CLAUDE.md

## Context
Side project by a PM exploring agentic workflow automation. Explain tradeoffs and your suggestion, then let the PM decide.

## What this project does

```
Steam API → raw reviews → Claude (categorize + summarize) → SendGrid (email)
                                                          → SQLite (history.db)
                                                          → Flask dashboard
```

## Running locally

```bash
python3 app.py                                        # dashboard at http://localhost:5000
python3 agent.py --app_id 1245620 --game_name "Elden Ring"   # single-game CLI
python3 agent.py --config games.json                  # multi-game CLI
```

Each module has a `__main__` block for isolated testing (`python3 steam.py --app_id 1245620`, etc.).

Local DB defaults to `data/history.db`. To test against a different DB: `DB_PATH=/path/to/history.db python3 app.py`.

## Project structure

```
app.py            # Flask dashboard — routes, auth, background jobs
agent.py          # CLI entry point — orchestrates pipeline phases
steam.py          # Phase 1: fetches reviews from Steam public API
analyze.py        # Phase 2: sends reviews to Claude, returns structured JSON
email_sender.py   # Phase 3: formats JSON into HTML email, sends via SendGrid
trends.py         # DB layer — games table, runs table, trend spike detection
templates/        # Jinja2 HTML templates
data/history.db   # SQLite DB — gitignored, created on first run
games.json        # Legacy multi-game config for CLI (not used by dashboard)
.env.example      # copy to .env and fill in keys
```

## Environment variables

```
ANTHROPIC_API_KEY=sk-ant-...
SENDGRID_API_KEY=SG....
FROM_EMAIL=you@example.com     # must match verified SendGrid sender
TO_EMAIL=you@example.com
FLASK_SECRET_KEY=...           # any long random string
ADMIN_PASSWORD=...             # dashboard admin login
```

`load_dotenv(override=True)` — `.env` always wins over shell env vars. Do NOT put `DB_PATH` in `.env`; it's set via Railway's env panel for prod and falls back to `data/history.db` locally.

## Key implementation details

### Week anchoring (Mon–Sun UTC)
Analysis windows are anchored to complete Mon 00:00 UTC → Sun 23:59:59 UTC weeks. `run_date` in the DB is always the **Sunday** ending the analyzed window (e.g., data from Apr 27–May 3 → `run_date = 2026-05-03`). `_get_week_windows(n)` in `app.py` computes the n most recently completed Mon-Sun windows relative to now.

Use-case: reviewed Monday morning to catch what happened over the weekend.

### app.py (Flask dashboard)
- Public routes: `/` (game cards), `/game/<app_id>` (detail with charts)
- Admin routes (session-gated): `/admin/add`, `/admin/game/<id>/remove`, `/admin/game/<id>/refresh-votes`, `/admin/game/<id>/reanalyze`
- Auth: single `ADMIN_PASSWORD` env var, Flask session cookie — no user system
- Long-running jobs run in daemon threads; frontend polls `/api/status/<job_id>` every 2s. Job state is in-memory (`_jobs` dict) — resets on restart, which is fine.
- Smart re-add: if a removed game is re-added and runs already exist, runs vote refresh instead of the full Claude backfill

### Admin actions (game detail page)
- **Refresh Steam ratings** — fetches 37 days of Steam votes, updates existing runs. No Claude calls. Aborts if Steam returns 0 reviews (prevents wiping data).
- **Re-run AI analysis (last 4 weeks)** — runs `_run_backfill`: fetches and analyzes the 4 most recent complete Mon-Sun weeks, then calls `delete_runs_except` to remove any old non-aligned rows. Use to migrate to Mon-Sun anchoring or fix bad data.
- **Re-run week** — runs `_run_single_week` for one specific week chosen from a dropdown (last 8 weeks, ✓ marks existing runs). Surgical fix without touching other weeks.
- **Remove game** — removes from `games` table only; run history in `runs` preserved so re-adding skips the Claude backfill.

### Dashboard UI
- **Summary cards:** Current Steam Rating (all-time, live), Last 7-Day Steam Rating (vote ratio → Steam label), Last 7-Day AI Sentiment (`overall_sentiment`), Last Analysis Run showing "Week of [Mon] – [Sun]"
- **Last 7-Day Rating** uses Steam's two-axis model: positive % + count bucket (10–49 / 50–499 / 500+). Yellow warning when < 10 reviews.
- **Rating color scale:** Overwhelmingly Positive → `green-500`, Very Positive → `green-400`, Mostly Positive/Positive → `green-300`, Mixed → `yellow-400`, Mostly Negative → `orange-400`, Negative → `red-400`, Very/Overwhelmingly Negative → `red-500`
- **Sentiment Trend chart:** stacked bars (positive/mixed/negative theme counts) per week. Timeframe toggle: 30d (default) / 60d / 90d / 180d — filters client-side. Empty week slots (no run data) show a gray overlay with a "No analyzed data for this period" pill; their x-axis tick labels are dimmed. The full date range always renders even when data is sparse.
- **Top Review Themes chart:** horizontal bars, sorted by `review_count` desc, top 8 shown. Week selector: 4 buttons for most recent weeks with data + "Older weeks…" dropdown for anything beyond that. Single-week view (not aggregated). Theme labels truncated to 26 chars; tooltip resolves full name before index lookup.
- **AI sentiment palette** (consistent across charts): positive `rgba(74,222,128)`, mixed `rgba(251,146,60)`, negative `rgba(248,113,113)`. 0.75 alpha at rest, 1.0 on hover.
- **0-themes warning:** shown when a run has >0 reviews but 0 themes — flags analysis failures.
- Tailwind CDN (`https://cdn.tailwindcss.com`) + Chart.js v4 — no build step. We previously switched to a pre-generated `static/tailwind.min.css` to fix Edge's "Not Secure" warning, but the warning persisted and the pre-generated approach caused pain with git worktrees (each worktree needs its own `npx tailwindcss` regeneration whenever new utility classes are added). Switched back to CDN. Edge's "Not Secure" is unrelated to the CDN — likely an HTTPS/mixed-content issue with the Railway deployment.

### steam.py
- `fetch_reviews(app_id, window_days=7, end_cutoff_ts=None, start_cutoff_ts=None)` — pass `start_cutoff_ts` + `end_cutoff_ts` for exact Mon-Sun windows (backfill); omit both for "last N days" (CLI agent).
- Cursor-based pagination, `filter=recent` (newest-first). Stops when timestamp falls outside window. Caps at 500 reviews; deduplicates cursors against Steam's repeated-cursor bug.
- Raises `SteamAPIError` for bad App IDs, network failures, timeouts.

### analyze.py
- Forces structured output via Claude tool use (`tool_choice={"type":"tool","name":"submit_analysis"}`). Output is a Python dict directly — no JSON parsing.
- Schema: `review_count`, `overall_sentiment`, `themes[]` (name, description, review_count, sentiment, representative_quotes, confidence), `flagged_spikes[]`
- Reviews truncated to 300 chars before sending. Model: `claude-sonnet-4-6`, `max_tokens=4096`.
- Raises `RuntimeError` on `stop_reason == "max_tokens"` — truncated responses fail loudly.
- Sub-theme splitting: opposing camps split into " — Praised" / " — Criticized" suffixes rather than collapsing to mixed.

### email_sender.py
- Named `email_sender.py` not `email.py` — avoids shadowing Python's stdlib `email` package.
- Inline-CSS HTML email (email client compatibility). Sentiment: 🟢 positive, 🔴 negative, 🟡 mixed.

### trends.py
- **`games` table:** source of truth for tracked games. Separate from `runs` so removing a game doesn't delete history.
- **`runs` table:** one row per `(app_id, run_date)`, upserts on conflict. `run_date` is always a Sunday in the Mon-Sun anchor system.
- `delete_runs_except(app_id, keep_dates)` — deletes all runs not in `keep_dates`; used by `_run_backfill` to clean up old non-aligned rows.
- Spike detection is proportion-based: `theme_count / total_review_count`, ≥50% relative increase. Floor: ≥10 reviews for existing themes, ≥15 for new ones.

### agent.py / schedule.sh
- `--config games.json` sends one consolidated email digest; per-game errors skip-and-continue.
- `run_weekly.sh` wraps the cron invocation; `schedule.sh` manages the cron entry with `--enable`/`--disable`/`--status`.

## Deployment
- Deployed on Railway at `reviews.jonathanpaek.com`
- `DB_PATH` set via Railway env panel → persistent Volume. Not in `.env`.
- Weekly-digest cron service on Railway (currently inactive)

## Git workflow
- Feature branches → PR → merge to main
- Claude Code uses `claude/` prefix for worktree branches
- `.env` gitignored; `.env.example` committed with placeholder values

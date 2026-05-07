"""
Trend storage and week-over-week comparison.
Persists each run to SQLite; flags themes that spike ≥50% as a share of total reviews.
"""

import json
import os
import sqlite3
from datetime import date

DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "history.db"))

SPIKE_THRESHOLD = 0.50  # 50% relative increase in proportion triggers a flag
MIN_REVIEWS = 10        # theme must have ≥10 reviews this week to be considered
NEW_THEME_MIN = 15      # new theme (no prior data) flagged if ≥15 reviews


def init_db() -> None:
    """Create the data directory, games table, and runs table if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id    TEXT    UNIQUE NOT NULL,
                game_name TEXT    NOT NULL,
                added_at  TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id            TEXT    NOT NULL,
                run_date          TEXT    NOT NULL,
                review_count      INTEGER NOT NULL,
                overall_sentiment TEXT    NOT NULL,
                themes_json       TEXT    NOT NULL,
                positive_count    INTEGER NOT NULL DEFAULT 0,
                negative_count    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(app_id, run_date)
            )
        """)
        # Migrate existing DBs that predate the vote count columns
        for col in ("positive_count", "negative_count"):
            try:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # column already exists


def save_run(
    app_id: str,
    analysis: dict,
    run_date: str | None = None,
    positive_count: int = 0,
    negative_count: int = 0,
) -> None:
    """Persist an analysis run. Overwrites if a run already exists for that date."""
    if run_date is None:
        run_date = date.today().isoformat()
    themes = [
        {"name": t["name"], "review_count": t["review_count"], "sentiment": t["sentiment"]}
        for t in analysis.get("themes", [])
    ]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs
                (app_id, run_date, review_count, overall_sentiment, themes_json,
                 positive_count, negative_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(app_id, run_date) DO UPDATE SET
                review_count      = excluded.review_count,
                overall_sentiment = excluded.overall_sentiment,
                themes_json       = excluded.themes_json,
                positive_count    = excluded.positive_count,
                negative_count    = excluded.negative_count
            """,
            (
                app_id,
                run_date,
                analysis.get("review_count", 0),
                analysis.get("overall_sentiment", "unknown"),
                json.dumps(themes),
                positive_count,
                negative_count,
            ),
        )


def add_game(app_id: str, game_name: str) -> None:
    """Add a game to the tracked games table. No-op if already present."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO games (app_id, game_name, added_at) VALUES (?, ?, ?)",
            (app_id, game_name, date.today().isoformat()),
        )


def remove_game(app_id: str) -> None:
    """Remove a game from the tracked list. Leaves runs data intact."""
    with _connect() as conn:
        conn.execute("DELETE FROM games WHERE app_id = ?", (app_id,))


def update_run_vote_counts(app_id: str, run_date: str, positive_count: int, negative_count: int) -> int:
    """Update vote counts on an existing run. Returns number of rows updated."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE runs SET positive_count = ?, negative_count = ?
            WHERE app_id = ? AND run_date = ?
            """,
            (positive_count, negative_count, app_id, run_date),
        )
        return cursor.rowcount


def get_games() -> list[dict]:
    """Return all tracked games ordered by most recently added."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT app_id, game_name, added_at FROM games ORDER BY added_at DESC"
        ).fetchall()
    return [{"app_id": r[0], "game_name": r[1], "added_at": r[2]} for r in rows]


def get_game(app_id: str) -> dict | None:
    """Return a single tracked game, or None if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT app_id, game_name, added_at FROM games WHERE app_id = ?", (app_id,)
        ).fetchone()
    if row is None:
        return None
    return {"app_id": row[0], "game_name": row[1], "added_at": row[2]}


def get_all_runs(app_id: str) -> list[dict]:
    """Return all runs for an app_id sorted oldest-first (for charting)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT run_date, review_count, overall_sentiment, themes_json,
                   positive_count, negative_count
            FROM runs WHERE app_id = ? ORDER BY run_date ASC
            """,
            (app_id,),
        ).fetchall()
    return [
        {
            "run_date": r[0],
            "review_count": r[1],
            "overall_sentiment": r[2],
            "themes": json.loads(r[3]),
            "positive_count": r[4],
            "negative_count": r[5],
        }
        for r in rows
    ]


def load_last_run(app_id: str) -> dict | None:
    """Return the most recent run for this app_id before today, or None."""
    today = date.today().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT run_date, review_count, overall_sentiment, themes_json
            FROM runs
            WHERE app_id = ? AND run_date < ?
            ORDER BY run_date DESC
            LIMIT 1
            """,
            (app_id, today),
        ).fetchone()

    if row is None:
        return None

    return {
        "run_date": row[0],
        "review_count": row[1],
        "overall_sentiment": row[2],
        "themes": json.loads(row[3]),
    }


def compute_trends(current: dict, previous: dict) -> list[dict]:
    """
    Compare current analysis against the previous run.
    Returns trend spike dicts sorted by magnitude (largest first).

    Each spike dict has: theme, type ("spike" | "new_theme"),
    current_pct, previous_pct, relative_change (None for new themes).
    """
    spikes = []

    cur_total = current.get("review_count", 0) or 1
    prev_total = previous.get("review_count", 0) or 1

    prev_map = {t["name"]: t["review_count"] for t in previous.get("themes", [])}

    for theme in current.get("themes", []):
        name = theme["name"]
        cur_count = theme.get("review_count", 0)

        if cur_count < MIN_REVIEWS:
            continue

        cur_pct = cur_count / cur_total

        if name not in prev_map:
            if cur_count >= NEW_THEME_MIN:
                spikes.append({
                    "theme": name,
                    "type": "new_theme",
                    "current_pct": round(cur_pct * 100, 1),
                    "previous_pct": 0.0,
                    "relative_change": None,
                })
            continue

        prev_pct = prev_map[name] / prev_total
        if prev_pct == 0:
            continue

        relative_change = (cur_pct - prev_pct) / prev_pct
        if relative_change >= SPIKE_THRESHOLD:
            spikes.append({
                "theme": name,
                "type": "spike",
                "current_pct": round(cur_pct * 100, 1),
                "previous_pct": round(prev_pct * 100, 1),
                "relative_change": round(relative_change * 100, 1),
            })

    spikes.sort(key=lambda s: s.get("relative_change") or 999, reverse=True)
    return spikes


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


# ── CLI / debug entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspect trend history for an app.")
    parser.add_argument("--app_id", required=True)
    args = parser.parse_args()

    init_db()
    run = load_last_run(args.app_id)
    if run is None:
        print(f"No prior run found for app_id={args.app_id}")
    else:
        print(f"Last run: {run['run_date']}  ({run['review_count']} reviews, {run['overall_sentiment']})")
        for t in run["themes"]:
            print(f"  {t['name']}: {t['review_count']} reviews")

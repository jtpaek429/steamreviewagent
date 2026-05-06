"""
Trend storage and week-over-week comparison.
Persists each run to SQLite; flags themes that spike ≥50% as a share of total reviews.
"""

import json
import os
import sqlite3
from datetime import date

DB_PATH = os.path.join("data", "history.db")

SPIKE_THRESHOLD = 0.50  # 50% relative increase in proportion triggers a flag
MIN_REVIEWS = 10        # theme must have ≥10 reviews this week to be considered
NEW_THEME_MIN = 15      # new theme (no prior data) flagged if ≥15 reviews


def init_db() -> None:
    """Create the data directory and runs table if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id            TEXT    NOT NULL,
                run_date          TEXT    NOT NULL,
                review_count      INTEGER NOT NULL,
                overall_sentiment TEXT    NOT NULL,
                themes_json       TEXT    NOT NULL,
                UNIQUE(app_id, run_date)
            )
        """)


def save_run(app_id: str, analysis: dict) -> None:
    """Persist today's analysis. Overwrites if the agent runs more than once today."""
    themes = [
        {"name": t["name"], "review_count": t["review_count"], "sentiment": t["sentiment"]}
        for t in analysis.get("themes", [])
    ]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (app_id, run_date, review_count, overall_sentiment, themes_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(app_id, run_date) DO UPDATE SET
                review_count      = excluded.review_count,
                overall_sentiment = excluded.overall_sentiment,
                themes_json       = excluded.themes_json
            """,
            (
                app_id,
                date.today().isoformat(),
                analysis.get("review_count", 0),
                analysis.get("overall_sentiment", "unknown"),
                json.dumps(themes),
            ),
        )


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

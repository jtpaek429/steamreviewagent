"""
Flask dashboard for the Steam Review Agent.
Public routes: dashboard, game detail.
Admin routes (password-gated): add game, remove game, refresh vote counts.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

load_dotenv(override=True)

from analyze import analyze_reviews
from steam import SteamAPIError, fetch_game_name, fetch_review_summary, fetch_reviews
from trends import (
    add_game,
    get_all_runs,
    get_game,
    get_games,
    init_db,
    load_last_run,
    remove_game,
    save_run,
    update_run_vote_counts,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

init_db()


# ── In-memory job store ────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _create_job(initial_message: str = "Starting...") -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "message": initial_message}
    return job_id


def _update_job(job_id: str, status: str, message: str) -> None:
    with _jobs_lock:
        _jobs[job_id] = {"status": status, "message": message}


# ── Auth ───────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Background jobs ────────────────────────────────────────────────────────────

def _run_backfill(app_id: str, job_id: str) -> None:
    """Fetches 30 days of reviews, runs Claude analysis week by week, stores results."""
    try:
        _update_job(job_id, "running", "Fetching reviews from Steam (30 days)...")
        reviews = fetch_reviews(app_id, window_days=30)

        if not reviews:
            _update_job(job_id, "complete", "No reviews found in the last 30 days.")
            return

        now_ts = int(datetime.now(timezone.utc).timestamp())
        week_secs = 7 * 24 * 3600
        completed_weeks = 0

        for week_num in range(4):
            end_ts = now_ts - week_num * week_secs
            start_ts = end_ts - week_secs
            bucket = [r for r in reviews if start_ts <= r["timestamp_created"] < end_ts]

            if not bucket:
                continue

            _update_job(job_id, "running",
                        f"Analyzing week {week_num + 1} of 4 ({len(bucket)} reviews)...")
            analysis = analyze_reviews(bucket)
            positive_count = sum(1 for r in bucket if r["voted_up"])
            negative_count = len(bucket) - positive_count
            run_date = datetime.fromtimestamp(end_ts, tz=timezone.utc).date().isoformat()
            save_run(app_id, analysis, run_date=run_date,
                     positive_count=positive_count, negative_count=negative_count)
            completed_weeks += 1

        _update_job(job_id, "complete", f"Done — {completed_weeks} week(s) analyzed.")

    except Exception as e:
        _update_job(job_id, "error", f"Backfill failed: {e}")


def _run_vote_refresh(app_id: str, job_id: str) -> None:
    """
    Fetches 30 days of Steam reviews and updates vote counts on existing runs.
    No Claude calls — cheap and fast.
    """
    try:
        _update_job(job_id, "running", "Fetching reviews from Steam...")
        # Fetch a bit wider than 30 days to cover all weekly windows
        reviews = fetch_reviews(app_id, window_days=37)

        existing_runs = get_all_runs(app_id)
        if not existing_runs:
            _update_job(job_id, "complete", "No existing runs to update.")
            return

        updated = 0
        for run in existing_runs:
            # Treat run_date as the end of the weekly window (inclusive of the full day)
            end_dt = datetime.strptime(run["run_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_ts = int((end_dt + timedelta(days=1)).timestamp())
            start_ts = end_ts - 7 * 24 * 3600

            bucket = [r for r in reviews if start_ts <= r["timestamp_created"] < end_ts]
            positive_count = sum(1 for r in bucket if r["voted_up"])
            negative_count = len(bucket) - positive_count

            if update_run_vote_counts(app_id, run["run_date"], positive_count, negative_count):
                updated += 1

        _update_job(job_id, "complete", f"Updated vote counts for {updated} run(s).")

    except Exception as e:
        _update_job(job_id, "error", f"Refresh failed: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _steam_style_rating(positive: int, negative: int) -> str | None:
    """Map thumbs up/down counts to Steam's review rating labels.

    Mirrors Steam's two-axis model: positive % + review count bucket.
    Buckets: 10–49 / 50–499 / 500+  (fewer than 10 → no label)
    """
    total = positive + negative
    if total == 0:
        return None
    pct = positive / total

    if pct >= 0.95:
        return "Overwhelmingly Positive" if total >= 500 else ("Very Positive" if total >= 50 else "Positive")
    if pct >= 0.80:
        return "Very Positive" if total >= 50 else "Positive"
    if pct >= 0.70:
        return "Mostly Positive"
    if pct >= 0.40:
        return "Mixed"
    if pct >= 0.20:
        return "Mostly Negative"
    # 0–19 %
    if total >= 500:
        return "Overwhelmingly Negative"
    if total >= 50:
        return "Very Negative"
    return "Negative"


def _theme_sentiment_counts(run: dict) -> tuple[int, int, int]:
    """Sum theme review counts by sentiment bucket."""
    pos = sum(t["review_count"] for t in run["themes"] if t["sentiment"] == "positive")
    mix = sum(t["review_count"] for t in run["themes"] if t["sentiment"] == "mixed")
    neg = sum(t["review_count"] for t in run["themes"] if t["sentiment"] == "negative")
    return pos, mix, neg


def _enrich_games(games: list[dict]) -> list[dict]:
    enriched = []
    for g in games:
        runs = get_all_runs(g["app_id"])
        latest = runs[-1] if runs else None
        enriched.append({**g, "latest_run": latest, "run_count": len(runs)})
    return enriched


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    games = _enrich_games(get_games())
    return render_template("index.html", games=games, is_admin=session.get("is_admin"))


@app.route("/game/<app_id>")
def game_detail(app_id: str):
    game = get_game(app_id)
    if not game:
        return "Game not found", 404

    runs = get_all_runs(app_id)
    latest = runs[-1] if runs else None

    try:
        steam_summary = fetch_review_summary(app_id)
    except SteamAPIError:
        steam_summary = None

    recent_rating = None
    rating_low_sample = False
    if latest:
        recent_rating = _steam_style_rating(
            latest["positive_count"], latest["negative_count"]
        )
        rating_low_sample = (latest["positive_count"] + latest["negative_count"]) < 10

    theme_data = [_theme_sentiment_counts(r) for r in runs]
    chart_data = json.dumps({
        "labels": [r["run_date"] for r in runs],
        "sentiments": [r["overall_sentiment"] for r in runs],
        "positive_counts": [r["positive_count"] for r in runs],
        "negative_counts": [r["negative_count"] for r in runs],
        "ai_positive": [t[0] for t in theme_data],
        "ai_mixed": [t[1] for t in theme_data],
        "ai_negative": [t[2] for t in theme_data],
    })

    return render_template(
        "game.html",
        game=game,
        runs=runs,
        latest=latest,
        chart_data=chart_data,
        steam_summary=steam_summary,
        recent_rating=recent_rating,
        rating_low_sample=rating_low_sample,
        is_admin=session.get("is_admin"),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD and ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Incorrect password.")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin/add", methods=["GET", "POST"])
@admin_required
def add_game_view():
    if request.method == "POST":
        app_id = request.form.get("app_id", "").strip()
        if not app_id.isdigit():
            return render_template("add_game.html", error="App ID must be a number.", is_admin=True)

        if get_game(app_id):
            return render_template(
                "add_game.html",
                error=f"App ID {app_id} is already being tracked.",
                is_admin=True,
            )

        try:
            game_name = fetch_game_name(app_id)
        except SteamAPIError as e:
            return render_template("add_game.html", error=str(e), is_admin=True)

        add_game(app_id, game_name)

        # If runs already exist (game was previously tracked), just refresh vote counts
        # instead of re-running the expensive Claude backfill
        existing_runs = get_all_runs(app_id)
        if existing_runs:
            job_id = _create_job("Refreshing vote counts from existing data...")
            thread = threading.Thread(target=_run_vote_refresh, args=(app_id, job_id), daemon=True)
        else:
            job_id = _create_job("Starting backfill...")
            thread = threading.Thread(target=_run_backfill, args=(app_id, job_id), daemon=True)

        thread.start()
        return redirect(url_for("job_status_page", job_id=job_id, game_name=game_name,
                                next=url_for("index")))

    return render_template("add_game.html", is_admin=True)


@app.route("/admin/game/<app_id>/remove", methods=["POST"])
@admin_required
def remove_game_view(app_id: str):
    remove_game(app_id)
    return redirect(url_for("index"))


@app.route("/admin/game/<app_id>/refresh-votes", methods=["POST"])
@admin_required
def refresh_votes_view(app_id: str):
    game = get_game(app_id)
    if not game:
        return "Game not found", 404
    job_id = _create_job("Starting vote count refresh...")
    thread = threading.Thread(target=_run_vote_refresh, args=(app_id, job_id), daemon=True)
    thread.start()
    return redirect(url_for("job_status_page", job_id=job_id,
                            game_name=game["game_name"],
                            next=url_for("game_detail", app_id=app_id)))


@app.route("/admin/status/<job_id>")
@admin_required
def job_status_page(job_id: str):
    game_name = request.args.get("game_name", "")
    next_url = request.args.get("next", url_for("index"))
    return render_template("backfill_status.html", job_id=job_id,
                           game_name=game_name, next_url=next_url, is_admin=True)


@app.route("/api/status/<job_id>")
@admin_required
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found", "message": "Job not found."}), 404
    return jsonify(job)


if __name__ == "__main__":
    app.run(debug=True)

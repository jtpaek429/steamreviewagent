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
    compute_trends,
    delete_runs_except,
    get_all_runs,
    get_game,
    get_games,
    get_setting,
    init_db,
    load_last_run,
    remove_game,
    save_run,
    set_digest_flag,
    set_setting,
    update_run_vote_counts,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

init_db()


@app.context_processor
def inject_globals():
    is_admin = session.get("is_admin", False)
    return {
        "digest_enabled": get_setting("digest_schedule_enabled", "1") == "1" if is_admin else False,
    }


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


# ── Week window helpers ────────────────────────────────────────────────────────

def _get_week_windows(n: int = 4) -> list[tuple[int, int, str]]:
    """
    Return the n most recently completed Mon–Sun UTC weeks as
    (start_ts, end_ts_exclusive, sunday_date_iso).

    start_ts  = Monday 00:00 UTC
    end_ts    = following Monday 00:00 UTC  (exclusive upper bound)
    sunday_date = ISO date of the Sunday that ends the window
    """
    now = datetime.now(timezone.utc)
    days_since_monday = now.weekday()  # 0=Mon … 6=Sun
    current_week_monday = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    weeks = []
    for i in range(n):
        week_end_monday = current_week_monday - timedelta(weeks=i)      # exclusive end
        week_start_monday = week_end_monday - timedelta(weeks=1)
        sunday_date = (week_end_monday - timedelta(days=1)).date().isoformat()
        weeks.append((
            int(week_start_monday.timestamp()),
            int(week_end_monday.timestamp()),
            sunday_date,
        ))
    return weeks


# ── Background jobs ────────────────────────────────────────────────────────────

def _run_backfill(app_id: str, job_id: str) -> None:
    """Fetches the 4 most recent complete Mon–Sun weeks, runs Claude per week, then
    deletes any old non-aligned runs so the chart stays clean."""
    weeks = _get_week_windows(4)
    completed_weeks = 0
    saved_dates: list[str] = []

    for week_num, (start_ts, end_ts, sunday_date) in enumerate(weeks):
        try:
            _update_job(job_id, "running",
                        f"Fetching week {week_num + 1} of 4 from Steam...")
            reviews = fetch_reviews(app_id, start_cutoff_ts=start_ts, end_cutoff_ts=end_ts)
        except SteamAPIError as e:
            _update_job(job_id, "error", f"Steam fetch failed on week {week_num + 1}: {e}")
            return

        if not reviews:
            continue

        try:
            _update_job(job_id, "running",
                        f"Analyzing week {week_num + 1} of 4 ({len(reviews)} reviews)...")
            analysis = analyze_reviews(reviews)
        except Exception as e:
            _update_job(job_id, "error", f"Analysis failed on week {week_num + 1}: {e}")
            return

        positive_count = sum(1 for r in reviews if r["voted_up"])
        negative_count = len(reviews) - positive_count
        save_run(app_id, analysis, run_date=sunday_date,
                 positive_count=positive_count, negative_count=negative_count)
        saved_dates.append(sunday_date)
        completed_weeks += 1

    # Remove any runs that don't align to the canonical Mon–Sun windows
    if saved_dates:
        deleted = delete_runs_except(app_id, saved_dates)
        suffix = f" ({deleted} old run(s) removed)" if deleted else ""
    else:
        suffix = ""

    _update_job(job_id, "complete", f"Done — {completed_weeks} week(s) analyzed{suffix}.")


def _run_single_week(app_id: str, sunday_date: str, job_id: str) -> None:
    """Re-analyze one specific Mon–Sun week identified by its Sunday date (YYYY-MM-DD)."""
    sunday = datetime.strptime(sunday_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    week_start = sunday - timedelta(days=6)   # Monday 00:00 UTC
    start_ts = int(week_start.timestamp())
    end_ts = int((sunday + timedelta(days=1)).timestamp())  # following Monday 00:00 UTC

    try:
        _update_job(job_id, "running", f"Fetching reviews for week of {sunday_date}...")
        reviews = fetch_reviews(app_id, start_cutoff_ts=start_ts, end_cutoff_ts=end_ts)
    except SteamAPIError as e:
        _update_job(job_id, "error", f"Steam fetch failed: {e}")
        return

    if not reviews:
        _update_job(job_id, "complete", "No reviews found in that week window.")
        return

    try:
        _update_job(job_id, "running", f"Analyzing {len(reviews)} reviews...")
        analysis = analyze_reviews(reviews)
    except Exception as e:
        _update_job(job_id, "error", f"Analysis failed: {e}")
        return

    positive_count = sum(1 for r in reviews if r["voted_up"])
    negative_count = len(reviews) - positive_count
    save_run(app_id, analysis, run_date=sunday_date,
             positive_count=positive_count, negative_count=negative_count)
    _update_job(job_id, "complete",
                f"Done — {len(reviews)} reviews analyzed for week of {sunday_date}.")


def _run_vote_refresh(app_id: str, job_id: str) -> None:
    """
    Fetches 30 days of Steam reviews and updates vote counts on existing runs.
    No Claude calls — cheap and fast.
    """
    try:
        _update_job(job_id, "running", "Fetching reviews from Steam...")
        # Fetch a bit wider than 30 days to cover all weekly windows
        reviews = fetch_reviews(app_id, window_days=37)

        if not reviews:
            _update_job(job_id, "error",
                        "Steam returned 0 reviews — aborting to avoid wiping existing vote data.")
            return

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


def _run_send_email(app_id: str, to_email: str, job_id: str) -> None:
    """Send an email digest for the most recent run. No new Claude call."""
    from email_sender import send_digest

    try:
        game = get_game(app_id)
        if not game:
            _update_job(job_id, "error", "Game not found.")
            return

        _update_job(job_id, "running", "Loading most recent analysis...")
        runs = get_all_runs(app_id)
        if not runs:
            _update_job(job_id, "error", "No analysis runs found — run AI analysis first.")
            return

        latest = runs[-1]
        # Reconstruct a minimal analysis dict from the stored run
        analysis = {
            "review_count": latest["review_count"],
            "overall_sentiment": latest["overall_sentiment"],
            "themes": latest["themes"],
            "flagged_spikes": [],
        }

        _update_job(job_id, "running", f"Sending email to {to_email}...")
        send_digest(analysis, app_id, game["game_name"], to_email=to_email)
        _update_job(job_id, "complete", f"Email sent to {to_email}.")

    except Exception as e:
        _update_job(job_id, "error", f"Email send failed: {e}")


def _run_digest(job_id: str) -> None:
    """Run the full pipeline for all digest-enabled games and send consolidated email."""
    from email_sender import send_multi_digest

    try:
        db_games = [g for g in get_games() if g["include_in_digest"]]
        if not db_games:
            _update_job(job_id, "error", "No games have digest enabled.")
            return

        results = []
        for i, game in enumerate(db_games, 1):
            app_id = str(game["app_id"])
            game_name = game["game_name"]
            _update_job(job_id, "running", f"Processing {game_name} ({i}/{len(db_games)})...")

            try:
                reviews = fetch_reviews(app_id)
            except SteamAPIError as e:
                results.append({"app_id": app_id, "game_name": game_name,
                                 "analysis": None, "trend_spikes": [], "error": str(e)})
                continue

            try:
                analysis = analyze_reviews(reviews)
            except (ValueError, RuntimeError) as e:
                results.append({"app_id": app_id, "game_name": game_name,
                                 "analysis": None, "trend_spikes": [], "error": str(e)})
                continue

            positive_count = sum(1 for r in reviews if r["voted_up"])
            negative_count = len(reviews) - positive_count
            previous_run = load_last_run(app_id)
            trend_spikes = compute_trends(analysis, previous_run) if previous_run else []
            save_run(app_id, analysis, positive_count=positive_count, negative_count=negative_count)
            results.append({"app_id": app_id, "game_name": game_name,
                            "analysis": analysis, "trend_spikes": trend_spikes, "error": None})

        n_ok = sum(1 for r in results if r["analysis"] is not None)
        _update_job(job_id, "running", f"Sending digest email ({n_ok}/{len(db_games)} succeeded)...")
        send_multi_digest(results)
        _update_job(job_id, "complete", f"Digest sent — {n_ok}/{len(db_games)} game(s) included.")

    except Exception as e:
        _update_job(job_id, "error", f"Digest failed: {e}")


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
    digest_enabled = get_setting("digest_schedule_enabled", "1") == "1"
    return render_template("index.html", games=games, is_admin=session.get("is_admin"),
                           digest_enabled=digest_enabled)


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

    latest_with_themes = next((r for r in reversed(runs) if r.get("themes")), None)

    latest_week_label = None
    if latest:
        sunday = datetime.strptime(latest["run_date"], "%Y-%m-%d")
        monday = sunday - timedelta(days=6)
        latest_week_label = (
            f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}"
        )

    existing_run_dates = {r["run_date"] for r in runs}
    weeks_for_admin = []
    for start_ts, end_ts, sunday_date in _get_week_windows(8):
        monday_date = (
            datetime.strptime(sunday_date, "%Y-%m-%d") - timedelta(days=6)
        ).strftime("%Y-%m-%d")
        weeks_for_admin.append({
            "sunday_date": sunday_date,
            "label": f"{monday_date} – {sunday_date}",
            "has_run": sunday_date in existing_run_dates,
        })

    return render_template(
        "game.html",
        game=game,
        runs=runs,
        latest=latest,
        latest_with_themes=latest_with_themes,
        chart_data=chart_data,
        steam_summary=steam_summary,
        recent_rating=recent_rating,
        rating_low_sample=rating_low_sample,
        latest_week_label=latest_week_label,
        weeks_for_admin=weeks_for_admin,
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


@app.route("/admin/game/<app_id>/reanalyze", methods=["POST"])
@admin_required
def reanalyze_game_view(app_id: str):
    game = get_game(app_id)
    if not game:
        return "Game not found", 404
    week_end_date = request.form.get("week_end_date", "").strip()
    if week_end_date:
        job_id = _create_job(f"Starting AI analysis for week of {week_end_date}...")
        thread = threading.Thread(
            target=_run_single_week, args=(app_id, week_end_date, job_id), daemon=True
        )
    else:
        job_id = _create_job("Starting AI analysis (last 4 weeks)...")
        thread = threading.Thread(target=_run_backfill, args=(app_id, job_id), daemon=True)
    thread.start()
    return redirect(url_for("job_status_page", job_id=job_id,
                            game_name=game["game_name"],
                            next=url_for("game_detail", app_id=app_id)))


@app.route("/admin/digest/toggle-schedule", methods=["POST"])
@admin_required
def toggle_schedule_view():
    current = get_setting("digest_schedule_enabled", "1")
    new_value = "0" if current == "1" else "1"
    set_setting("digest_schedule_enabled", new_value)
    return jsonify({"enabled": new_value == "1"})


@app.route("/admin/game/<app_id>/toggle-digest", methods=["POST"])
@admin_required
def toggle_digest_view(app_id: str):
    game = get_game(app_id)
    if not game:
        return jsonify({"error": "Game not found"}), 404
    new_value = not game["include_in_digest"]
    set_digest_flag(app_id, new_value)
    return jsonify({"include_in_digest": new_value})


@app.route("/admin/game/<app_id>/send-email", methods=["POST"])
@admin_required
def send_email_view(app_id: str):
    game = get_game(app_id)
    if not game:
        return "Game not found", 404
    to_email = request.form.get("to_email", "").strip() or os.environ.get("TO_EMAIL", "")
    if not to_email:
        return "No recipient email address provided.", 400
    job_id = _create_job(f"Preparing email for {game['game_name']}...")
    thread = threading.Thread(target=_run_send_email, args=(app_id, to_email, job_id), daemon=True)
    thread.start()
    return redirect(url_for("job_status_page", job_id=job_id,
                            game_name=game["game_name"],
                            next=url_for("game_detail", app_id=app_id)))


@app.route("/admin/send-digest", methods=["POST"])
@admin_required
def send_digest_view():
    job_id = _create_job("Starting weekly digest...")
    thread = threading.Thread(target=_run_digest, args=(job_id,), daemon=True)
    thread.start()
    return redirect(url_for("job_status_page", job_id=job_id,
                            game_name="Weekly Digest",
                            next=url_for("index")))


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

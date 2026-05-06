"""
Entry point — orchestrates steam.py → analyze.py → email_sender.py.

Single-game:  python agent.py --app_id 1245620 --game_name "Elden Ring"
Multi-game:   python agent.py --config games.json
"""

import argparse
import json

from dotenv import load_dotenv

load_dotenv(override=True)

from steam import SteamAPIError, fetch_game_name, fetch_reviews
from analyze import analyze_reviews
from email_sender import send_digest, send_multi_digest
from trends import compute_trends, init_db, load_last_run, save_run


def resolve_game_name(app_id: str, game_name: str) -> str:
    """Return game_name if provided, otherwise look it up from the Steam store API."""
    if game_name:
        return game_name
    try:
        name = fetch_game_name(app_id)
        print(f"  [lookup]  App ID {app_id} → {name}")
        return name
    except SteamAPIError as e:
        print(f"  [lookup]  App ID {app_id} name lookup failed ({e}); using App ID as label.")
        return ""


def run_one(app_id: str, game_name: str) -> dict:
    """
    Run the full pipeline for a single game.
    Returns a result dict compatible with send_multi_digest.
    Never raises — errors are captured into the result.
    """
    label = game_name or f"App ID {app_id}"

    print(f"  [fetch]   {label}…")
    try:
        reviews = fetch_reviews(app_id)
    except SteamAPIError as e:
        print(f"  [ERROR]   {label} — Steam fetch failed: {e}")
        return {"app_id": app_id, "game_name": game_name,
                "analysis": None, "trend_spikes": [], "error": f"Steam fetch failed: {e}"}
    print(f"            → {len(reviews)} review(s)")

    print(f"  [analyze] {label}…")
    try:
        analysis = analyze_reviews(reviews)
    except (ValueError, RuntimeError) as e:
        print(f"  [ERROR]   {label} — Analysis failed: {e}")
        return {"app_id": app_id, "game_name": game_name,
                "analysis": None, "trend_spikes": [], "error": f"Analysis failed: {e}"}
    print(f"            → {len(analysis.get('themes', []))} theme(s)")

    previous_run = load_last_run(app_id)
    trend_spikes = compute_trends(analysis, previous_run) if previous_run else []
    save_run(app_id, analysis)

    if previous_run:
        print(f"            → {len(trend_spikes)} trend spike(s) vs {previous_run['run_date']}")
    else:
        print(f"            → No prior run; trend baseline saved.")

    return {"app_id": app_id, "game_name": game_name,
            "analysis": analysis, "trend_spikes": trend_spikes, "error": None}


def main():
    parser = argparse.ArgumentParser(description="Steam Review Agent")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--app_id", help="Steam App ID (single-game mode)")
    group.add_argument("--config", help="Path to JSON config file (multi-game mode)")
    parser.add_argument("--game_name", default="", help="Game name (single-game mode only)")
    args = parser.parse_args()

    init_db()

    # ── Multi-game mode ────────────────────────────────────────────────────────
    if args.config:
        try:
            with open(args.config) as f:
                games = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Failed to load config file '{args.config}': {e}")
            raise SystemExit(1)

        if not isinstance(games, list) or not games:
            print("Config file must be a non-empty JSON array of {app_id, game_name} objects.")
            raise SystemExit(1)

        print(f"[multi-game] Running pipeline for {len(games)} game(s)…\n")
        results = []
        for game in games:
            app_id = str(game.get("app_id", "")).strip()
            game_name = game.get("game_name", "").strip()
            if not app_id:
                print(f"  [SKIP] Entry missing app_id: {game}")
                continue
            game_name = resolve_game_name(app_id, game_name)
            results.append(run_one(app_id, game_name))
            print()

        if not results:
            print("No games to process.")
            raise SystemExit(1)

        n_ok = sum(1 for r in results if r["analysis"] is not None)
        n_fail = len(results) - n_ok
        print(f"Pipeline complete: {n_ok} succeeded, {n_fail} failed.\n")

        print("[email] Sending consolidated digest…")
        try:
            send_multi_digest(results)
        except (ValueError, RuntimeError) as e:
            print(f"Email error: {e}")
            raise SystemExit(1)

    # ── Single-game mode ───────────────────────────────────────────────────────
    else:
        args.game_name = resolve_game_name(args.app_id, args.game_name)
        print(f"[1/3] Fetching Steam reviews for App ID {args.app_id}…")
        try:
            reviews = fetch_reviews(args.app_id)
        except SteamAPIError as e:
            print(f"Steam error: {e}")
            raise SystemExit(1)
        print(f"      → {len(reviews)} review(s) collected.\n")

        print("[2/3] Analyzing reviews with Claude…")
        try:
            analysis = analyze_reviews(reviews)
        except (ValueError, RuntimeError) as e:
            print(f"Analysis error: {e}")
            raise SystemExit(1)
        print(f"      → {len(analysis.get('themes', []))} theme(s) identified.\n")

        previous_run = load_last_run(args.app_id)
        trend_spikes = compute_trends(analysis, previous_run) if previous_run else []
        save_run(args.app_id, analysis)

        if previous_run:
            print(f"      → Compared against run from {previous_run['run_date']}; "
                  f"{len(trend_spikes)} trend spike(s) detected.\n")
        else:
            print("      → No prior run found; trend baseline saved for next week.\n")

        print("[3/3] Sending email digest…")
        try:
            send_digest(analysis, args.app_id, args.game_name, trend_spikes)
        except (ValueError, RuntimeError) as e:
            print(f"Email error: {e}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()

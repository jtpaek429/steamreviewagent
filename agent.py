"""
Entry point — orchestrates steam.py → analyze.py → email_sender.py.
Usage: python agent.py --app_id 1245620 [--game_name "Elden Ring"]
"""

import argparse

from dotenv import load_dotenv

load_dotenv(override=True)

from steam import SteamAPIError, fetch_reviews
from analyze import analyze_reviews
from email_sender import send_digest
from trends import compute_trends, init_db, load_last_run, save_run


def main():
    parser = argparse.ArgumentParser(description="Steam Review Agent")
    parser.add_argument("--app_id", required=True, help="Steam App ID")
    parser.add_argument("--game_name", default="", help="Human-readable game name")
    args = parser.parse_args()

    init_db()

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

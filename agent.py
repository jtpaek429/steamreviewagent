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


def main():
    parser = argparse.ArgumentParser(description="Steam Review Agent")
    parser.add_argument("--app_id", required=True, help="Steam App ID")
    parser.add_argument("--game_name", default="", help="Human-readable game name")
    args = parser.parse_args()

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

    print("[3/3] Sending email digest…")
    try:
        send_digest(analysis, args.app_id, args.game_name)
    except (ValueError, RuntimeError) as e:
        print(f"Email error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""
Entry point — orchestrates steam.py → analyze.py → email.py.
Usage: python agent.py --app_id 1245620
"""

import argparse

from dotenv import load_dotenv

load_dotenv(override=True)

from steam import SteamAPIError, fetch_reviews

# Phase 2 + 3 imports added when implemented
# from analyze import analyze_reviews
# from email_sender import send_digest  # Phase 3


def main():
    parser = argparse.ArgumentParser(description="Steam Review Agent")
    parser.add_argument("--app_id", required=True, help="Steam App ID")
    args = parser.parse_args()

    # Phase 1
    print(f"[1/3] Fetching Steam reviews for App ID {args.app_id}…")
    try:
        reviews = fetch_reviews(args.app_id)
    except SteamAPIError as e:
        print(f"Steam error: {e}")
        raise SystemExit(1)
    print(f"      → {len(reviews)} review(s) collected.\n")

    # Phase 2 — placeholder
    print("[2/3] Analyzing reviews… (not yet implemented)")

    # Phase 3 — placeholder
    print("[3/3] Sending email digest… (not yet implemented)")


if __name__ == "__main__":
    main()

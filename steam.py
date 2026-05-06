"""
Fetches Steam reviews for a given App ID from the last 7 days.
Uses the public Steam Reviews API — no auth required.
If more than 500 reviews are found in the window, 500 are sampled randomly.
"""

import random
import time
from datetime import datetime, timezone, timedelta

import requests

STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
MAX_REVIEWS = 500
WINDOW_DAYS = 7
PAGE_SIZE = 100  # Steam's max per request


class SteamAPIError(Exception):
    pass


def fetch_reviews(app_id: str) -> list[dict]:
    """
    Return up to MAX_REVIEWS review dicts from the last WINDOW_DAYS days.
    Each dict has keys: review (str), voted_up (bool), timestamp_created (int).

    Raises SteamAPIError on bad App ID, network failure, or Steam error response.
    """
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).timestamp())

    collected: list[dict] = []
    cursor = "*"
    seen_cursors: set[str] = set()

    while True:
        params = {
            "json": 1,
            "language": "english",
            "review_type": "all",
            "purchase_type": "all",
            "num_per_page": PAGE_SIZE,
            "cursor": cursor,
            "filter": "recent",          # sorted newest-first
        }

        try:
            response = requests.get(
                STEAM_REVIEWS_URL.format(app_id=app_id),
                params=params,
                timeout=15,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise SteamAPIError(f"Network error fetching reviews: {e}") from e
        except requests.exceptions.Timeout:
            raise SteamAPIError("Request to Steam API timed out.")
        except requests.exceptions.HTTPError as e:
            raise SteamAPIError(f"HTTP error from Steam API: {e}") from e

        data = response.json()

        # Steam returns success: 1 for valid app IDs
        if data.get("success") != 1:
            raise SteamAPIError(
                f"Steam API returned an error for App ID '{app_id}'. "
                "Check that the App ID is correct."
            )

        reviews = data.get("reviews", [])

        if not reviews:
            break  # No more reviews

        # Pagination guard — Steam sometimes repeats cursors
        new_cursor = data.get("cursor", "")
        if new_cursor in seen_cursors:
            break
        seen_cursors.add(new_cursor)

        for review in reviews:
            ts = review.get("timestamp_created", 0)
            if ts < cutoff_ts:
                # Reviews are newest-first; once we're past the window we're done
                return _maybe_sample(collected)
            collected.append({
                "review": review.get("review", "").strip(),
                "voted_up": review.get("voted_up", False),
                "timestamp_created": ts,
            })

        # Stop early if we've already collected more than enough to sample from
        if len(collected) >= MAX_REVIEWS * 2:
            return _maybe_sample(collected)

        cursor = new_cursor
        if not cursor:
            break

        # Be polite to Steam's servers
        time.sleep(0.5)

    return _maybe_sample(collected)


def fetch_game_name(app_id: str) -> str:
    """
    Return the game's display name from the Steam store API.
    Raises SteamAPIError if the lookup fails or the App ID is unrecognised.
    """
    try:
        response = requests.get(
            STEAM_DETAILS_URL,
            params={"appids": app_id, "filters": "basic"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise SteamAPIError(f"Network error fetching game name: {e}") from e
    except requests.exceptions.Timeout:
        raise SteamAPIError("Request to Steam store API timed out.")
    except requests.exceptions.HTTPError as e:
        raise SteamAPIError(f"HTTP error from Steam store API: {e}") from e

    payload = response.json()
    entry = payload.get(str(app_id), {})
    if not entry.get("success"):
        raise SteamAPIError(
            f"Steam store API returned no data for App ID '{app_id}'. "
            "Check that the App ID is correct."
        )

    name = entry.get("data", {}).get("name", "").strip()
    if not name:
        raise SteamAPIError(f"Steam store API returned an empty name for App ID '{app_id}'.")
    return name


def _maybe_sample(reviews: list[dict]) -> list[dict]:
    """Return up to MAX_REVIEWS reviews, sampled randomly if over the cap."""
    if len(reviews) > MAX_REVIEWS:
        return random.sample(reviews, MAX_REVIEWS)
    return reviews


# ── CLI / debug entrypoint ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch Steam reviews for a game.")
    parser.add_argument("--app_id", required=True, help="Steam App ID (e.g. 1245620)")
    args = parser.parse_args()

    print(f"Fetching reviews for App ID: {args.app_id} (last {WINDOW_DAYS} days)…\n")

    try:
        reviews = fetch_reviews(args.app_id)
    except SteamAPIError as e:
        print(f"Error: {e}")
        raise SystemExit(1)

    if not reviews:
        print("No reviews found in the last 7 days.")
        raise SystemExit(0)

    print(f"Retrieved {len(reviews)} review(s):\n{'─' * 60}")
    for i, r in enumerate(reviews, 1):
        ts = datetime.fromtimestamp(r["timestamp_created"], tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        sentiment = "👍" if r["voted_up"] else "👎"
        # Truncate long reviews for terminal readability
        text = r["review"][:300] + ("…" if len(r["review"]) > 300 else "")
        print(f"[{i:>3}] {sentiment} {ts}  {text}")
        print()

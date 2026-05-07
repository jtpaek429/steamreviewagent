"""
Phase 2 — analyze reviews using Claude API with tool use.
Returns structured JSON: themes, per-theme sentiment, review count, flagged spikes.
"""

import os

import anthropic

MODEL = "claude-sonnet-4-6"
MAX_REVIEW_CHARS = 300  # truncate individual reviews before sending to Claude


# ── Output schema (enforced via tool use) ─────────────────────────────────────

ANALYSIS_TOOL = {
    "name": "submit_analysis",
    "description": "Submit the structured analysis of Steam reviews.",
    "input_schema": {
        "type": "object",
        "properties": {
            "review_count": {
                "type": "integer",
                "description": "Total number of reviews analyzed.",
            },
            "overall_sentiment": {
                "type": "string",
                "enum": ["positive", "negative", "mixed"],
                "description": "Overall sentiment across all reviews.",
            },
            "themes": {
                "type": "array",
                "description": "Distinct themes found across reviews, ordered by frequency.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short label for the theme (e.g. 'Difficulty & Challenge — Praised', 'Performance Issues'). When a topic has clearly opposing camps, use ' — Praised' / ' — Criticized' suffixes and emit two separate themes instead of one mixed theme.",
                        },
                        "description": {
                            "type": "string",
                            "description": "One sentence summarising what reviewers say about this theme.",
                        },
                        "review_count": {
                            "type": "integer",
                            "description": "Approximate number of reviews touching this theme.",
                        },
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "mixed"],
                            "description": "Dominant sentiment for this theme.",
                        },
                        "representative_quotes": {
                            "type": "array",
                            "description": "Up to 2 short verbatim quotes illustrating this theme.",
                            "items": {"type": "string"},
                            "maxItems": 2,
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence score (0.0–1.0) that this is a genuine, distinct theme vs. noise.",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["name", "description", "review_count", "sentiment", "representative_quotes", "confidence"],
                },
            },
            "flagged_spikes": {
                "type": "array",
                "description": "Notable spikes or anomalies worth flagging (e.g. sudden surge in crash reports, review bomb, DLC backlash). Empty list if nothing stands out.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["label", "detail"],
                },
            },
        },
        "required": ["review_count", "overall_sentiment", "themes", "flagged_spikes"],
    },
}


# ── Main function ──────────────────────────────────────────────────────────────

def analyze_reviews(reviews: list[dict]) -> dict:
    """
    Send reviews to Claude and return a structured analysis dict.
    Raises ValueError if no reviews are passed.
    Raises anthropic.APIError on API failures.
    """
    if not reviews:
        raise ValueError("No reviews to analyze.")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    review_block = _format_reviews(reviews)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        tools=[ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_analysis"},  # force tool call
        system=(
            "You are a game analytics assistant. "
            "Analyze the provided Steam reviews and identify recurring themes, "
            "sentiment, and any notable spikes or anomalies. "
            "Be concise and data-driven. "
            "When a topic has clearly opposing camps — some reviewers love it, others hate it — "
            "split it into two separate themes using ' — Praised' and ' — Criticized' suffixes "
            "(e.g. 'Difficulty & Challenge — Praised' and 'Difficulty & Challenge — Criticized') "
            "rather than labelling the combined theme as mixed."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here are {len(reviews)} Steam reviews from the past 7 days.\n\n"
                    f"{review_block}\n\n"
                    "Analyze these reviews and call submit_analysis with your findings."
                ),
            }
        ],
    )

    # Extract the tool call result — guaranteed present because tool_choice forces it
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis":
            return block.input

    # Should never reach here given tool_choice="tool", but be explicit
    raise RuntimeError("Claude did not return a tool call — unexpected response format.")


def _format_reviews(reviews: list[dict]) -> str:
    """Format review dicts into a numbered plaintext block for the prompt."""
    lines = []
    for i, r in enumerate(reviews, 1):
        sentiment = "thumbs_up" if r["voted_up"] else "thumbs_down"
        text = r["review"][:MAX_REVIEW_CHARS]
        if len(r["review"]) > MAX_REVIEW_CHARS:
            text += "…"
        lines.append(f"[{i}] ({sentiment}) {text}")
    return "\n".join(lines)


# ── CLI / debug entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    from dotenv import load_dotenv

    load_dotenv(override=True)

    from steam import SteamAPIError, fetch_reviews

    parser = argparse.ArgumentParser(description="Analyze Steam reviews with Claude.")
    parser.add_argument("--app_id", required=True, help="Steam App ID (e.g. 1245620)")
    args = parser.parse_args()

    print(f"Fetching reviews for App ID {args.app_id}…")
    try:
        reviews = fetch_reviews(args.app_id)
    except SteamAPIError as e:
        print(f"Steam error: {e}")
        raise SystemExit(1)

    print(f"Fetched {len(reviews)} reviews. Sending to Claude…\n")

    try:
        analysis = analyze_reviews(reviews)
    except (ValueError, RuntimeError) as e:
        print(f"Analysis error: {e}")
        raise SystemExit(1)

    print(json.dumps(analysis, indent=2))

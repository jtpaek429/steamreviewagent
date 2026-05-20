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
            "ambiguous_theme_names": {
                "type": "array",
                "description": "Names of themes where sentiment or scope is still unclear and a focused deeper pass would help. Empty list if all themes are well-understood.",
                "items": {"type": "string"},
            },
        },
        "required": ["review_count", "overall_sentiment", "themes", "flagged_spikes", "ambiguous_theme_names"],
    },
}


FOCUSED_ANALYSIS_TOOL = {
    "name": "submit_focused_analysis",
    "description": "Submit updated analysis for a specific set of ambiguous themes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "themes": {
                "type": "array",
                "description": "Updated theme objects for the requested themes only. Use the same schema as the primary analysis.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "review_count": {"type": "integer"},
                        "sentiment": {"type": "string", "enum": ["positive", "negative", "mixed"]},
                        "representative_quotes": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": ["name", "description", "review_count", "sentiment", "representative_quotes", "confidence"],
                },
            },
            "ambiguous_theme_names": {
                "type": "array",
                "description": "Any of the requested themes still ambiguous after this deeper pass. Empty list if all are now resolved.",
                "items": {"type": "string"},
            },
        },
        "required": ["themes", "ambiguous_theme_names"],
    },
}


SPIKE_VALIDATION_TOOL = {
    "name": "submit_spike_validation",
    "description": "Decide which theme changes constitute genuine notable spikes worth surfacing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "flagged_spikes": {
                "type": "array",
                "description": "Spikes you consider genuinely notable and worth surfacing in a report.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short label for the spike (e.g. 'Crash Reports — Spike')."},
                        "detail": {"type": "string", "description": "One sentence explaining why this is notable (statistical observation)."},
                        "hypothesis": {"type": "string", "description": "1-2 sentences explaining WHAT specifically appears to be driving this spike based on the theme description and review content — the concrete issue, event, or change players are reacting to. Be specific, not generic."},
                        "theme_name": {"type": "string", "description": "Exact theme name this spike relates to."},
                    },
                    "required": ["label", "detail", "hypothesis", "theme_name"],
                },
            },
            "dismissed_spikes": {
                "type": "array",
                "description": "Candidate themes you considered but dismissed as not notable enough.",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme_name": {"type": "string"},
                        "reason": {"type": "string", "description": "One sentence explaining why this was dismissed (e.g. 'volume too low', 'consistent with prior weeks')."},
                    },
                    "required": ["theme_name", "reason"],
                },
            },
        },
        "required": ["flagged_spikes", "dismissed_spikes"],
    },
}


# ── Core API helpers ───────────────────────────────────────────────────────────

def _run_analysis_pass(reviews: list[dict]) -> dict:
    """Single Claude analysis call. Returns structured dict including agentic fields."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    review_block = _format_reviews(reviews)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=[ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_analysis"},
        system=(
            "You are a game analytics assistant. "
            "Analyze the provided Steam reviews and identify recurring themes, "
            "sentiment, and any notable spikes or anomalies. "
            "Be concise and data-driven. "
            "When a topic has clearly opposing camps — some reviewers love it, others hate it — "
            "split it into two separate themes using ' — Praised' and ' — Criticized' suffixes "
            "(e.g. 'Difficulty & Challenge — Praised' and 'Difficulty & Challenge — Criticized') "
            "rather than labelling the combined theme as mixed. "
            "Set confidence_sufficient=false only if the sample is genuinely too small or contradictory "
            "to trust the themes. Set ambiguous_theme_names to themes where a focused re-read would "
            "meaningfully improve confidence."
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
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis":
            if response.stop_reason == "max_tokens":
                raise RuntimeError(
                    "Claude's response was truncated (max_tokens reached). "
                    "The analysis could not be completed — increase max_tokens."
                )
            return block.input
    raise RuntimeError("Claude did not return a tool call — unexpected response format.")


def _run_focused_pass(reviews: list[dict], ambiguous_theme_names: list[str], current_themes: list[dict]) -> dict:
    """Targeted re-analysis of specific ambiguous themes across all fetched reviews."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    review_block = _format_reviews(reviews)
    theme_list = "\n".join(f"- {name}" for name in ambiguous_theme_names)
    prior_summaries = "\n".join(
        f"- {t['name']}: {t['description']} (current confidence: {t.get('confidence', '?')})"
        for t in current_themes if t["name"] in ambiguous_theme_names
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        tools=[FOCUSED_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_focused_analysis"},
        system=(
            "You are a game analytics assistant performing a focused re-analysis. "
            "A prior pass flagged certain themes as ambiguous. "
            "Re-read the reviews specifically looking for evidence about those themes and "
            "return updated, higher-confidence theme objects for them only."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Prior analysis flagged these themes as ambiguous:\n{theme_list}\n\n"
                    f"Prior descriptions:\n{prior_summaries}\n\n"
                    f"Here are all {len(reviews)} reviews:\n\n{review_block}\n\n"
                    "Re-analyze specifically these themes and call submit_focused_analysis."
                ),
            }
        ],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_focused_analysis":
            if response.stop_reason == "max_tokens":
                raise RuntimeError("Focused analysis pass was truncated (max_tokens reached).")
            return block.input
    raise RuntimeError("Claude did not return a tool call in focused pass.")


def _merge_themes(current_themes: list[dict], focused_themes: list[dict]) -> list[dict]:
    """Replace ambiguous themes with focused-pass versions; keep all others."""
    focused_by_name = {t["name"]: t for t in focused_themes}
    result = []
    for t in current_themes:
        if t["name"] in focused_by_name:
            result.append({**focused_by_name[t["name"]], "deeper_pass": True})
        else:
            result.append(t)
    return result


def _validate_spikes(
    current_themes: list[dict],
    review_count: int,
    previous_run: dict | None,
) -> dict:
    """Ask Claude to decide which theme changes are genuine spikes. Returns flagged + dismissed."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    current_summary = "\n".join(
        f"- {t['name']}: {t['review_count']} reviews ({round(t['review_count']/review_count*100, 1) if review_count else 0}% of total), sentiment={t['sentiment']}. {t.get('description', '')}"
        for t in current_themes
    )

    if previous_run and previous_run.get("themes"):
        prev_total = previous_run.get("review_count", 0) or 1
        prev_summary = "\n".join(
            f"- {t['name']}: {t['review_count']} reviews ({round(t['review_count']/prev_total*100, 1)}% of total)"
            for t in previous_run["themes"]
        )
        context = (
            f"Current week ({review_count} total reviews):\n{current_summary}\n\n"
            f"Previous week ({prev_total} total reviews):\n{prev_summary}"
        )
    else:
        context = (
            f"Current week ({review_count} total reviews):\n{current_summary}\n\n"
            "No historical data available — evaluate each theme on its own volume and proportion."
        )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[SPIKE_VALIDATION_TOOL],
        tool_choice={"type": "tool", "name": "submit_spike_validation"},
        system=(
            "You are a game analytics assistant evaluating theme changes for a weekly report. "
            "A spike is something that CHANGED — a week-over-week surge, a new issue emerging, "
            "a review bomb, or a sudden backlash. High volume alone is NOT a spike if the theme "
            "is consistent with prior weeks. "
            "Flag only: (1) themes that increased meaningfully vs. last week, "
            "(2) new themes appearing for the first time with notable volume given the total review count "
            "(e.g. 8 out of 100 reviews is meaningful; 8 out of 500 is likely noise), "
            "(3) clear one-time events (DLC launch, patch, controversy). "
            "Dismiss: dominant themes that are stable, background sentiment, and anything "
            "consistent with the prior week's distribution. "
            "Be strict — 1-2 flags per week is normal. More than 3 should be rare."
        ),
        messages=[
            {
                "role": "user",
                "content": f"{context}\n\nEvaluate each current theme and call submit_spike_validation.",
            }
        ],
    )
    theme_lookup = {t["name"]: t for t in current_themes}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_spike_validation":
            result = block.input
            return {
                "flagged_spikes": [
                    {"label": s["label"], "detail": s["detail"], "hypothesis": s.get("hypothesis", "")}
                    for s in result.get("flagged_spikes", [])
                ],
                "all_candidates": [
                    {
                        "theme_name": s["theme_name"],
                        "label": s["label"],
                        "detail": s["detail"],
                        "hypothesis": s.get("hypothesis", ""),
                        "flagged": True,
                        "sentiment": theme_lookup.get(s["theme_name"], {}).get("sentiment", "mixed"),
                        "representative_quotes": theme_lookup.get(s["theme_name"], {}).get("representative_quotes", []),
                    }
                    for s in result.get("flagged_spikes", [])
                ] + [
                    {
                        "theme_name": s["theme_name"],
                        "reason": s["reason"],
                        "flagged": False,
                        "sentiment": theme_lookup.get(s["theme_name"], {}).get("sentiment", "mixed"),
                    }
                    for s in result.get("dismissed_spikes", [])
                ],
            }
    raise RuntimeError("Claude did not return a tool call in spike validation.")


# ── Public entry points ─────────────────────────────────────────────────────────

def analyze_reviews(reviews: list[dict]) -> dict:
    """
    Send reviews to Claude and return a structured analysis dict.
    Raises ValueError if no reviews are passed.
    Raises anthropic.APIError on API failures.
    """
    if not reviews:
        raise ValueError("No reviews to analyze.")
    return _run_analysis_pass(reviews)


def analyze_reviews_agentic(reviews: list[dict], previous_run: dict | None = None) -> dict:
    """
    Agentic pipeline: adaptive sampling → multi-pass → spike validation.
    Returns the analysis dict augmented with agentic_metadata.
    """
    if not reviews:
        raise ValueError("No reviews to analyze.")

    # Pass all available reviews (up to 500, sampled by steam.py)
    result = _run_analysis_pass(reviews)

    # Multi-pass on ambiguous themes (max 3 total passes)
    passes_used = 1
    for _ in range(2):  # up to 2 additional passes (3 total)
        ambiguous = result.get("ambiguous_theme_names", [])
        if not ambiguous:
            break
        focused = _run_focused_pass(reviews, ambiguous, result["themes"])
        result["themes"] = _merge_themes(result["themes"], focused["themes"])
        passes_used += 1
        result["ambiguous_theme_names"] = focused.get("ambiguous_theme_names", [])

    # Loop 3 — spike validation (replaces Python heuristic)
    spike_result = _validate_spikes(result["themes"], result.get("review_count", 0), previous_run)

    result["flagged_spikes"] = spike_result["flagged_spikes"]
    result["agentic_metadata"] = {
        "passes_used": passes_used,
        "spike_candidates": spike_result["all_candidates"],
        "ambiguous_themes": result.get("ambiguous_theme_names", []),
    }
    return result


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

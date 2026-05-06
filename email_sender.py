"""
Phase 3 — formats analysis JSON into an HTML email and sends via SendGrid.
"""

import os
from datetime import datetime, timezone

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


SENTIMENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "mixed": "🟡",
}


def send_digest(analysis: dict, app_id: str, game_name: str = "", trend_spikes: list[dict] | None = None) -> None:
    """
    Format analysis dict into an HTML email and send via SendGrid.
    Raises ValueError for missing env vars.
    Raises sendgrid exception on delivery failure.
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("FROM_EMAIL")
    to_email = os.environ.get("TO_EMAIL")

    if not all([api_key, from_email, to_email]):
        raise ValueError(
            "Missing required env vars: SENDGRID_API_KEY, FROM_EMAIL, TO_EMAIL"
        )

    title = game_name if game_name else f"App ID {app_id}"
    subject = f"Steam Review Digest — {title} — {_today()}"
    html = _build_html(analysis, title, trend_spikes or [])

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )

    client = SendGridAPIClient(api_key)
    response = client.send(message)

    status = response.status_code
    if status not in (200, 202):
        raise RuntimeError(f"SendGrid returned unexpected status code: {status}")

    print(f"Email sent to {to_email} (status {status})")


# ── HTML builder ───────────────────────────────────────────────────────────────

def _build_html(analysis: dict, title: str, trend_spikes: list[dict]) -> str:
    overall = analysis.get("overall_sentiment", "unknown")
    review_count = analysis.get("review_count", 0)
    themes = analysis.get("themes", [])
    spikes = analysis.get("flagged_spikes", [])

    themes_html = _build_themes(themes)
    spikes_html = _build_spikes(spikes)
    trends_html = _build_trends(trend_spikes)

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; margin: 0; padding: 24px; color: #1a1a1a; }}
  .card {{ background: #fff; border-radius: 8px; padding: 24px;
           max-width: 680px; margin: 0 auto 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .05em;
        color: #666; margin: 0 0 16px; font-weight: 500; }}
  h3 {{ font-size: 15px; margin: 0 0 4px; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px;
            font-size: 12px; font-weight: 600; }}
  .positive {{ background: #d1fae5; color: #065f46; }}
  .negative {{ background: #fee2e2; color: #991b1b; }}
  .mixed    {{ background: #fef9c3; color: #713f12; }}
  .theme {{ border-left: 3px solid #e5e7eb; padding: 12px 16px; margin-bottom: 12px; }}
  .theme-header {{ display: flex; justify-content: space-between; align-items: center;
                   margin-bottom: 6px; }}
  .theme-desc {{ font-size: 14px; color: #444; margin: 0 0 8px; }}
  .quotes {{ margin: 0; padding: 0; list-style: none; }}
  .quotes li {{ font-size: 13px; color: #555; font-style: italic;
                border-left: 2px solid #d1d5db; padding-left: 10px; margin-bottom: 6px; }}
  .spike {{ background: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px;
            padding: 12px 16px; margin-bottom: 10px; }}
  .spike-label {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; }}
  .spike-detail {{ font-size: 13px; color: #555; }}
  .count {{ font-size: 12px; color: #888; }}
  .trend {{ background: #fff7ed; border: 1px solid #fb923c; border-radius: 6px;
            padding: 12px 16px; margin-bottom: 10px; }}
  .trend-label {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; }}
  .trend-bar {{ display: flex; align-items: center; gap: 8px; margin-top: 6px; }}
  .trend-bar-prev {{ height: 8px; background: #d1d5db; border-radius: 4px; }}
  .trend-bar-curr {{ height: 8px; background: #fb923c; border-radius: 4px; }}
  .trend-meta {{ font-size: 12px; color: #78350f; margin-top: 4px; }}
  .footer {{ text-align: center; font-size: 12px; color: #aaa; padding: 8px; }}
</style>
</head>
<body>

<div class="card">
  <h1>🎮 {title}</h1>
  <div class="meta">Steam Review Digest · Last 7 days · {_today()}</div>
  <h2>Summary</h2>
  <p>
    <strong>{review_count}</strong> reviews analysed &nbsp;·&nbsp;
    Overall sentiment: {SENTIMENT_EMOJI.get(overall, '')}
    <span class="badge {overall}">{overall.capitalize()}</span>
  </p>
</div>

<div class="card">
  <h2>Themes</h2>
  {themes_html}
</div>

{f'<div class="card"><h2>⚠️ Flagged Spikes</h2>{spikes_html}</div>' if spikes else ''}

{f'<div class="card"><h2>📈 Week-over-Week Trends</h2>{trends_html}</div>' if trend_spikes else ''}

<div class="footer">Generated by steam-review-agent</div>

</body>
</html>
"""


def _build_themes(themes: list[dict]) -> str:
    if not themes:
        return "<p>No themes identified.</p>"

    parts = []
    for theme in themes:
        name = theme.get("name", "")
        desc = theme.get("description", "")
        sentiment = theme.get("sentiment", "mixed")
        count = theme.get("review_count", 0)
        quotes = theme.get("representative_quotes", [])

        quotes_html = "".join(f"<li>&ldquo;{q}&rdquo;</li>" for q in quotes)

        parts.append(f"""
<div class="theme">
  <div class="theme-header">
    <h3>{name}</h3>
    <span>
      <span class="badge {sentiment}">{SENTIMENT_EMOJI.get(sentiment, '')} {sentiment.capitalize()}</span>
      &nbsp;<span class="count">{count} reviews</span>
    </span>
  </div>
  <p class="theme-desc">{desc}</p>
  {"<ul class='quotes'>" + quotes_html + "</ul>" if quotes_html else ""}
</div>
""")

    return "\n".join(parts)


def _build_spikes(spikes: list[dict]) -> str:
    if not spikes:
        return ""

    parts = []
    for spike in spikes:
        label = spike.get("label", "")
        detail = spike.get("detail", "")
        parts.append(f"""
<div class="spike">
  <div class="spike-label">⚡ {label}</div>
  <div class="spike-detail">{detail}</div>
</div>
""")
    return "\n".join(parts)


def _build_trends(trend_spikes: list[dict]) -> str:
    if not trend_spikes:
        return ""

    parts = []
    for spike in trend_spikes:
        theme = spike["theme"]
        cur_pct = spike["current_pct"]
        prev_pct = spike["previous_pct"]

        if spike["type"] == "new_theme":
            label = f"🆕 New theme emerged: <strong>{theme}</strong>"
            meta = f"{cur_pct}% of reviews this week (not present last week)"
            bar_html = ""
        else:
            change = spike["relative_change"]
            label = f"📈 <strong>{theme}</strong> up {change:.0f}%"
            meta = f"{prev_pct}% → {cur_pct}% of reviews week-over-week"
            # Visual proportional bar scaled to 200px max (capped at 100%)
            scale = 200
            prev_w = min(int(prev_pct * scale / 100), scale)
            curr_w = min(int(cur_pct * scale / 100), scale)
            bar_html = f"""
  <div class="trend-bar">
    <span style="font-size:11px;color:#888;width:32px">prev</span>
    <div class="trend-bar-prev" style="width:{prev_w}px"></div>
  </div>
  <div class="trend-bar">
    <span style="font-size:11px;color:#888;width:32px">now</span>
    <div class="trend-bar-curr" style="width:{curr_w}px"></div>
  </div>"""

        parts.append(f"""
<div class="trend">
  <div class="trend-label">{label}</div>
  <div class="trend-meta">{meta}</div>{bar_html}
</div>""")

    return "\n".join(parts)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%B %d, %Y")


# ── CLI / debug entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv(override=True)

    from steam import SteamAPIError, fetch_reviews
    from analyze import analyze_reviews

    parser = argparse.ArgumentParser(description="Send Steam review digest email.")
    parser.add_argument("--app_id", required=True, help="Steam App ID (e.g. 1245620)")
    parser.add_argument("--game_name", default="", help="Human-readable game name")
    args = parser.parse_args()

    print(f"Fetching reviews for App ID {args.app_id}…")
    try:
        reviews = fetch_reviews(args.app_id)
    except SteamAPIError as e:
        print(f"Steam error: {e}")
        raise SystemExit(1)

    print(f"Fetched {len(reviews)} reviews. Analyzing…")
    try:
        analysis = analyze_reviews(reviews)
    except (ValueError, RuntimeError) as e:
        print(f"Analysis error: {e}")
        raise SystemExit(1)

    print("Sending email…")
    try:
        send_digest(analysis, args.app_id, args.game_name)
    except (ValueError, RuntimeError) as e:
        print(f"Email error: {e}")
        raise SystemExit(1)

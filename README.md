# Steam Review Agent
A personal dashboard that automatically tracks and summarizes Steam game reviews using AI. Each week, it fetches recent player reviews from Steam's API, sends them to Claude for analysis, and returns a structured breakdown of the top themes driving sentiment — categorized as positive, mixed, or negative.

Results are stored in a SQLite database and surfaced through a Flask web dashboard with per-game sentiment trend charts, week-over-week theme tracking, and spike detection when a topic suddenly surges. An optional weekly email digest delivers the summary to your inbox.

Stack: Python · Flask · Claude API (Sonnet) · Steam Web API · SendGrid · SQLite · Railway

Key features:

Tracks multiple games; analysis is anchored to Mon–Sun UTC weeks for consistent comparisons
Dashboard shows current Steam rating, 7-day AI sentiment, top themes, and trend charts
Weekly job updates all tracked games and optionally sends a digest email — both independently toggled from the admin navbar
Deployed on Railway with a cron webhook (/admin/run-weekly) triggering the weekly pipeline


My main learning goal for this project was to experiment building out simple agentic workflows that I wish I had set up while working in games. 

Rough data flow:
Railway pings the Flask app on a schedule → Flask fetches raw reviews from Steam → ships them to Claude for categorization → saves the structured output to SQLite → optionally emails a digest via SendGrid. The dashboard is a read layer on top of the same SQLite database and reflects the lateset run. 

# Steam Review Agent
A self-hosted agentic pipeline that pulls Steam player reviews weekly, runs them through Claude to extract sentiment and themes, and delivers a digest email + persistent dashboard. 

**Live demo:** [reviews.jonathanpaek.com](https://reviews.jonathanpaek.com)

### What it does
- Fetches reviews from the Steam public API on a Mon–Sun cadence
- Sends reviews to Claude (claude-sonnet-4-6) via structured tool use to extract: overall sentiment, top themes, representative quotes, and flagged spikes
- Emails a weekly digest via SendGrid
- Stores history in SQLite and serves a Flask dashboard with trend charts

### Why I built this
This is a tool I wish I had while I still worked at Pahdo Labs to help track our game + our comps' performance. While there are existing tools that provide high level positive/negative review ratings over time, there aren't any tools that helps you quickly understand the sentiment & underlying context behind user reviews. I wanted to make something where I could easily access that sentiment & track it longitudinally.

### Stack
Python · Flask · SQLite · Claude API (Anthropic) · SendGrid · Railway

### Pipeline
Steam API → steam.py → analyze.py (Claude) → email_sender.py (SendGrid) → trends.py (SQLite) → app.py (Flask dashboard)

### Key features / decisions
- Dashboard shows current Steam rating, 7-day AI sentiment, top themes, and trend charts
- Week anchoring from Mon 00:00 → Sun 23:59 for consistent comparison window 
- Weekly job updates all tracked games and optionally sends a digest email, both independently toggled from the admin navbar
- Deployed on Railway with a cron webhook (/admin/run-weekly) to trigger the weekly pipeline

## Screenshots & Slay the Spire 2 Mini Case Study
Slay the Spire 2 is a very popular Early Access game that I've been loosely following since its release. Steam Review Agent flagged a pretty significant sentiment shift in the week ending Sunday 4/19. The pipeline identified that some balance changes drew an outsized negative reaction from Chinese players and was also able to pick up on Discord being blocked in China, making Steam the primary outlet for community feedback & protest vs. the studio's own communication channels.

<strong>Dashboard: tracked games index</strong>
<img width="1447" height="702" alt="image" src="https://github.com/user-attachments/assets/5aa99b2e-fe52-476a-938a-12d22988a2fe" />
<br>
<strong>Game detail: Slay the Spire 2 sentiment trend & top themes</strong>
<img width="1136" height="665" alt="image" src="https://github.com/user-attachments/assets/2cd7bf01-8e94-479a-81aa-f73ee0560701" />
<br>
<strong>Weekly digest email snippet</strong>
<img width="1449" height="419" alt="image" src="https://github.com/user-attachments/assets/dcb91dfa-6dfe-482b-9ed7-c95c3327092e" />
<br>
<strong>Context: Steam's native review page split by recent review language</strong>
<img width="416" height="436" alt="image" src="https://github.com/user-attachments/assets/d347a07d-df32-4d6a-9bb0-3514a6a07b09" />


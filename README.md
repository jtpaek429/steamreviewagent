# Steam Review Agent
A self-hosted agentic pipeline that pulls Steam player reviews weekly, runs them through Claude to extract sentiment and themes, and delivers a digest email + persistent dashboard. 

**Live demo:** [reviews.jonathanpaek.com](https://reviews.jonathanpaek.com)

### What it does
- Fetches up to 500 English-language reviews from the Steam public API on a Mon–Sun cadence
- Sends reviews to Claude (claude-sonnet-4-6) via structured tool use to extract overall sentiment, top themes, representative quotes, and flagged spikes
- Emails a weekly digest via SendGrid
- Stores history in SQLite and serves a Flask dashboard with trend charts

### Why I built this
This is a tool I wish I had while I still worked at Pahdo Labs. While there are some existing tools that provide high level positive/negative game review ratings, there aren't any that help you quickly understand player review sentiment & the underlying context behind those reviews. I wanted to make something where I could easily access that user sentiment as well as track relevant themes longitudinally across a number of different games. 

### Stack
Python · Flask · SQLite · Claude API (Anthropic) · SendGrid · Railway

### Pipeline
Steam API → steam.py → analyze.py (Claude) → email_sender.py (SendGrid) → trends.py (SQLite) → app.py (Flask dashboard)

### Key features / decisions
- Dashboard shows current Steam rating, 7-day AI sentiment, top themes, and trend charts
- Week anchoring from Mon 00:00 → Sun 23:59 for consistent comparison window 
- Weekly job updates all tracked games and optionally sends a digest email, both independently toggled from the admin navbar
- Deployed on Railway with a cron webhook (/admin/run-weekly) to trigger the weekly pipeline
- Initial focus on English-language reviews only for audience segmentation & cost management 

## Screenshots & Slay the Spire 2 Mini Case Study
Slay the Spire 2 is a very popular Early Access game that I've been loosely following since its release. Steam Review Agent flagged a pretty significant sentiment shift in the week ending Sunday 4/19. Even though the analysis focuses on English reviews, the pipeline was still able to identify that some balance changes drew an outsized negative reaction from the game's Chinese playerbase. It was also able to pick up on Discord being blocked in China, which left Steam as the primary outlet for community feedback & protest. 

<strong>Dashboard: tracked games index</strong>
<img width="1447" height="702" alt="image" src="https://github.com/user-attachments/assets/5aa99b2e-fe52-476a-938a-12d22988a2fe" />
<br>
<strong>Game detail: Slay the Spire 2 sentiment trend & top themes</strong>
<img width="1137" height="663" alt="image" src="https://github.com/user-attachments/assets/adeafe25-807a-428a-b904-4a8f3507c2a2" />
<br>
<strong>Weekly digest email snippet</strong>
<img width="1449" height="419" alt="image" src="https://github.com/user-attachments/assets/dcb91dfa-6dfe-482b-9ed7-c95c3327092e" />
<br>
<strong>Context: Steam's native review page split by recent review language</strong>
<img width="416" height="436" alt="image" src="https://github.com/user-attachments/assets/d347a07d-df32-4d6a-9bb0-3514a6a07b09" />


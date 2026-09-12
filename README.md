# 🤖 Daily Agents Collection

A growing library of **one-per-day AI agents**, each self-contained, deterministic, production-quality and shipped with tests, docs and a demo. Built by the **Daily Agent Builder**.

| # | Agent | Folder | What it does |
|---|-------|--------|--------------|
| 18 | **FORGE (Workout Coach)** | [`workout-coach-agent/`](workout-coach-agent/) | Double-progression strength coaching: prescription with a reason, deload/plateau rules, 5 splits, volume audit — offline |
| 17 | **PANTRY (Recipe & Meal Planner)** | [`recipe-planner-agent/`](recipe-planner-agent/) | Pantry → cookable recipes, meal plans, aisle-grouped shopping lists — offline |
| 16 | **Password Vault (VAULTGUARD)** | [`password-vault-agent/`](password-vault-agent/) | Encrypted offline vault: PBKDF2 + AES-GCM, hygiene audit, generators — zero network calls |
| 15 | **Habit Tracker (HABITOS)** | [`habit-tracker-agent/`](habit-tracker-agent/) | Daily habit logging, streak tracking, weekly reports — deterministic & offline |
| 14 | Log Analyzer (LOGWATCH) | `log-analyzer-agent/` | Server-log → structured health reports, insights, pattern clustering |
| 13 | JARVIS AI Assistant | `jarvis-agent/` · `jarvis-agent-web/` | Voice-first persona assistant (Iron Man J.A.R.V.I.S., V4.2 prompt) |
| 12 | API Client Generator | `api-client-generator-agent/` | OpenAPI/Swagger spec → typed Python client package |
| 11 | Meeting Notes | `meeting-notes-agent/` | Minutes, action items, decisions from raw meeting transcripts |
| 10 | Study Buddy | `study-buddy-agent/` | Flashcards, quiz generator, spaced repetition |
| 9 | Email Summarizer | `email-summarizer-agent/` | Thread → concise digest + action items |
| 8 | Social Media Manager | `social-media-manager-agent/` | Platform-aware posts, calendar, captions |
| 7 | Finance Tracker | `finance-tracker-agent/` | CSV → budget reports, health score, insights |
| 6 | Data Analyst | `data-analyst-agent/` | CSV/JSON → stats, charts, narratives |
| 5 | SEO Analyzer | `seo-analyzer-agent/` | On-page SEO audits with weighted scoring |
| 4 | Content Writer | `content-writer-agent/` | Blogs, social, product + email copy, SEO analysis |
| 3 | Research Assistant | `research-assistant-agent/` | Multi-source research digests with citations |
| 2 | Code Reviewer | `code-reviewer-agent/` | Static review, smells, security nits |
| 1 | Travel Planner | `travel-planner-agent/` | Itineraries, budgets, packing lists |

## Conventions

Every agent follows the same recipe:

- **Deterministic engine** — pure functions, no ML, no network, no flake.
- **3+ surfaces** — CLI, Python library, and REST API (FastAPI, Vercel-ready).
- **Live demo** — a client-side `web-live/` build, published as a single self-contained HTML file.
- **Tests** — pytest suite that runs fully offline (`python3 -m pytest tests/ -q`).
- **Docs** — professional README with architecture, setup, usage, config and deployment.

## Live demos

- FORGE workout coach (69-lift library, live prescription engine):
  https://raza077-coder.github.io/daily-agents/workout-coach-agent/web-live/
- PANTRY recipe & meal planner (single-file, runs 100% in-browser):
  https://static.teamily.ai/sites/78d6b75c-bd28-454d-af6d-43bbb8dc75ba/webpages/pantry-agent/index.html
- VAULTGUARD browser vault: `https://raza077-coder.github.io/daily-agents/password-vault-agent/web-live/`
- HABITOS dashboard: `https://raza077-coder.github.io/daily-agents/habit-tracker-agent/web-live/`
- LOGWATCH dashboard: `https://raza077-coder.github.io/daily-agents/log-analyzer-agent/web-live/`
- JARVIS HUD: `https://raza077-coder.github.io/jarvis-agent/`

## Deployment notes

Vercel credentials are not available on the build platform, so each agent ships Vercel-ready (`vercel.json` + `api/index.py`) plus a static web demo. To put any agent live on Vercel: `npm i -g vercel && vercel login`, then `cd <agent-folder> && vercel --prod`.

Agents that duplicate their engine in the browser (FORGE's `web-live/forge-engine.js`) also ship a parity harness — `tools/parity_check.js` + `tools/parity_check.py` — because duplicated logic drifts, and a demo that quietly disagrees with the CLI is worse than no demo.

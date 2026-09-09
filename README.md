# 🤖 Daily Agents Collection

A growing library of **one-per-day AI agents**, each self-contained, deterministic, production-quality and shipped with tests, docs and a demo. Built by the **Daily Agent Builder**.

| # | Agent | Folder | What it does |
|---|-------|--------|--------------|
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
- **Live demo** — a client-side `web-live/` build deployed on GitHub Pages where possible.
- **Tests** — pytest suite that runs fully offline (`python3 -m pytest tests/ -q`).
- **Docs** — professional README with architecture, setup, usage, config and deployment.

## Live demos (GitHub Pages)

- HABITOS dashboard: `https://raza077-coder.github.io/daily-agents/habit-tracker-agent/web-live/`
- LOGWATCH dashboard: `https://raza077-coder.github.io/daily-agents/log-analyzer-agent/web-live/`
- JARVIS HUD: `https://raza077-coder.github.io/jarvis-agent/`

## Deployment notes

Vercel credentials are not available on the build platform, so each agent ships Vercel-ready (`vercel.json` + `api/index.py`) and a static GitHub Pages demo. To put any agent live on Vercel: `npm i -g vercel && vercel login`, then `cd <agent-folder> && vercel --prod`.

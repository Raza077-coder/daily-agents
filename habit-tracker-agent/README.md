# HABITOS — Habit Tracker Agent

**HABITOS** turns daily habits into streaks, weekly reports and honest status — deterministic, offline-first, no ML, no accounts, no network required. Log a habit, protect a streak, read the report. It is the fifteenth agent in the [daily-agents](https://github.com/Raza077-coder/daily-agents) collection.

> “Discipline is matching intention. Keep the chain alive, Sir.” — HABITOS coach

---

## Overview

HABITOS is a small, dependency-light personal habit tracker delivered three ways:

| Surface | What you get |
|---|---|
| **CLI** (`habitos …`) | add / list / log / unlog / status / report / trend / detail / delete / archive / demo |
| **Python library** | `HabitEngine` — script your own trackers, CI checks, analytics |
| **REST API** | FastAPI endpoints (Vercel-ready) + a fully client-side **web dashboard** |
| **Web demo (live)** | `web-live/` — pure-JavaScript port of the engine running 100% in the browser (data stays in localStorage) |

The engine is a deterministic 5-layer pipeline: **store → hydration → logging → streak/analytics computation → report rendering**. Same input always yields the same output, so it is trivially testable and CI-friendly.

---

## Features

- **Habit CRUD** — create with name/category/target (days-per-week or frequency label: `daily`, `weekdays`, `weekly`, `weekends`), list, archive, delete.
- **Daily check-ins** — `log` a completion for today or any past date (backfill), `unlog` mistakes. Idempotent — double-logging is a no-op.
- **Streak engine** — current + longest streak in calendar days. A run ending *yesterday* still counts as current while today is pending (so you are not punished before the day is over).
- **Weekly health** — per-habit completion % for the current ISO week, projected pace, and a status label: 🟢 `on_track`, 🟡 `at_risk`, 🔴 `off_track`.
- **Weekly report** — plain-text digest of every active habit with a dot-grid of the week, plus JSON variant for pipelines.
- **Trend view** — per-ISO-week completion counts over the last N weeks (ASCII chart in the CLI).
- **Demo dataset** — one command seeds ~3 weeks of realistic history across 4 habits.
- **Deterministic & offline** — pure Python stdlib engine, no ML, no API keys, no telemetry.

---

## How it works

```
                ┌──────────────────────────────────────────────┐
  JSON store ──▶│ storage.py   load / hydrate / save           │
  (file/temp)   │ models.py    Habit · LogEntry · HabitStore   │
                ├──────────────────────────────────────────────┤
                │ analytics.py compute_streaks · week_stats    │
                │              status_for · normalize_target   │
                ├──────────────────────────────────────────────┤
                │ engine.py    HabitEngine (orchestration)     │
                └──────────────┬───────────────────────────────┘
                               │
        ┌──────────────────────┼───────────────────────┐
        ▼                      ▼                       ▼
     cli.py                api/index.py           web-live/
  argparse CLI            FastAPI REST           habit-engine.js
  (habitos …)             (Vercel-ready)         (browser port)
```

Streak semantics (pure function `compute_streaks`):

1. Walk backwards from today while the habit was done → **current streak**. Today need not be done yet — if today is pending but yesterday was done, the run continues.
2. Scan the full history for the longest consecutive run → **longest streak**.

Weekly health (pure function `week_stats`): the ISO week starts Monday; `week_possible` is the number of days elapsed this week; `completion_pct = week_done / week_possible`.

---

## Tech stack

| Layer | Tech |
|---|---|
| Engine | Python 3.9+ **standard library only** (dataclasses, argparse, json) |
| REST API | FastAPI + Pydantic + Uvicorn |
| Web demo | Vanilla HTML/CSS/JS (no build step) — `habit-engine.js` is a faithful port of the Python engine |
| Tests | pytest (engine + CLI + API), Node smoke check for JS parity |
| Persistence | JSON file (CLI/API) · localStorage (web demo) |
| Deployment | Vercel (`vercel.json` + `api/index.py`) · GitHub Pages (`web-live/`) · Docker |

---

## Setup / Installation

```bash
git clone https://github.com/Raza077-coder/daily-agents
cd daily-agents/habit-tracker-agent

# CLI-only (stdlib, zero deps beyond Python itself)
python3 -m habit_tracker.cli --help

# Full stack (API + tests)
pip install -r requirements.txt
```

No install required for the web demo — open `web-live/index.html` in any browser, or serve it:

```bash
cd web-live && python3 -m http.server 8000   # → http://localhost:8000
```

---

## Usage examples

### CLI

```bash
# Create habits
python3 -m habit_tracker.cli add "Meditate" --frequency daily
python3 -m habit_tracker.cli add "Workout" --target 4 --category fitness
python3 -m habit_tracker.cli add "Read 20 pages" --frequency weekdays --category learning

# Log check-ins (today by default, backfill with --date)
python3 -m habit_tracker.cli log meditate
python3 -m habit_tracker.cli log workout --date 2026-09-07
python3 -m habit_tracker.cli unlog workout --date 2026-09-08    # oops

# See the board
python3 -m habit_tracker.cli list
python3 -m habit_tracker.cli status        # 7-day strip per habit
python3 -m habit_tracker.cli report        # weekly digest (text)
python3 -m habit_tracker.cli report --json # weekly digest (JSON)
python3 -m habit_tracker.cli trend workout --weeks 8
python3 -m habit_tracker.cli demo          # seed a demo dataset
```

Typical output of `list`:

```
HABIT                                           PROGRESS  WEEKLY  STREAK
🟢 Meditate               ██████░░░░░░░░    57%  week 4/7  streak 4d  best 9d
🟢 Workout                ████████████░░   100%  week 4/4  streak 2d  best 5d
```

### Python library

```python
from habit_tracker.engine import HabitEngine

eng = HabitEngine(data_path="my_habits.json")     # "" = in-memory
eng.add_habit("Meditate", category="mindfulness", frequency="daily")
eng.log("meditate")                                # today
eng.log("meditate", date="2026-09-07")             # backfill
print(eng.weekly_report(format="text"))
print(eng.summary())
```

### REST API (FastAPI)

```bash
uvicorn api.index:app --reload        # → http://localhost:8000/docs
```

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/health` | — | liveness |
| GET | `/habits` | — | list habits + summary |
| GET | `/habits/{id}` | — | detail incl. `last_7_days` |
| POST | `/habits` | `{"name","category","target_per_week","frequency"}` | create |
| POST | `/habits/{id}/log` | `{}` or `{"date","note"}` | check-in |
| POST | `/habits/{id}/unlog` | `{}` or `{"date"}` | remove check-in |
| POST | `/habits/{id}/archive` | `{"archived": true}` | archive/unarchive |
| GET | `/report` · `/report/json` | — | weekly digest |
| POST | `/demo` | — | seed demo dataset |

---

## Configuration

| Knob | Where | Default |
|---|---|---|
| Data file path | `HABITOS_DATA` env / `--data` / `data_path=` | temp file (`/tmp/habitos_data.json`) |
| `""` as data path | constructor | pure in-memory (no persistence) |
| Categories | `HABITOS_CATEGORIES` | `health,productivity,learning,fitness,mindfulness,general` |
| Frequency labels | `analytics.FREQ_LABELS` | `daily`→7 · `weekdays`→5 · `weekly`→1 · `weekends`→2 |
| Trend window | `--weeks` / `HABITOS_TREND_WEEKS` | 6 |
| Coach tone | `habitos_persona.json` | per-status phrases (on_track / at_risk / off_track) |
| Suggested habits + colors | `habitos_persona.json` | per-category |

See `config.env.example` for an env-style template.

---

## Testing

```bash
python3 -m pytest tests/ -q      # 61 tests: engine · analytics · CLI · API · determinism
```

Coverage highlights: streak edge cases (pending today, broken runs, longest ≠ current), ISO-week boundaries, idempotent logging, backfill, duplicate-name id generation, persistence round-trips, every CLI exit code, every FastAPI endpoint (incl. 400/404 paths), and byte-identical deterministic reports.

The browser engine is parity-checked against the same semantics in Node.

---

## Deployment

### Vercel (recommended for the API + dashboard)

`vercel.json` and `api/index.py` are ready:

```bash
npm i -g vercel && vercel login
cd daily-agents/habit-tracker-agent
vercel --prod
```

Or use the Vercel Dashboard → **Import Git Repository** → `Raza077-coder/daily-agents` → **Root Directory** = `habit-tracker-agent`.

### GitHub Pages (static web demo)

The `web-live/` folder is a fully client-side dashboard (no backend calls). Enable Pages on the repo with **branch `main`, folder `/`**, then open:

```
https://<user>.github.io/daily-agents/habit-tracker-agent/web-live/
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
```

### CI

Because everything is deterministic, `python3 -m pytest tests/ -q` is a complete CI gate — no network, no fixtures, no flake.

---

## Repository layout

```
habit-tracker-agent/
├── habit_tracker/          # engine (stdlib only)
│   ├── __init__.py
│   ├── models.py           # Habit · LogEntry · HabitStore
│   ├── storage.py          # JSON load/save · hydration · date helpers
│   ├── analytics.py        # streaks · week stats · status labels
│   ├── engine.py           # HabitEngine orchestration
│   └── cli.py              # argparse CLI (habitos)
├── api/index.py            # FastAPI REST (Vercel-ready)
├── web-live/               # client-side dashboard (GitHub Pages-ready)
│   ├── index.html · style.css · app.js · habit-engine.js
├── tests/test_habitos.py   # 61 pytest cases
├── data/sample_habits.json # demo dataset (generated)
├── make_sample_data.py     # dataset generator
├── habitos_persona.json    # coach phrases · suggestions · colors
├── config.env.example
├── vercel.json · pyproject.toml · requirements.txt
└── README.md
```

---

## License

MIT — free to use, fork, and ship. Built by the **Daily Agent Builder** for the daily-agents collection.

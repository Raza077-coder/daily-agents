# JOBFLOW — Job Application Tracker Agent

> **Your job search, with the maths done honestly.**

JOBFLOW is a deterministic, offline agent that keeps an honest ledger of a job
search and turns it into a **ranked list of things worth doing today** — with a
readable reason attached to every single action.

No accounts. No API keys. No network calls. No LLM in the loop at runtime. Same
vault plus same date always produces byte-identical output, so you can actually
trust the numbers.

Part of the [`daily-agents`](https://github.com/Raza077-coder/daily-agents)
collection — **Daily Agent #20**.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Key features](#key-features)
- [How it works](#how-it-works)
- [The two ideas that make it honest](#the-two-ideas-that-make-it-honest)
- [Tech stack](#tech-stack)
- [Project layout](#project-layout)
- [Setup](#setup)
- [Usage — CLI](#usage--cli)
- [Usage — Python library](#usage--python-library)
- [Usage — REST API](#usage--rest-api)
- [Live web demo](#live-web-demo)
- [Configuration](#configuration)
- [Verification](#verification)
- [Deployment](#deployment)
- [FAQ](#faq)
- [Limitations](#limitations)

---

## Why this exists

A spreadsheet tracks applications. It does not tell you:

- that **60 days of silence** means the follow-up phase is over and the effort
  should move elsewhere;
- that your **referrals convert 3× better** than job-board applications, so
  next month should look different from last month;
- that your response rate is **not measurable yet** because nothing is old
  enough to have received a reply;
- that a rejection *after* an onsite round means you **did reach the final
  stage** — the funnel should not erase that.

JOBFLOW computes all of the above from an append-only event log, using rules
you can read, argue with, and change.

---

## Key features

### Pipeline tracking
- **Nine statuses** — `wishlist → applied → screen → interview → onsite → offer → accepted`, plus `rejected` / `withdrawn`
- **Append-only event log** — applications, moves, interviews, notes and follow-ups are all events, so history is never rewritten
- **Rung validation** — skipping a stage (`applied → onsite`) is refused with the skipped stages named, unless you explicitly `force` it
- **Interview scheduling** with kind (`screen`, `technical`, `behavioural`, `system_design`, `final`, …) and done-tracking
- **Next-action commitments** — log what you owe and when, and get nudged when it lapses

### Cohort-aware funnel maths
- **Furthest-rung tracking** — a rejection after an onsite still counts as having reached onsite
- **Maturity windows per rung** (21/30/45/60/75/90 days) so fresh applications are never counted as failures
- **Step rates** (rung-to-rung) *and* cumulative rates (against matures) reported side by side
- **Response rate with an honest denominator** — read as `n/a` when nothing has matured
- **Time-in-stage** — median/min/max days per rung, over completed stretches only

### A ranked daily plan
- **16 deterministic rules** covering offers, deadlines, interviews, quiet
  applications, unanswered follow-ups, thank-you notes, rejection post-mortems,
  wishlist rot and weekly targets
- **Explainable scoring** — every action returns its own `terms` breakdown
  (severity, overdue days, staleness, priority, stage) and an `explain` sentence
- **Follow-up discipline** — an application is nudged at most `max_followups`
  times, then the advice flips from "follow up" to "stop chasing"

### Insights
- **Source effectiveness** — interview rate per channel, so you know where to
  spend effort
- **Compensation summary** — reported per currency (never summed across them)
- **Weekly throughput and streak** — applications against interviews, week by week
- **Going-quiet list** — open applications past their stall threshold

### Four surfaces, one engine
| Surface | Entry point | Use it for |
|---|---|---|
| **CLI** | `python3 -m jobtracker.cli` | Day-to-day logging on your machine |
| **Python** | `JobFlowEngine` | Scripting, notebooks, custom reports |
| **REST** | `api/app.py` | Apps, integrations, serverless deploys |
| **Web HUD** | `web-live/index.html` | A live demo, and a read-only browser view |

---

## How it works

```
                 ┌──────────────────────────────────────────────┐
   CLI  ────────►│                                              │
   Python ──────►│              JobFlowEngine                    │
   REST ────────►│   (jobtracker/engine.py — the only facade)    │
                 │                                              │
                 └───┬───────────────┬───────────────┬──────────┘
                     │               │               │
              ┌──────▼─────┐  ┌──────▼──────┐  ┌─────▼──────┐
              │ pipeline.py│  │analytics.py │  │followups.py│
              │  verbs:    │  │ funnel      │  │ 16 rules   │
              │ add/move/  │  │ aging       │  │ scoring    │
              │ note/apply │  │ time-in-    │  │ explain    │
              │ interview  │  │   stage     │  │ plan       │
              └──────┬─────┘  │ sources     │  └─────┬──────┘
                     │        │ weekly      │        │
                     │        └──────┬──────┘        │
                     │               │               │
              ┌──────▼───────────────▼───────────────▼──────┐
              │              models.py                       │
              │  Application · Event · Interview · Note      │
              │  money = integer minor units                 │
              │  every number derived from the event log     │
              └──────────────────┬───────────────────────────┘
                                 │
                          ┌──────▼──────┐
                          │  store.py   │  atomic JSON vault (0600)
                          └─────────────┘
```

**Data flow for one plan request:**

1. `store.py` loads the vault (or you construct a `Vault` in memory).
2. `models.py` reconstructs each `Application`, re-deriving state from its
   **event log** — not from a cached status field.
3. `analytics.py` computes the funnel, aging, time-in-stage, sources and weekly
   throughput, each against a concrete `today`.
4. `followups.py` runs all 16 rules, scores and sorts them, and returns the plan.
5. `report.py` renders it as text or Markdown; the API serialises it as JSON.

---

## The two ideas that make it honest

### 1. Cohort-aware funnels

Naive funnel math counts a 3-day-old application in the denominator and a
6-month-old one the same way. That makes week one look like a catastrophe and
week fifty look like a crisis of confidence.

JOBFLOW asks: **"of the applications old enough for a reply to be plausible, how
many got one?"** Each rung has its own maturity window:

| Stage | Maturity |
|---|---|
| Applied | 21 days |
| Recruiter Screen | 30 days |
| Interview | 45 days |
| Onsite / Final | 60 days |
| Offer | 75 days |
| Accepted | 90 days |

When nothing is old enough, the response rate reads **"not measurable yet"** —
not `0.0%`. Reporting an immaturity as a failure is the single most common way
a job-search tracker demoralises its user.

### 2. Furthest-rung semantics

An application rejected *after* the onsite round reached the onsite round.
JOBFLOW derives progress from the event log, so that rejection increments the
onsite rung and increments the interview rung — it does not get rewritten into
"applied and rejected" by a status column.

This is why the demo vault shows **2 applications reaching onsite** while only
one has an open offer: one of them is a post-onsite rejection.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Core engine | **Python 3.9+ stdlib only** | Runs anywhere; no install friction |
| Persistence | **JSON vault** via `store.py` | Human-readable, diffable, portable |
| Money | **Integer minor units** | Floats cannot represent money safely |
| Dates | `datetime.date`, ISO strings | No timezone surprises |
| CLI | `argparse` | Zero deps, real exit codes |
| REST | **FastAPI** + Pydantic | Typed schemas, auto OpenAPI |
| Web demo | **Vanilla JS / CSS / HTML** | No build step, no CDN, works from `file://` |
| Parity | **Node.js harness** | Proves the JS engine matches the Python one |

The engine has **no third-party dependencies at all**. FastAPI is needed only
for the REST layer.

---

## Project layout

```
job-tracker-agent/
├── jobtracker/                 # the engine — stdlib only
│   ├── __init__.py             #   public exports
│   ├── models.py               #   Application, Event, Interview, Note, money
│   ├── store.py                #   atomically-persisted JSON vault
│   ├── pipeline.py             #   verbs: add, apply, move, note, interview…
│   ├── analytics.py            #   funnel, aging, time-in-stage, sources, weekly
│   ├── followups.py            #   16 rules, scoring, the daily plan
│   ├── report.py               #   text + Markdown rendering
│   ├── engine.py               #   JobFlowEngine facade
│   └── cli.py                  #   argparse CLI (24 commands)
│
├── api/
│   ├── app.py                  # FastAPI REST layer (/api/*)
│   └── index.py                # Vercel serverless entry point
│
├── web-live/                   # the live browser demo (read-only)
│   ├── index.html              #   4 tabs: Dashboard / Plan / Board / Insights
│   ├── style.css               #   no CDN, no web fonts
│   ├── track-engine.js         #   JS port of the engine (real, not a mock)
│   ├── demo-data.js            #   GENERATED — do not hand-edit
│   └── app.js                  #   Pure rendering over the two above
│
├── tools/
│   ├── build_web_data.py       # bakes the demo vault into demo-data.js
│   ├── parity_check.py         # Python ↔ JS parity harness (123 checks)
│   ├── api_smoke.py            # 43 live API checks
│   └── web_smoke.js            # 36 offline/no-network checks
│
├── AGENT.md                    # system prompt + behaviour spec
├── jobflow.persona.json        # machine-readable persona & invariants
├── config.example.yaml         # every configuration key
├── requirements.txt            # FastAPI/uvicorn (core needs nothing)
├── vercel.json                 # serverless config
└── README.md
```

---

## Setup

### Requirements

- **Python 3.9+** for the CLI and library (no packages needed)
- **Node.js 18+** only if you want to regenerate the web demo or run parity
- **FastAPI + uvicorn** only if you want the REST layer

### Install

```bash
git clone https://github.com/Raza077-coder/daily-agents.git
cd daily-agents/job-tracker-agent

# The engine needs nothing:
python3 -m jobtracker.cli vocab

# Only for the REST API:
pip install -r requirements.txt
```

### Try it in 30 seconds

```bash
export JOBFLOW_VAULT=/tmp/demo-vault.json

python3 -m jobtracker.cli demo          # seed 7 realistic applications
python3 -m jobtracker.cli plan          # the ranked plan
python3 -m jobtracker.cli report        # full digest
```

---

## Usage — CLI

24 commands. The vault path comes from `JOBFLOW_VAULT` or `--vault`.

### Add and track applications

```bash
# Log a wishlist item with a deadline
python3 -m jobtracker.cli add "Stripe" "Backend Engineer" \
    --source referral --priority 5 \
    --salary-min 160000 --salary-max 190000 --currency USD \
    --location "Remote" --deadline 2026-09-30

# Mark it submitted
python3 -m jobtracker.cli log-apply stripe-backend-engineer

# Move it along the ladder
python3 -m jobtracker.cli move stripe-backend-engineer screen --note "recruiter call"
python3 -m jobtracker.cli move stripe-backend-engineer interview
```

```
$ python3 -m jobtracker.cli move stripe-backend-engineer onsite --note "full loop"
Stripe — Backend Engineer is now Onsite / Final.
```

### Track interviews and commitments

```bash
python3 -m jobtracker.cli interview stripe-backend-engineer \
    --on 2026-09-22 --kind technical --note "system design focus"

python3 -m jobtracker.cli next stripe-backend-engineer "Send thank-you note" --on 2026-09-19
python3 -m jobtracker.cli note stripe-backend-engineer "Panel asked about rate limiting"
python3 -m jobtracker.cli followup stripe-backend-engineer --note "pinged recruiter"
```

### The daily plan and reports

```bash
python3 -m jobtracker.cli plan          # ranked actions with reasons
python3 -m jobtracker.cli status        # funnel + plan, one screen
python3 -m jobtracker.cli report        # full digest
python3 -m jobtracker.cli md            # Markdown, for a PR or a journal
python3 -m jobtracker.cli board         # applications grouped by status
python3 -m jobtracker.cli upcoming      # interviews in the next 14 days
python3 -m jobtracker.cli why no_response_overdue   # explain a rule
```

### Sample output

```
$ python3 -m jobtracker.cli plan

JOBFLOW — Thursday 17 September 2026
════════════════════════════════════════════════════════════════════
  6 submitted · 5 open · 1 wishlist · 2 closed
  Response rate 100.0% (5/5 mature applications)

TODAY'S PLAN
────────────────────────────────────────────────────────────────────
 1. ! CRITICAL  Offer decision due
      Northwind Labs — Senior Backend Engineer
      Decide on Northwind Labs — respond to the offer, ask about the
      deadline, or negotiate. Silence reads as disinterest.
      why: Offer outstanding for 27 days.
      score 1100 · severity 1000 · overdue 27 · priority 75

 2. ! HIGH      Follow up — no response
      Quarry Analytics — ML Engineer
      Stop chasing Quarry Analytics — you have followed up 2 time(s).
      Mark it rejected or withdrawn and put the effort into new applications.
      score 668 · severity 200 · staleness 116 · priority 30

 3. · MEDIUM    Learn from a rejection
      Cobalt Systems — Platform Engineer
      Write a post-mortem note: what was asked, where it went thin, and
      the one thing to drill before the next loop.
      why: Rejected at the onsite rung 9 day(s) ago.
      score 573 · severity 200 · overdue 9 · priority 60 · stage 48
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Domain error — validation, not found, illegal move |
| `2` | Usage error (argparse) |

`0`/`1` make the CLI safe to use in scripts and CI.

---

## Usage — Python library

```python
from jobtracker import JobFlowEngine

engine = JobFlowEngine.open("jobflow-vault.json")

# Log an application
engine.add_application(
    company="Veridian Media",
    role="Backend Developer",
    source="referral",
    priority=4,
    salary_min="120000",
    currency="USD",
)
engine.save()

# Advance it
engine.log_apply("veridian-media-backend-developer")
engine.move("veridian-media-backend-developer", "screen", note="recruiter call")
engine.schedule_interview("veridian-media-backend-developer", "2026-09-25", kind="technical")
engine.save()

# Read
plan = engine.plan(today="2026-09-17", limit=5)
print(plan["headline"])
for action in plan["actions"]:
    print(f"{action['severity']:8} {action['title']}: {action['action']}")

summary = engine.summary(today="2026-09-17")
print(summary["funnel"]["response_rate"], summary["funnel"]["response_denominator"])
print(summary["sources"][0])     # best-converting channel first
print(summary["salary"])         # per-currency — never summed
```

Everything is pure: pass `today=` and the output is reproducible forever.

---

## Usage — REST API

```bash
pip install -r requirements.txt
export JOBFLOW_VAULT=/tmp/jobflow-vault.json
uvicorn api.app:app --reload --port 8000
```

Interactive docs at `http://127.0.0.1:8000/docs`.

### Key endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness + vault path |
| `POST` | `/api/demo` | Seed the demo vault |
| `GET` | `/api/summary` | Funnel, aging, sources, weekly, salary |
| `GET` | `/api/plan?limit=10` | The ranked daily plan |
| `GET` | `/api/funnel` | Funnel rows with maturity windows |
| `GET` | `/api/applications` | List (derived state included) |
| `GET` | `/api/applications/{id}` | One record + the same derived block |
| `POST` | `/api/applications` | Create |
| `POST` | `/api/applications/{id}/move` | Advance a stage |
| `POST` | `/api/applications/{id}/apply` | Mark submitted |
| `POST` | `/api/applications/{id}/interview` | Schedule an interview |
| `POST` | `/api/applications/{id}/followup` | Log a follow-up |
| `POST` | `/api/applications/{id}/next` | Set the next commitment |
| `GET` | `/api/report`, `/api/report.md` | Text / Markdown digest |
| `GET` | `/api/why/{rule}` | Explain a rule |
| `GET` | `/api/vocabulary` | Statuses, stages, sources, rules |

```bash
BASE=http://127.0.0.1:8000/api

curl -s -X POST $BASE/demo
curl -s "$BASE/plan?limit=3" | python3 -m json.tool | head -30

curl -s -X POST $BASE/applications \
  -H 'Content-Type: application/json' \
  -d '{"company":"Acme","role":"SRE","source":"referral","priority":5}'

curl -s -X POST $BASE/applications/acme-sre/move \
  -H 'Content-Type: application/json' \
  -d '{"status":"screen","note":"recruiter call"}'
```

**Error contract:** `400` domain error (with the reason in `detail`), `404` unknown
id, `422` malformed body. Illegal stage skips are `400` and name the skipped rungs.

---

## Live web demo

**→ https://raza077-coder.github.io/daily-agents/job-tracker-agent/web-live/**

Four tabs, all computed in your browser from a baked-in demo vault:

| Tab | Shows |
|---|---|
| **Dashboard** | Stat cards, funnel with maturity note, response rate, weekly bars |
| **Today's Plan** | Every action with severity, the instruction, the reason, the score breakdown |
| **Board** | Applications grouped by status with age, quiet days, stall flags |
| **Insights** | Source effectiveness, per-currency compensation, time-in-stage, going quiet |

The page is **read-only and fully offline** — no `fetch`, no `XMLHttpRequest`,
no CDN. It works from `file://`. Verify that claim yourself:

```bash
node tools/web_smoke.js     # stubs fetch/XHR to throw, then asserts the page still renders
```

> The web demo re-implements the engine in JavaScript. Rather than trusting that
> the two stay in sync, `tools/parity_check.py` asserts it: it runs both engines
> over two vaults and compares **123 fields**.

Regenerate the demo data after changing the engine:

```bash
python3 tools/build_web_data.py     # writes web-live/demo-data.js
```

---

## Configuration

Every key is optional. Set via environment variable or the vault's config block
— see [`config.example.yaml`](config.example.yaml) for the annotated list.

| Env var | Default | Meaning |
|---|---|---|
| `JOBFLOW_VAULT` | `jobflow-vault.json` | Vault file path |
| `JOBFLOW_TODAY` | *(system date)* | Pin the reference date (testing/reports) |
| `JOBFLOW_WEEKLY_TARGET` | `5` | Applications per ISO week |
| `JOBFLOW_FOLLOWUP_DAYS` | `7` | Silence before the first follow-up |
| `JOBFLOW_SECOND_FOLLOWUP_DAYS` | `14` | Silence before the final follow-up |
| `JOBFLOW_WISHLIST_IDLE_DAYS` | `10` | Wishlist-rot threshold |
| `JOBFLOW_MAX_FOLLOWUPS` | `2` | Hard ceiling on nudges |
| `JOBFLOW_PLAN_LIMIT` | `10` | Actions shown by default |
| `JOBFLOW_CURRENCY` | `USD` | Default currency for new applications |

```bash
export JOBFLOW_WEEKLY_TARGET=8
export JOBFLOW_MAX_FOLLOWUPS=3
python3 -m jobtracker.cli plan
```

The full behavioural spec — every rule, threshold and scoring weight — is in
[`AGENT.md`](AGENT.md), with a machine-readable copy in
[`jobflow.persona.json`](jobflow.persona.json).

---

## Verification

Three independent harnesses, all offline.

```bash
# 1. Python <-> JavaScript parity — 123 checks over 2 vaults
python3 tools/parity_check.py
# => PASS — all 123 checks agree between Python and JavaScript

# 2. REST layer — 43 checks against a real ephemeral vault
python3 tools/api_smoke.py
# => PASS — all 43 checks green

# 3. Web demo — 36 checks, network stubbed to throw
node tools/web_smoke.js
# => PASS — page is fully offline and renders real data
```

### Bugs these harnesses actually caught during development

These are documented because they are the reason the harnesses exist — each one
produced plausible-looking output that was wrong.

| # | Bug | Symptom | Fix |
|---|---|---|---|
| 1 | **Salary double-conversion on reload** | `--salary-min 95000` became `9,500,000` after save→load, because the vault already stores minor units and the loader parsed the int again | `salary_is_minor` flag on vault reads; `parse_money` documented to never see vault values |
| 2 | **Day padding drift** | Python `strftime('%b %d')` → `Aug 03`, JS `toLocaleDateString` → `Aug 3` | Explicit `weekLabel()` in JS |
| 3 | **Half-even vs half-up rounding** | Compact median salary `€106.5k` → Python `€106k`, JS `€107k` | `roundHalfEven()` replicates Python's rule |
| 4 | **Empty-vault 500** | Every read endpoint returned HTTP 500 on a brand-new vault: `resolve_today(None)` raised instead of falling back to the clock | `resolve_today` falls back to `date.today()` |
| 5 | **List/detail divergence** | `/api/applications` returned a derived block; `/api/applications/{id}` returned the raw record — the same application reported different state depending on the endpoint | one shared `_summary_row()` helper |
| 6 | **Contradictory offer advice** | An open offer got both `offer_expiring` *and* a "follow up" nudge | silence rules skip `status=offer` |

Bug #1 is the one worth dwelling on: it is silent, it scales the number by 100×,
and a spreadsheet would never notice.

---

## Deployment

### GitHub Pages (the live demo — recommended for this agent)

Zero backend, so the demo is already deployed. To rebuild:

```bash
# enable Pages once:
#   Settings → Pages → Source: Deploy from a branch → main → / (root)
python3 tools/build_web_data.py
git add web-live/ && git commit -m "Rebuild JOBFLOW web demo" && git push
```

Live at `https://<user>.github.io/daily-agents/job-tracker-agent/web-live/`.

### Vercel (the REST API)

`vercel.json` and `api/index.py` are committed and the app is verified working.

```bash
npm i -g vercel
cd job-tracker-agent
vercel login
vercel --prod
```

Or import the repo at [vercel.com/new](https://vercel.com/new) with
**Root Directory = `job-tracker-agent`**.

> ⚠️ **Serverless caveat.** Vercel's filesystem is ephemeral — a JSON vault
> written during one invocation will not survive a cold start. For durable
> storage use `GET /api/export` before scaling down, point
> `JOBFLOW_VAULT` at a mounted volume, or swap `store.py` for a database
> backend. The CLI on a local machine has no such limitation.

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV JOBFLOW_VAULT=/data/jobflow-vault.json
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t jobflow .
docker run -p 8000:8000 -v jobflow-data:/data jobflow
```

### Cron / CI

The plan is deterministic and the CLI uses real exit codes, so it drops into
automation cleanly — a Monday-morning digest, or a nightly stall check that
fails the job if anything critical appears:

```bash
#!/usr/bin/env bash
set -euo pipefail
export JOBFLOW_VAULT=/srv/jobflow/vault.json
python3 -m jobtracker.cli md > /srv/jobflow/reports/$(date +%F).md

# fail the pipeline if anything urgent is outstanding
if python3 -m jobtracker.cli --json plan | grep -q '"severity": *"critical"'; then
  echo "JOBFLOW: critical items outstanding" >&2
  exit 1
fi
```

---

## FAQ

**Is this just a spreadsheet?**
No. A spreadsheet stores rows. JOBFLOW derives cohort-aware funnel rates,
time-in-stage medians, source effectiveness, stall detection and a ranked plan
with a reason per action — and refuses to report a number it cannot support.

**Why no LLM?**
Because "what should I do today" is a rules question with a defensible answer. A
model that phrases advice differently on every run is worse than a rule that
says *"60 days quiet, threshold 14, stop chasing."* Every action is auditable.

**Where is my data?**
In one JSON file you own, at `JOBFLOW_VAULT` (default `jobflow-vault.json`),
written with `0600` permissions. Nothing leaves the machine.

**Can I use it for a team?**
The vault is a single file. Put it in a private repo or shared drive, or run the
REST layer against a database-backed `store.py`. Be aware the file is the unit
of concurrency — two writers can clobber each other.

**The demo shows 2 onsite but only 1 offer — a bug?**
No, and it is the point. One application was rejected *after* its onsite round.
JOBFLOW counts the furthest rung reached, so it stays in the onsite total.

**Why does the response rate say "not measurable yet"?**
Nothing in the vault is older than the 21-day maturity window, so there is no
honest denominator. Reporting `0.0%` would be an immaturity dressed up as a
failure.

**Why is the salary stored as an integer?**
Floats cannot represent money exactly. `0.1 + 0.2 != 0.3` in binary floating
point, and those errors compound across a pipeline. Everything is integer minor
units, rounded half-up, converted exactly once at the boundary.

---

## Limitations

- **Single-writer.** The JSON vault is not safe for concurrent writers.
- **No job-board integration.** It does not scrape listings or auto-apply; you
  log what you did. That is deliberate — it keeps the tool offline and honest.
- **No FX.** Salaries are compared within a currency, never across currencies.
- **No notifications.** It produces a plan when asked; scheduling is your cron's job.
- **Never sends anything.** No email, no messages, no browser automation.

---

## License

MIT — see the repository root.

---

**JOBFLOW** is **Daily Agent #20** in the
[`daily-agents`](https://github.com/Raza077-coder/daily-agents) collection.
Built to be read, modified and trusted.

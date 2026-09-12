# 🔨 FORGE — Workout Coach Agent

**Deterministic strength & workout coaching that runs entirely offline.**

FORGE turns logged training into a clear next action, and explains the rule behind it.
No ML, no API keys, no accounts, no network calls — just a transparent engine that always
produces the same answer for the same input.

```
$ forge log bench 60x8x3
Logged Barbell Bench Press on 2026-09-12 — 3 sets, volume 1440kg

Next session:
  Barbell Bench Press: 3x9-12 @ 60kg
    Hold 60kg and beat your last session (60kgx8). Hit top of range on every
    set and the weight goes up.
```

---

## Table of contents

- [What it does](#what-it-does)
- [Key features](#key-features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Setup & installation](#setup--installation)
- [Usage examples](#usage-examples)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Deployment](#deployment)
- [Live demo](#live-demo)
- [Safety & disclaimer](#safety--disclaimer)

---

## What it does

Most training apps are either a blank spreadsheet or a black box that tells you to
"crush it today". FORGE does the thing a good coach actually does:

1. **Reads your logged work** — every set, with weight and reps.
2. **Applies a rule you can inspect** — double progression, deload-on-stall, plateau
   detection.
3. **Tells you exactly what to do next** — sets, reps, load, and *why*.

It also audits the shape of your training: push/pull balance, quad vs posterior-chain
volume, muscle coverage, weekly tonnage trend, and which lifts have stopped moving.

> **The design bet:** progressive overload is arithmetic, not intuition. Given the same
> logged sets, the correct next prescription is derivable — so it should be deterministic
> code you can check, not a language model you have to trust.

---

## Key features

### 🏋️ Exercise library — 69 movements
Each entry carries the metadata the engine reasons over: primary and secondary muscles,
equipment, movement pattern, compound vs isolation, difficulty, and the smallest real
increment for that equipment (2.5 kg barbell, 5 kg lower-body compound, 0 kg bodyweight).

Search by name, muscle, equipment or pattern:
```bash
forge library --muscle chest
forge library bench
forge library --equipment dumbbell --pattern hinge
```

### 📝 Flexible set logging
One compact spec grammar covers everything people actually type:

| Spec | Meaning |
|---|---|
| `60x8` | one set of 8 at 60 kg |
| `60x8x3` | three sets of 8 at 60 kg |
| `60x8,60x8,60x6` | explicit, mixed reps |
| `bw x10` | bodyweight, 10 reps |
| `bw+10 x5` | bodyweight plus 10 kg added |
| `60x8@8` | set at RPE 8 |

Malformed input raises a specific error naming the offending fragment — a typo never
silently drops work from your history.

### 📈 Double-progression engine
The heart of the coach. Five outcomes, each with a stated reason:

| Situation | Action | Why |
|---|---|---|
| No history | Start at 60–100% of the reference ratio (by experience) | Calibrate from real data, not ego |
| All sets hit the top of the range | **+1 increment**, reset to bottom | That is the overload signal |
| Bodyweight at top of range | Raise the rep range | Bodyweight progresses by reps |
| 3 sessions under the bottom | **Deload 10%** | Rebuild quality reps |
| Estimated 1RM flat for 4+ sessions | Change rep range, add a set | Same lift, different stimulus |
| Otherwise | Repeat the load, chase reps | Volume at fixed load is still progress |

### 🏆 PR detection via estimated 1RM
Uses the Epley formula to estimate one-rep max from ordinary working sets — so strength
progress is trackable **without ever attempting a maximal single**:

$$
\text{e1RM} = w \times \left(1 + \frac{r}{30}\right)
$$

Two record types fire on every log: heaviest-ever top set, and best-ever estimated 1RM.

### 🗓️ Program builder — 5 splits
Deterministic split selection from days available, with real exercises assigned:

| Days | Split |
|---|---|
| 2 | Full Body (2 days) |
| 3 | Full Body (3 days) |
| 4 | Upper / Lower |
| 5 | Push / Pull / Legs (5 days) |
| 6 | Push / Pull / Legs (6 days) |

Respects your equipment list, honours per-muscle weekly set caps, avoids scheduling the
same lift on back-to-back days, and rotates muscle emphasis across repeated sessions
(Push A vs Push B).

### 🔮 Overload projection
Projects the next N weeks if you complete every session as prescribed:

```
week 1 Back Squat 60.0 kg x 3
week 2 Back Squat 60.0 kg x 4
week 3 Back Squat 60.0 kg x 5
week 4 Back Squat 60.0 kg x 6
week 5 Back Squat 65.0 kg x 3   ← earned the increment, reps reset
```

### 📊 Analytics
- **Volume by muscle** — working sets and tonnage, with secondary muscles credited at 0.5
- **Weekly tonnage** — per ISO week, with bodyweight sets reported separately so a high
  pull-up count never looks like lost work
- **Balance report** — push/pull ratio, quad vs posterior chain, neglected rear delts/calves
- **Muscle coverage** — which of 13 major muscles were trained, lightly touched, or missed
- **Streaks & consistency** — consecutive weeks at a session target; the open week never
  breaks a streak
- **Plateau detection** — lifts flat across 4+ sessions

### 🎙️ Four surfaces, one engine
CLI · Python library · REST API (FastAPI) · interactive browser demo — all backed by the
same pure functions, so the numbers always agree.

---

## How it works

```
                    ┌──────────────────────────┐
   forge init ─────▶│  profile (JSON)          │
                    │  bodyweight, experience, │
                    │  goal, equipment         │
                    └────────────┬─────────────┘
                                 │
   forge program ───▶  ┌─────────▼─────────┐
                       │  PROGRAM BUILDER  │  split selection
                       │  program.py       │  exercise assignment
                       └─────────┬─────────┘  volume caps
                                 │
   forge log ───────▶  ┌─────────▼─────────┐
                       │  ENGINE           │  parse sets
                       │  engine.py        │  double progression
                       └─────────┬─────────┘  PR detection
                                 │
   forge report ────▶  ┌─────────▼─────────┐
                       │  ANALYTICS        │  volume, balance,
                       │  analytics.py     │  streaks, plateaus
                       └─────────┬─────────┘
                                 │
                       ┌─────────▼─────────┐
                       │  COACH            │  plain-language output
                       │  coach.py         │  (forge_persona.json)
                       └─────────┬─────────┘
                                 │
        ┌────────────┬───────────┼───────────┬────────────┐
        ▼            ▼           ▼           ▼            ▼
      CLI        Python lib   REST API   Browser      JSON
   cli.py         import      api/       web-live/    --json
```

**Pipeline stages**

1. **Models** (`models.py`) — `Exercise`, `SetEntry`, `LoggedExercise`, `Workout`,
   `Program`. Plain dataclasses; ISO dates throughout.
2. **Library** (`exercises.py`) — the curated 69-movement catalogue plus search/filter.
3. **Storage** (`storage.py`) — a single atomic JSON file. Writes go to a temp file and
   are `os.replace`d into position, so an interrupted save can never corrupt history.
4. **Engine** (`engine.py`) — set-spec parsing, workout merging, double progression, PRs.
5. **Program** (`program.py`) — split selection, slot filling, projection.
6. **Analytics** (`analytics.py`) — pure functions over `List[Workout]`.
7. **Coach** (`coach.py`) — loads `forge_persona.json` and phrases everything.

### Provenance note

This agent was built as Daily Agent #18 in the *Daily Agents* collection. The test
suite and smoke runs caught **six real bugs**, all fixed before shipping:

1. **Increment lookup** — a loop variable name typo broke library construction outright.
2. **Cross-muscle contamination** — isolation slots on upper-body days could be filled by
   *any* isolation movement, which put **Leg Extension on Pull Day**. Fixed by rewriting
   the picker to relax the *pattern* constraint before the *muscle* constraint.
3. **Inverted progression ladder** — the overload projection climbed *down* the rep range
   before adding load (60×10 → 60×9 → 60×8 → 60×7, never getting heavier). Fixed to climb
   *up* the range and then step the load.
4. **Alphabetical exercise selection** — with a full gym the candidate list was sorted by
   name, so **Back Extension beat Romanian Deadlift** for the hinge slot and Chin-Up beat
   Overhead Press for the vertical push. Equipment is now ranked, heaviest-loadable first.
5. **Volume-cap padding** — when a muscle's weekly set cap ran low, remaining slots were
   padded to a token 2 sets, which *both* broke the cap (16 quad sets against a cap of 12
   on a 4-day beginner plan) and prescribed a dose too small to adapt to. Slots that
   cannot be funded properly are now skipped.
6. **"Hold 0kg"** — the coach printed `0kg` for bodyweight work, which is nonsense to read.
   Bodyweight is now named, so a pull-up prescription reads *"Hold bodyweight"*.

Bug 1 in that list is the reason the codebase ships a **Python↔JavaScript parity
harness** (`tools/parity_check.js` + `tools/parity_check.py`). The browser demo
re-implements the progression rules in JavaScript, and duplicated logic drifts. The
harness runs the same inputs through both engines and diffs every field — 16 engine
cases, 5 programmes across 36 sessions, and the whole 69-entry library. Any divergence
fails loudly rather than quietly shipping different advice than the CLI.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.9+ | Standard library only for the core |
| Core engine | **Zero dependencies** | Runs anywhere, forever, no supply chain |
| CLI | `argparse` | No framework needed; ships with Python |
| REST API | FastAPI + Pydantic | Auto-generated OpenAPI docs, serverless-friendly |
| Browser demo | Vanilla JS + WebCrypto-free embedded data | No build step, no `fetch`, works from `file://` |
| Storage | Atomic JSON | Human-readable, diffable, greppable, backup with `cp` |
| Tests | pytest | Fully offline, deterministic |

**The core has no third-party runtime dependencies.** FastAPI is needed only for the
optional REST surface, and pytest only to run the tests.

---

## Setup & installation

### Requirements
- Python 3.9 or newer
- `pip` (only for the optional API/tests)

### Install

```bash
git clone https://github.com/Raza077-coder/daily-agents.git
cd daily-agents/workout-coach-agent

# Option A — install as a package (gives you the `forge` command)
pip install -e .

# Option B — no install at all, run from the folder
python3 -m forge.cli --help

# Optional: REST API + tests
pip install -r requirements.txt
```

### Quick start

```bash
forge init --bodyweight 80 --experience intermediate --goal strength
forge program --days 4
forge today
forge log bench 60x8x3
forge report
```

Try it instantly with a seeded 3-week history:

```bash
forge demo --force
```

---

## Usage examples

### Set up a profile

```bash
# Full gym, strength focus
forge init --bodyweight 80 --experience intermediate --goal strength

# Home setup — dumbbells and bodyweight only
forge init --bodyweight 70 --experience beginner --goal hypertrophy \
  --equipment dumbbell,bodyweight

# Wipe everything and start fresh
forge init --bodyweight 82 --force
```

### Build a programme

```bash
forge program --days 3                          # pick a split automatically
forge program --days 4 --goal hypertrophy       # upper/lower, size focus
forge program --days 6 --preview                # compare without saving
forge program --days 4 --project 8              # show 8 weeks of overload
```

```
$ forge program --days 4 --project 4
Upper / Lower (4 days) · Strength
=================================
Split: Upper / Lower (4 days) · 4 days/week · 8 week block · goal: strength

Upper A (mon)
  Barbell Bench Press          4x3-6 @ 42.5kg, rest 180s
  Barbell Row                  4x3-6 @ 37.5kg, rest 180s
  Dumbbell Shoulder Press      4x3-6 @ 12kg, rest 180s
  Dumbbell Lateral Raise       3x8-12 @ 5kg, rest 90s

Lower A (tue)
  Back Squat                   4x3-6 @ 60kg, rest 180s
  Romanian Deadlift            4x3-6 @ 55kg, rest 180s
  Leg Curl                     3x8-12 @ 20kg, rest 90s
  Standing Calf Raise          3x8-12 @ 40kg, rest 90s
...
```

### Log training

```bash
forge log bench 60x8x3                              # three sets of 8 at 60
forge log squat 80x5x5 --session "Lower A"          # label the session
forge log pullup "bw x10x3" --date 2026-09-10       # backfill a date
forge log bench "60x8,60x8,60x6" --notes "felt heavy"
```

```
$ forge log bench 60x8x3
Logged Barbell Bench Press on 2026-09-12 — 3 sets, volume 1440kg

Next session:
  Barbell Bench Press: 3x9-12 @ 60kg
    Hold 60kg and beat your last session (60kgx8). Hit top of range on every
    set and the weight goes up.
```

### Log a whole session at once

```bash
forge session bench=60x8x3 row=50x10x3 press=35x8x3 --label "Upper A"
```

### Track progress and records

```bash
forge progress bench            # timeline + what to do next
forge progress squat --limit 4  # last four sessions only
forge pr                        # every personal record
forge pr bench                  # one lift
```

```
$ forge progress bench
Barbell Bench Press — 8 logged session(s)
  First: 2026-08-23 — 60kg x 8 (e1RM 76kg)
  Latest: 2026-09-09 — 67.5kg x 8 (e1RM 85.5kg)
  Change: +7.5kg on the bar, +9.5kg on estimated 1RM (improving)

  Next session:
    Barbell Bench Press: 3x10-12 @ 67.5kg
      Hold 67.5kg and beat your last session (67.5kgx8)...
```

### Reports

```bash
forge report                    # last 28 days
forge report --days 90          # a longer window
forge history --limit 10        # raw logged workouts
forge undo today --yes          # remove a day's sessions
```

```
$ forge report
FORGE — training report for 2026-09-12
======================================

Lifetime: 8 sessions · 41,235kg total tonnage · 96 working sets
Last 28 days: 8 sessions, 41,235kg, 96 sets
Weekly tonnage: 5,940kg (0 bodyweight sets excluded from tonnage)
  Weekly tonnage up 4.2% on last week.
Streak: 2 week(s) at 3+ sessions (best 2) — 2 logged this week.
Muscle coverage: 11/13 major muscles trained this window.
  Not trained: calves, rear_delts

Balance:
  [medium] Only 3 rear-delt sets. Face pulls or reverse flyes are cheap
  insurance for shoulder health.

Volume by muscle (working sets, secondary at 0.5):
  back              10.0 sets  [########....] 83% / 12 target
  quads              8.0 sets  [########....] 80% / 10 target
  chest              6.0 sets  [#######.....] 60% / 10 target
  ...
```

### JSON output for scripting

Every command accepts `--json`:

```bash
forge report --json | jq '.streak.current'
forge pr --json | jq '.records[] | {name, best_e1rm}'
forge today --json | jq '.exercises[].advice.action'
```

### Use as a Python library

```python
from forge import ForgeEngine, Store, build_program, summary, Coach

engine = ForgeEngine(Store("~/my-training.json"))
engine.init_profile(bodyweight_kg=80, experience="intermediate", goal="strength")

program = build_program(days_per_week=4, experience="intermediate",
                        goal="strength", bodyweight_kg=80)
engine.store.set_program(program)

engine.log("bench", "60x8x3")
engine.log("squat", "80x5x5")

advice = engine.next_prescription("bench")
print(advice["action"], advice["weight"], advice["reason"])

print(Coach().describe_summary(summary(engine.store.workouts)))
```

### REST API

```bash
uvicorn api.index:app --reload --port 8000
# open http://localhost:8000/docs for interactive OpenAPI docs

curl -s localhost:8000/api/health | jq
curl -s "localhost:8000/api/exercises?muscle=chest" | jq '.count'

curl -s -X POST localhost:8000/api/program \
  -H 'Content-Type: application/json' \
  -d '{"days_per_week":4,"experience":"intermediate","goal":"strength",
       "bodyweight_kg":80,"project_weeks":4}' | jq '.program.name'

curl -s -X POST localhost:8000/api/log \
  -H 'Content-Type: application/json' \
  -d '{"exercise":"bench","sets":"60x8x3"}' | jq '{prs, next: .next.action}'

curl -s "localhost:8000/api/report?days=28" | jq '.balance.findings'
```

**Endpoints**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Status, library size, disclaimer |
| `GET` | `/api/exercises` | Search/filter the library |
| `GET` | `/api/splits` | Available splits |
| `POST`/`GET` | `/api/profile` | Set / read the athlete profile |
| `POST`/`GET` | `/api/program` | Build / read the programme |
| `GET` | `/api/program/projection` | Week-by-week overload projection |
| `GET` | `/api/today` | Today's session with per-lift advice |
| `POST` | `/api/log` | Log an exercise |
| `POST` | `/api/session` | Log a whole session |
| `GET` | `/api/progress/{exercise}` | Per-lift timeline |
| `GET` | `/api/prs` | Personal records |
| `GET` | `/api/next/{exercise}` | Next prescription only |
| `GET` | `/api/report` | Full analytics summary |
| `GET` | `/api/history` | Logged workouts |
| `DELETE` | `/api/workout/{date}` | Delete a day |
| `POST` | `/api/demo` | Seed 3 weeks of history |
| `GET` | `/api/export` | Dump the whole data file |

---

## Configuration

### `forge_persona.json` — the agent's voice

All phrasing lives in one file so the CLI, API and browser sound like the same coach.
Edit it and every surface changes; no code changes needed.

```json
{
  "name": "FORGE",
  "tagline": "Deterministic strength coaching — no hype, no guessing.",
  "voice": {
    "tone": "direct, calm, evidence-first",
    "style_rules": ["Lead with the number, then the reason.", "One recommendation per lift."],
    "banned_phrases": ["no pain no gain", "listen to your body"]
  },
  "principles": ["Progressive overload is the only non-negotiable variable."],
  "safety": { "disclaimer": "...", "red_flag_phrases": ["sharp pain", "numbness"] },
  "messages": {
    "deload": "Deload queued for {name}. Better to step back for one session than stall for five.",
    "plateau": "Plateau detected on {name} across {sessions} sessions."
  }
}
```

- **`voice.banned_phrases`** — vague advice the coach must never emit
- **`messages.*`** — `{placeholder}` templates for each coaching situation
- **`safety.disclaimer`** — surfaced with every programme and report
- **`safety.red_flag_phrases`** — phrases that signal stop-and-see-a-professional

`forge_persona.md` is the human-readable system-prompt form of the same content.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FORGE_DATA` | `~/.forge/training.json` | Where the training file lives |

### Tuning the engine

Constants worth knowing (all in `forge/engine.py` and `forge/program.py`):

| Constant | Default | Meaning |
|---|---|---|
| `STALL_WINDOW` | `3` | Sessions under target before a deload |
| `PLATEAU_SESSIONS` | `4` | Flat e1RM sessions before changing stimulus |
| `SECONDARY_CREDIT` | `0.5` | Set credit for secondary muscles |
| `VOLUME_TARGETS` | per-muscle | Weekly set targets for the balance report |
| `WEEKLY_SET_CAP` | 12/18/24 | Weekly cap per muscle by experience |
| `GOAL_SCHEMES` | per-goal | Sets, rep ranges and rest per goal |
| `EXPERIENCE_WEEKS` | `8` | Default block length |

---

## Project structure

```
workout-coach-agent/
├── forge/                      # the engine (stdlib only)
│   ├── __init__.py             # public API surface
│   ├── models.py               # dataclasses + Epley e1RM
│   ├── exercises.py            # 69-exercise library, search, filter
│   ├── storage.py              # atomic JSON persistence
│   ├── engine.py               # set parsing, progression, PRs
│   ├── program.py              # 5 splits, slot filling, projection
│   ├── analytics.py            # volume, balance, streaks, plateaus
│   ├── coach.py                # persona-driven phrasing
│   └── cli.py                  # 12 subcommands
├── api/
│   └── index.py                # FastAPI wrapper (Vercel-ready)
├── web-live/                   # self-contained browser demo
│   ├── index.html
│   ├── style.css
│   ├── forge-data.js           # generated: embedded library
│   ├── forge-engine.js         # JS port of the progression rules
│   └── app.js
├── tools/
│   └── build_web_data.py       # regenerates forge-data.js from Python
├── forge_persona.json          # runtime persona (loaded by coach.py)
├── forge_persona.md            # persona as a system prompt
├── pyproject.toml              # packaging + `forge` entry point
├── requirements.txt            # optional API/test deps
├── vercel.json                 # serverless config
└── README.md
```

---

## Deployment

### GitHub Pages (live demo — no backend)

The browser demo is fully client-side: all data is embedded in `forge-data.js`, there are
no `fetch`/XHR calls, and it works from `file://` as well as over HTTP.

```
https://raza077-coder.github.io/daily-agents/workout-coach-agent/web-live/
```

Repo Settings → Pages → Source: `main` branch, `/` (root). The demo needs no build step.

### Vercel (REST API)

`vercel.json` and `api/index.py` are ready:

```bash
npm i -g vercel
vercel login
cd workout-coach-agent
vercel --prod
```

Or import the repo at [vercel.com/new](https://vercel.com/new) and set
**Root Directory = `workout-coach-agent`**.

> ⚠️ **Serverless storage caveat.** Serverless instances are ephemeral, so the REST API
> keeps its training file in temp storage. Data does not survive a cold start — use
> `GET /api/export` to pull it out, or run the CLI locally for durable logging.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Static hosting

`web-live/` can go on any static host (Netlify, Cloudflare Pages, S3) — it is five
files with no build step and no dependencies.

---

## Live demo

**https://raza077-coder.github.io/daily-agents/workout-coach-agent/web-live/**

The demo lets you:
- browse and filter all 69 exercises
- enter your last session on a lift and get the exact next prescription
- view a full sample programme with starting loads
- see the week-by-week overload projection

---

## Safety & disclaimer

> **FORGE is an informational fitness tool. It is not medical advice.** Consult a
> qualified physician or physiotherapist before starting a new training programme,
> especially if you have an injury, are pregnant, or have a cardiovascular condition.

Hard rules the engine enforces:

- Never prescribes a maximal single-rep attempt
- Never encourages training through sharp, radiating or numbing pain
- Caps beginner starting loads at 60% of the reference ratio
- Surfaces this disclaimer with every programme and report

If you experience sharp pain, radiating pain, numbness, chest pain or dizziness, stop
training and see a qualified professional. No programme is worth an injury.

---

<p align="center">
  <strong>Daily Agent #18</strong> · part of the
  <a href="https://github.com/Raza077-coder/daily-agents">Daily Agents</a> collection
  <br>
  <sub>Deterministic · offline · tested · documented</sub>
</p>
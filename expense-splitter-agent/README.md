# 🧾 SplitKit — Shared Expense Splitter & Settle-Up Agent

> **Shared costs, settled exactly.** A deterministic, offline-first agent that splits group expenses to the cent and reduces the whole ledger to the fewest possible transfers.

[![Tests](https://img.shields.io/badge/tests-354%20passing-brightgreen)](#testing)
[![Parity](https://img.shields.io/badge/JS%2FPython%20parity-verified-blue)](#python--javascript-parity)
[![Live Demo](https://img.shields.io/badge/live%20demo-GitHub%20Pages-5b8cff)](https://raza077-coder.github.io/daily-agents/expense-splitter-agent/web-live/)
[![Offline](https://img.shields.io/badge/network%20calls-zero-lightgrey)](#design-principles)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**🚀 Live demo:** https://raza077-coder.github.io/daily-agents/expense-splitter-agent/web-live/

---

## Overview

Splitting a bill sounds trivial and almost never is. `100.00` split three ways is `33.3333…` each — round each share and you lose a cent; round the total and you invent one. Then someone pays for an item only two people ate, someone else covers a tip for everyone, and six weeks later nobody remembers who owes what.

SplitKit is built around one rule: **money is never a float**. Every amount is an integer count of minor units (cents, fils, yen), and every split is allocated with the largest-remainder method so the parts always sum to the total *exactly*. Given a set of balances that provably sums to zero, it then computes the minimum number of payments that clears the book — and **tells you whether that minimum is proven or merely a good heuristic**.

It's the kind of arithmetic a spreadsheet gets subtly wrong, so it's the kind worth putting behind tests.

---

## Key Features

### 💰 Exact integer money
- Amounts live as **integer minor units**, parsed from strings once at the boundary
- Decimal places follow the currency: 2 for most, **3 for KWD/BHD/OMR/TND**, **0 for JPY/KRW/VND**
- 60+ currencies with correct exponents and symbols
- Refuses float input outright rather than rounding it quietly

### 🧮 Six split modes

| Mode | What it does | Checked constraint |
|---|---|---|
| `equal` | Evenly across participants (default: everyone) | parts sum to total |
| `exact` | A fixed amount per person | amounts must sum to the total |
| `shares` | Weighted parts — `2:1:1` is a half / quarter / quarter | exact rational allocation |
| `percent` | Percentages per person | must total **exactly** 100% |
| `itemized` | Assign each line item, then spread tax & tip | items + tax + tip = total |
| `adjustment` | Even base plus signed tweaks | tweaks must net to zero |

### 🤝 Minimum-transfer settle-up
- **Greedy** solver: largest creditor / largest debtor, always valid
- **Optimal** solver: partitions members into independent zero-sum groups via bitmask DP, then settles each internally — provably minimal at *k−1* transfers for a *k*-group
- Reports the **information-theoretic lower bound** `max(#creditors, #debtors)` and states plainly whether the plan *achieves* it
- Falls back to the greedy plan above 20 active members, and **says so** instead of pretending

### 📊 Reporting
- Balance sheet: paid / share / net per member, with a zero-sum assertion
- Category breakdown with proportional shares
- Text, Markdown and JSON outputs — the JSON is pipeline-friendly

### 🔀 Four surfaces, one engine

| Surface | Entry point | Use it for |
|---|---|---|
| **CLI** | `splitkit <command>` | Everyday use from a terminal |
| **Library** | `from splitkit.engine import SplitKit` | Embedding in your own code |
| **REST API** | `api/index.py` (FastAPI) | Serverless / Vercel deployment |
| **Browser HUD** | `web-live/` | Zero-install live demo |

---

## How It Works

```
                       ┌──────────────────────┐
  amounts as strings ─▶│  money.parse_amount  │──▶ integer minor units
                       └──────────────────────┘
                                  │
                       ┌──────────▼───────────┐
                       │   splits.resolve     │  equal · exact · shares
                       │  (largest remainder) │  percent · itemized · adjustment
                       └──────────┬───────────┘
                                  │  shares sum == expense amount  (asserted)
                       ┌──────────▼───────────┐
                       │       ledger         │──▶ net per member,
                       │  paid − owed − settled│    asserted to sum to 0
                       └──────────┬───────────┘
                                  │
                       ┌──────────▼───────────┐
                       │       settle         │  greedy ─┐
                       │  zero-sum partitions │  optimal ┘─▶ fewest transfers
                       └──────────┬───────────┘
                                  │
                       ┌──────────▼───────────┐
                       │ verify_plan (replay) │  independent re-check
                       └──────────────────────┘
```

### The cent that has to go somewhere

`100.00` across three people is `3333.333…` cents each. Rounding each share independently gives `3333 × 3 = 9999` — a cent vanished. SplitKit allocates the remainder to the largest fractional parts, ties broken by member order:

```
allocate(10000, [1, 1, 1])  ->  [3334, 3333, 3333]   sum = 10000  ✓
allocate(7,     [1, 1, 1])  ->  [3, 2, 2]            sum = 7      ✓
```

### Why "proven minimum" is not just a claim

A single payment can zero out at most one person's balance, so any plan needs at least `max(#creditors, #debtors)` transfers. SplitKit computes that bound, computes both plans, and reports whether the one you're looking at hits it. `verify_plan` then **replays the plan independently** and asserts every balance lands on zero — including refusing an empty plan against an unbalanced book.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.9+** | Stdlib-only engine — no runtime dependencies |
| Money | `fractions.Fraction` | Exact rational allocation, no float error |
| API | **FastAPI** + Pydantic | Typed schemas, free OpenAPI docs |
| CLI | `argparse` | Zero-dependency, scriptable |
| Storage | Atomic JSON writes | Human-readable, diff-able, crash-safe |
| Browser HUD | **Vanilla JS + BigInt** | No framework, no CDN, no build step |
| Tests | `pytest` | 354 tests, fully offline |

**The engine and CLI need nothing but the standard library.** FastAPI is only required for the REST surface, and Node only for the parity harness.

---

## Setup & Installation

### Requirements
- Python **3.9+** (3.11+ recommended)
- *Optional:* Node 18+ for the parity harness

### Install

```bash
# Core engine + CLI — no dependencies at all
git clone https://github.com/Raza077-coder/daily-agents
cd daily-agents/expense-splitter-agent
python3 -m splitkit.cli --help

# Or install as a package
pip install -e .

# For the REST API
pip install -r requirements.txt

# For the test suite
pip install pytest fastapi httpx
```

### Verify your install

```bash
python3 -m pytest tests/ -q          # 354 passed
python3 tools/parity_check.py        # PARITY PASS - 34 checks
python3 -m splitkit.cli demo         # the worked example
```

---

## Usage Examples

### Create a group and add expenses

```bash
$ python3 -m splitkit.cli init "Goa Weekend" --members Ali,Sara,Bilal --currency USD
Created “Goa Weekend” (USD) at .splitkit/group.json
  members: Ali, Sara, Bilal

$ python3 -m splitkit.cli add "Beach hut" 180.00 --paid-by ali --category lodging
Added “Beach hut” 180.00 paid by Ali
  split evenly between everyone
    Ali         60.00
    Sara        60.00
    Bilal       60.00
  shares sum exactly to 180.00 ✓
```

### The three-way split that proves the point

```bash
$ python3 -m splitkit.cli add "Group dinner" 100.00 --paid-by sara --category food
Added “Group dinner” 100.00 paid by Sara
  split evenly between everyone
    Ali         33.34      ← gets the leftover cent (largest remainder)
    Sara        33.33
    Bilal       33.33
  shares sum exactly to 100.00 ✓
```

### Weighted, exact and itemized splits

```bash
# Ali ate a double portion
python3 -m splitkit.cli add "Seafood platter" 92.40 --paid-by ali \
    --split shares --shares ali=2,sara=1,bilal=1

# Fixed amounts that must add up
python3 -m splitkit.cli add "Airport cab" 24.00 --paid-by bilal \
    --split exact --amounts ali=12,sara=6,bilal=6

# A receipt: items, then tax and tip spread proportionally
python3 -m splitkit.cli add "Groceries" 58.30 --paid-by sara \
    --split itemized \
    --item "Snacks:14.00:ali,sara" \
    --item "Water:18.30:ali,sara,bilal" \
    --item "Sunscreen:12.00:bilal" \
    --tax 6.00 --tip 8.00
```

### A split that gets refused (and why that matters)

```bash
$ python3 -m splitkit.cli add "Cab" 20.00 --paid-by ali --split exact --amounts ali=12,sara=6
error: exact amounts sum to 18.00 but the expense is 20.00 — off by +2.00.
       Adjust the amounts (or use 'adjustment' mode) so they match exactly.
```

A silently-rounded ledger is worse than a rejected one.

### Balances and settle-up

```bash
$ python3 -m splitkit.cli balances
Goa Weekend — balances (USD)
Member      Paid     Share      Net
------  --------  --------  -------
Ali       317.40    192.82   +124.58  owed
Sara      158.30    145.68    +12.62  owed
Bilal      24.07    161.27   -137.20  owes

Sum of net positions: 0.00  (must be 0)

$ python3 -m splitkit.cli settle
Bilal  →  Ali   124.58
Bilal  →  Sara   12.62

2 transfers (proven minimum) clears the whole book.

$ python3 -m splitkit.cli verify
Ledger balances         : yes (sum 0.00)
Settle plan clears it   : yes
Transfers in plan       : 2
Theoretical minimum     : at least 2
Plan is proven minimum  : yes
```

### Reports

```bash
python3 -m splitkit.cli report                 # text digest
python3 -m splitkit.cli report --format markdown > trip.md
python3 -m splitkit.cli report --format json | jq '.settle_plan.transfers'
python3 -m splitkit.cli ask "where did the money go"
python3 -m splitkit.cli demo                   # seeded 7-expense example
```

### As a library

```python
from splitkit.engine import SplitKit

kit = SplitKit.from_file("goa.json")
kit.add_expense("Group dinner", "100.00", paid_by="sara", split={"mode": "equal"})

for row in kit.balance_table():
    print(row["name"], row["balance_display"], row["state"])

plan = kit.settle(strategy="optimal")
print(plan["transfer_count"], "transfers", "proven:", plan["optimal_feasible"])
for t in plan["readable"]:
    print(" ", t)

assert kit.verify()["settle_plan_valid"]
```

### REST API

```bash
uvicorn api.index:app --reload

curl -X POST localhost:8000/demo                                  # load the example
curl localhost:8000/balances
curl "localhost:8000/settle?strategy=optimal"
curl localhost:8000/report?format=markdown
curl -X POST localhost:8000/expense -H 'content-type: application/json' \
  -d '{"description":"Dinner","amount":"100.00","paid_by":"sara",
       "split":{"mode":"shares","shares":{"ali":2,"sara":1,"bilal":1}}}'
curl -X POST localhost:8000/ask -d '{"question":"who owes what"}'
```

Interactive docs are served at `/docs` — the OpenAPI schema is generated from the same Pydantic models that validate your input.

---

## Configuration

Settings resolve in this order (later wins): **built-in defaults → `splitkit.json` → `.splitkitrc.json` → `.splitkit.json` → environment variables**.

`splitkit.json`:

```json
{
  "default_currency": "USD",
  "default_split": "equal",
  "min_transfer": "0",
  "group_file": ".splitkit/group.json",
  "rounding": "largest_remainder",
  "settle_strategy": "optimal",
  "date_format": "%Y-%m-%d",
  "category_icons": true,
  "max_members": 100
}
```

| Key | Env var | Purpose |
|---|---|---|
| `default_currency` | `SPLITKIT_CURRENCY` | Currency for new groups |
| `default_split` | `SPLITKIT_SPLIT` | Split mode when you omit one |
| `min_transfer` | `SPLITKIT_MIN_TRANSFER` | Threshold below which transfers are flagged |
| `group_file` | `SPLITKIT_GROUP_FILE` | Path to the ledger |
| `settle_strategy` | `SPLITKIT_SETTLE_STRATEGY` | `optimal` · `greedy` · `compare` |
| `max_members` | `SPLITKIT_MAX_MEMBERS` | Refuse absurd group sizes early |

`persona.json` holds the coach voice for `ask` replies, so tone is editable without touching code.

---

## Design Principles

1. **No floats, ever.** Money is an integer count of minor units. Float input is rejected, not coerced.
2. **Conservation is asserted, not hoped for.** Every split is checked to sum to its expense; every ledger is checked to sum to zero; every settle plan is replayed independently.
3. **Refuse rather than guess.** A mismatched exact split is an error, not a rounding opportunity.
4. **Say what you can't prove.** Above the DP limit, the plan is labelled greedy instead of being presented as minimal.
5. **Zero network calls.** No telemetry, no phone-home, no runtime fetches. Your ledger stays local — the browser demo makes this auditable.
6. **Deterministic.** Same input, same output, always — ties broken by a fixed rule rather than by hash order.

---

## Testing

```bash
python3 -m pytest tests/ -q                  # 354 tests
python3 -m pytest tests/ -q -k money         # one module
python3 -m pytest tests/ --cov=splitkit      # with coverage
```

| Module | Focus |
|---|---|
| `test_money.py` | Parsing, formatting, allocation, exponent handling |
| `test_splits.py` | All six modes and every validation rule |
| `test_ledger.py` | Balances, zero-sum invariant, categories |
| `test_settle.py` | Optimality, plan verification, thresholds |
| `test_api.py` | Every endpoint, including error paths |
| `test_web_parity.py` | Generated data matches the engine; no network in assets |

### Python → JavaScript parity

The browser demo re-implements the money rules in JavaScript. Duplicated logic drifts, and a demo that quietly disagrees with the CLI is worse than no demo — so a harness runs both engines over identical inputs:

```bash
$ python3 tools/parity_check.py
PARITY PASS - 34 checks, Python and JavaScript agree exactly
  allocations : 10
  currencies  : 3 (USD, KWD, JPY) - balances + settle plans
  formats     : 18
```

It covers all three currency exponent classes (2, 3 and 0 decimals), negative totals, prime-count groups, and sub-unit amounts.

---

## Deployment

### Option 1 — Static (recommended for the demo)

The browser HUD depends on nothing but its own files. Serve `web-live/` from any static host:

```bash
python3 -m http.server 8000 --directory web-live
```

It also works straight from `file://`, because the data is embedded rather than fetched.

### Option 2 — Vercel (REST API)

`vercel.json` and `api/index.py` are ready:

```bash
npm i -g vercel && vercel login
cd expense-splitter-agent && vercel --prod
```

Or import the repo and set **Root Directory = `expense-splitter-agent`**.

> **⚠️ Caveat worth knowing:** serverless filesystems are ephemeral, so a group written to `/tmp` will not survive a cold start. `GET /api/export` exists for that reason, but for durable multi-request ledgers point `SPLITKIT_GROUP_FILE` at a mounted volume or a real database. The CLI and library have no such limitation.

### Option 3 — Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Option 4 — CI gate

`report --format json` is designed for pipelines:

```yaml
- run: python3 -m splitkit.cli verify --json | jq -e '.balanced and .settle_plan_valid'
```

---

## Project Structure

```
expense-splitter-agent/
├── splitkit/
│   ├── money.py        # integer minor units, rational allocation
│   ├── models.py       # members, expenses, groups
│   ├── splits.py       # the six split modes + validation
│   ├── ledger.py       # balances and the zero-sum invariant
│   ├── settle.py       # greedy + optimal settle-up, plan verification
│   ├── reports.py      # text / markdown / JSON renderers
│   ├── engine.py       # the facade everything else calls
│   ├── storage.py      # atomic JSON persistence
│   ├── config.py       # layered settings resolution
│   ├── demo.py         # the seeded worked example
│   └── cli.py          # argparse front end
├── api/index.py        # FastAPI surface
├── web-live/           # offline browser HUD
│   ├── index.html
│   ├── splitkit-engine.js
│   ├── demo-data.js    # generated by tools/build_web_data.py
│   ├── app.js
│   └── style.css
├── tools/
│   ├── parity_check.py
│   └── build_web_data.py
├── tests/              # 354 tests
├── AGENT.md            # system prompt / agent configuration
├── persona.json
├── splitkit.json
└── vercel.json
```

---

## Limitations

- **Single currency per group.** Cross-currency expenses need an explicit FX rate and are out of scope.
- **Optimal settle-up is exponential in group size**, so it switches to greedy above ~20 active members. It reports which solver produced the plan.
- **No user accounts or sync.** The file *is* the ledger; share it however you like.
- **`min_transfer` flags, it does not erase.** A small debt between two specific people genuinely has to be paid by those two — the plan stays exact and notes the small transfers instead of hiding them.

---

## FAQ

**Why not just use a card-splitting app?** Most work fine — until someone wants the arithmetic to be inspectable, offline, scriptable, or embedded in their own tool. SplitKit is the engine, not the app.

**Can it get the settle-up wrong?** It reports `optimal_feasible` and re-replays the plan through an independent verifier. If it can't prove minimality, it says so.

**Is my ledger sent anywhere?** No. The engine makes zero network calls, and `test_web_parity.py` asserts the browser assets contain no `fetch`/`XHR`/`WebSocket`/`sendBeacon`.

---

## License

MIT — see [LICENSE](LICENSE).

---

*Daily Agent #19. Deterministic, offline, integer-exact money.*

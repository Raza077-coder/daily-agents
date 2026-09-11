# 🥕 PANTRY — Recipe & Meal Planner Agent

**Tell it what's in your kitchen. It tells you what to cook, what to buy, and what to use up first.**

PANTRY is a deterministic, **fully offline** meal-planning agent. Feed it a pantry —
the things actually sitting in your cupboard — and it ranks every recipe in its
library by how completely you can already make it, builds a multi-day meal plan,
and produces a shopping list for only the gaps, grouped by supermarket aisle.

No API keys. No network calls. No LLM. Same input, same plan, every time.

```
$ pantry cook --demo

🧑‍🍳 WHAT CAN I COOK?
==============================================
 1. Spaghetti Aglio e Olio — ██████████ 100% · ready to cook
 2. Shakshuka             — ██████████ 100% · ready to cook
 3. Stuffed Bell Peppers  — █████████░  86% · missing 0
 4. Roasted Tomato Soup   — ████████░░  80% · missing 0
 5. Banana Pancakes       — █████████░  86% · missing 1
      buy: banana
----------------------------------------------
 2 of 33 recipes need no shopping
```

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Installation](#installation)
- [Usage](#usage)
  - [CLI](#cli)
  - [Python library](#python-library)
  - [REST API](#rest-api)
  - [Browser HUD](#browser-hud)
- [Configuration](#configuration)
- [Data formats](#data-formats)
- [Testing](#testing)
- [Deployment](#deployment)
- [Design decisions & honest limitations](#design-decisions--honest-limitations)
- [License](#license)

---

## Why this exists

Most "what can I cook" tools answer a different question than the one you asked.
They search for recipes *containing* an ingredient, then hand you a list where
every single option still needs a trip to the shop. PANTRY answers the question
you actually meant: **given what I have right now, what is possible?**

Three things make that hard, and PANTRY takes a position on each:

| Hard part | PANTRY's position |
|---|---|
| `200 g flour` vs `1 cup flour` vs `flour` | Convert everything to a real base unit (grams / millilitres / pieces) and **never add incompatible units together**. If it can't be converted, say so. |
| `6 eggs` vs `2 eggs` | Track *amounts*, not just presence — a recipe is only "covered" if you have **enough**. |
| "I have some rice" | An unquantified pantry entry means "present, amount unspecified" and satisfies any quantity — because that's what a human means by it. |

---

## Features

| Feature | What it does |
|---|---|
| 🥕 **Free-text pantry** | Type `200 g spaghetti`, `6 eggs`, `olive oil`, `3 clove garlic` — the parser understands amounts, fractions (`1 1/2 cups`), and bare names. |
| 🧠 **Ingredient canonicalisation** | `scallions` = `spring onion`, `aubergine` = `eggplant`, `all-purpose flour` = `flour`. ~90 safe aliases, plus singularisation. |
| 📏 **Real unit maths** | g/kg/oz/lb, ml/l/cups/tbsp/tsp/fl oz, and countable units. Cross-dimension comparisons are reported, never guessed. |
| ✅ **Amount-aware matching** | Full match / partial (not enough) / missing / covered-by-substitute — four distinct states, each reported. |
| 🔁 **Declared substitutes** | A recipe can name a valid swap (`butter` ← `olive oil`). Only used when the real thing is absent. |
| 🗓 **Meal planner** | Multi-day, multi-slot (breakfast/lunch/dinner), no repeats, deterministic ordering. |
| 🌱 **Leftovers mode** | A plan containing **only** meals that need zero shopping. Returns fewer meals rather than sneaking shopping onto the list. |
| 🛒 **Shopping list** | Merges duplicate needs across recipes, subtracts what you have, groups by aisle, suppresses staples (salt/pepper/water). |
| ⏳ **Use-it-up** | Mark items as expiring; the planner prioritises recipes that consume them. |
| 🎨 **4 surfaces, one engine** | CLI · Python library · FastAPI REST · zero-install browser HUD. |
| 🔒 **Offline & private** | The engine is standard-library-only. Your pantry never leaves your machine. |

---

## How it works

```
                      ┌──────────────────────┐
   pantry lines ─────▶│  units.split_amount  │  "200 g spaghetti"
   "200 g spaghetti"  │  ─────────────────── │  → qty 200, unit g, name "spaghetti"
   "6 eggs"           │  known-unit only     │  → qty 6,   unit "", name "eggs"
   "olive oil"        └──────────┬───────────┘
                                 ▼
                      ┌──────────────────────┐
                      │  canonical_name      │  "Spring Onions" → "spring onion"
                      │  aliases + plurals   │  "Eggs" → "egg"
                      └──────────┬───────────┘
                                 ▼
   recipes.json ────▶ ┌──────────────────────┐
   (33 recipes)       │  match_recipe        │  per ingredient:
                      │  ─────────────────── │   have | partial | missing | sub
                      │  staples → always ok │   (unit mismatch ⇒ partial + reason)
                      └──────────┬───────────┘
                                 ▼
                      ┌──────────────────────┐
                      │  Library.rank        │   cookable first, then fewest gaps,
                      │  deterministic sort  │   then expiring-first, then id
                      └──────────┬───────────┘
                    ┌────────────┴────────────┐
                    ▼                         ▼
         ┌────────────────────┐    ┌────────────────────────┐
         │  MealPlanner.plan  │    │  build_shopping_list   │
         │  days × slots      │───▶│  merge → subtract      │
         │  no repeats        │    │  → group by aisle      │
         └────────────────────┘    └────────────────────────┘
```

**Determinism.** Nothing samples a random number. The planner's tie-breaks all
end at the recipe `id`, so running the same pantry twice produces a byte-identical
plan. This is asserted in the test suite.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Core engine | **Python 3.9+, standard library only** | Zero dependencies means it runs anywhere, including air-gapped machines. |
| Data | **Bundled JSON** (`pantry/data/`) | Recipes are data, not code — swap in your own library without touching Python. |
| CLI | `argparse` | No install friction; `--json` on every command for pipelines. |
| REST API | **FastAPI + Pydantic** | Optional extra. Auto-generated OpenAPI docs at `/docs`. |
| Browser HUD | **Vanilla JS** (no framework, no build) | An 880-line faithful port of the Python engine. No bundler, no CDN, no tracker. |
| Tests | **pytest** | 245 tests, fully offline. |

---

## Installation

### Core engine (no dependencies)

```bash
git clone https://github.com/Raza077-coder/daily-agents
cd daily-agents/recipe-planner-agent

# Option A — just run it (no install needed)
python3 -m pantry.cli demo

# Option B — install the `pantry` command + the REST extras
pip install -e ".[api,dev]"
pantry demo
```

Python 3.9 or newer. The core engine imports **nothing** outside the standard library.

### Browser HUD

```bash
cd web-live
python3 -m http.server 8000
# → http://localhost:8000
```

---

## Usage

### CLI

```bash
# Guided four-step tour on the bundled sample pantry
pantry demo

# What can I cook right now?
pantry cook --demo --limit 8
pantry cook --pantry my_pantry.txt --max-minutes 25 --vegetarian
pantry cook --pantry my_pantry.txt --only-cookable --json

# Plan the week
pantry plan --pantry my_pantry.txt --days 5 --slots dinner
pantry plan --pantry my_pantry.txt --days 3 --slots breakfast,lunch,dinner
pantry plan --pantry my_pantry.txt --days 7 --leftovers-only   # zero shopping

# Shopping list
pantry shop --pantry my_pantry.txt --days 5
pantry shop --pantry my_pantry.txt --days 5 --json

# Browse
pantry library --stats
pantry search chickpea
pantry recipe shakshuka
pantry recipe shakshuka --servings 6

# Manage the pantry
pantry pantry list    --pantry my_pantry.txt
pantry pantry add     --pantry my_pantry.txt "500 g rice" "2 piece avocado"
pantry pantry add     --pantry my_pantry.txt --expires-in 2 "200 g spinach"
pantry pantry remove  --pantry my_pantry.txt rice
pantry pantry expiring --pantry my_pantry.txt --within-days 3

# Utilities
pantry seed --output my_pantry.txt     # write a starter pantry file
pantry stats --demo
pantry profile
```

Add `--json` to any command for machine-readable output. Exit codes: `0` success,
`1` error (e.g. missing pantry file), `3` not found (unknown recipe / no search hits).

### Python library

```python
from pantry import PantryEngine
from pantry.engine import sample_pantry

engine = PantryEngine()
engine.load_pantry(sample_pantry())

# What can I cook?
for match in engine.cook_now(limit=5):
    print(f"{match.recipe.name:<32} {match.coverage:.0%}  missing={[i.name for i in match.missing]}")

# Plan five dinners and get the shopping list
plan = engine.plan_week(days=5, slots=("dinner",))
print(plan["summary"])

shopping = engine.shopping_list(days=5, slots=("dinner",))
for group in shopping["aisles"]:
    print(group["aisle"])
    for item in group["items"]:
        print("  [ ]", item["display"])

# Use-it-up: prioritise what's about to turn
engine.set_expiry("spinach", 1)
print(engine.expiring(within_days=3))
```

### REST API

```bash
uvicorn pantry.api:app --reload --port 8000
# interactive docs: http://127.0.0.1:8000/docs
```

```bash
curl -s localhost:8000/health

curl -s -X POST localhost:8000/cook \
  -H 'content-type: application/json' \
  -d '{"pantry":["400 g spaghetti","4 clove garlic","200 ml olive oil"],"limit":3}'

curl -s -X POST localhost:8000/plan \
  -H 'content-type: application/json' \
  -d '{"pantry":["400 g spaghetti","200 g penne"],"days":5,"slots":["dinner"]}'

curl -s -X POST localhost:8000/shop \
  -H 'content-type: application/json' \
  -d '{"pantry":["400 g spaghetti"],"days":3}'

curl -s -X POST localhost:8000/pantry/parse \
  -H 'content-type: application/json' \
  -d '{"pantry":["200 g spaghetti","6 eggs","olive oil"]}'
```

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness + recipe count |
| `GET` | `/routes` | Machine-readable API index |
| `GET` | `/library` | Library stats + recipe list (filters: `cuisine`, `vegetarian`) |
| `GET` | `/profile` | Household profile |
| `GET` | `/demo` | Full tour payload on the sample pantry |
| `POST` | `/cook` | Rank recipes against a pantry |
| `POST` | `/plan` | Multi-day meal plan |
| `POST` | `/shop` | Shopping list for a plan |
| `POST` | `/search` | Keyword search |
| `GET` | `/recipe/{id}` | One recipe (optional `?servings=`) |
| `POST` | `/recipe/{id}/scale` | Rescale a recipe |
| `POST` | `/pantry/parse` | Normalise free-text pantry lines only |

Every request carries its pantry inline — **no server-side session, nothing persisted.**

### Browser HUD

Zero-install, works on any static host:

```bash
cd web-live && python3 -m http.server 8000
```

The page runs the JS port of the engine entirely client-side: add pantry items,
watch the "cook now" list re-rank, plan the week, and export the shopping list
as plain text or JSON. Nothing is uploaded.

---

## Configuration

### Household profile — `pantry/data/profile.json`

Read by the CLI and API so plans match your kitchen. Edit freely.

```json
{
  "household": { "label": "two adults, one toddler", "adults": 2, "children": 1,
                 "default_servings": 3 },
  "diet":      { "style": "omnivore", "notes": "No pork." },
  "allergies": [],
  "dislikes":  ["olives"],
  "preferences": ["30 minutes or less on weeknights", "one-pot meals preferred"],
  "week":      { "cook_days": ["monday","tuesday","wednesday","thursday","sunday"],
                 "shop_days": ["saturday"], "busy_nights": ["wednesday","thursday"] },
  "budget":    { "currency": "PKR", "weekly_target": 8000 }
}
```

Add a `"staples": ["salt","pepper"]` key to override which items are assumed
always on hand (default: `salt`, `black pepper`, `water`).

### Engine defaults

| Constant | Location | Default | Meaning |
|---|---|---|---|
| `DEFAULT_STAPLES` | `pantry/models.py` | salt, black pepper, water | Never written to a shopping list |
| `MAX_PER_DAY` | `pantry/planner.py` | `3` | Max cooked meals per day in a plan |
| `SLOT_TAGS` | `pantry/planner.py` | — | Which recipe tags suit each meal slot |
| `AISLE_ORDER` / `_AISLE_KEYWORDS` | `pantry/aisles.py` | — | Shopping-list grouping |
| `_INGREDIENT_ALIASES` | `pantry/units.py` | ~90 entries | Safe equivalences only |
| `_UNITS` | `pantry/units.py` | — | Unit → dimension + conversion factor |

---

## Data formats

### Pantry file (one item per line, `#` for comments)

```
# a leading number + unit means "I have this much"
200 g spaghetti
6 piece egg

# a bare name means "I have some" — satisfies any quantity
olive oil
salt
```

### Recipe

```json
{
  "id": "shakshuka",
  "name": "Shakshuka",
  "cuisine": "middle-eastern",
  "servings": 2,
  "minutes": 30,
  "difficulty": "easy",
  "tags": ["breakfast", "dinner", "vegetarian", "one-pot", "high-protein"],
  "ingredients": [
    { "name": "egg", "quantity": 4, "unit": "piece" },
    { "name": "canned tomato", "quantity": 400, "unit": "g" },
    { "name": "feta", "quantity": 60, "unit": "g", "optional": true },
    { "name": "butter", "quantity": 20, "unit": "g", "substitutes": ["olive oil"] }
  ],
  "steps": ["Soften onion and pepper…", "…"]
}
```

`unit` may be a mass (`g`, `kg`, `oz`, `lb`), a volume (`ml`, `l`, `cups`,
`tbsp`, `tsp`, `fl oz`), or a countable unit (`piece`, `clove`, `slice`, `fillet`…).
`optional: true` items never block a recipe. `substitutes` is consulted only when
the ingredient itself is missing.

Add your own recipes by appending to `pantry/data/recipes.json` — no code changes.

---

## Testing

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

**245 tests, all offline**, covering:

| File | Focus |
|---|---|
| `tests/test_units.py` | Quantity/unit parsing, conversions, cross-dimension rejection, aliases, humanising |
| `tests/test_models.py` | Model parsing, scaling, immutability, aisle classification |
| `tests/test_library.py` | Bundled data integrity, filters, all four match states, substitutes, ranking determinism |
| `tests/test_planner.py` | Plan shape, daily cap, no repeats, determinism across instances, filters, leftovers mode |
| `tests/test_shopping.py` | Merging, unit-incompatible separation, stock subtraction, aisle grouping, renderers |
| `tests/test_cli.py` | Every subcommand, `--json` shapes, exit codes, seed safety |
| `tests/test_api.py` | All endpoints incl. 400/404/422 paths and determinism |

Notable regression tests worth keeping green:

- `test_unknown_tail_is_not_eaten_as_a_unit` — the original bug where
  `200 g spaghetti` parsed as unit `"g spaghetti"` and silently broke every match.
- `test_unit_mismatch_is_flagged_rather_than_guessed` — 400 ml of rice must
  never be treated as satisfying 400 g of rice.
- `test_scaling_does_not_mutate_the_original`.

---

## Deployment

### GitHub Pages (used for the live demo)

The browser HUD is pure static files, so it serves straight from the repo:

```bash
# already enabled for this repo — the demo lives at:
# https://raza077-coder.github.io/daily-agents/recipe-planner-agent/web-live/
```

To do it yourself: push `web-live/`, then **Settings → Pages → Source: main / (root)**.

### Vercel (REST API)

`vercel.json` and `api/index.py` are included and ready:

```bash
npm i -g vercel
vercel login
cd recipe-planner-agent
vercel --prod
```

Or import the repo at [vercel.com/new](https://vercel.com/new) with
**Root Directory = `recipe-planner-agent`**. `api/index.py` exposes the FastAPI
`app` object, which is what Vercel's Python runtime looks for.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[api]"
EXPOSE 8000
CMD ["uvicorn", "pantry.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t pantry . && docker run -p 8000:8000 pantry
```

### Anywhere else

The core is standard-library Python — `python3 -m pantry.cli` runs on a cron job,
a Raspberry Pi in the kitchen, or an air-gapped laptop with no changes.

---

## Design decisions & honest limitations

**What PANTRY deliberately does *not* do:**

- **No allergen certification.** The bundled recipes carry no allergen metadata.
  PANTRY will never tell you a dish is nut-free or gluten-free — check labels yourself.
- **No nutritional analysis.** No calorie or macro tracking. It's a kitchen-planner, not a dietitian.
- **No recipe scraping.** The library is curated JSON, not a crawler. This keeps it
  offline and makes every result reproducible.
- **Linear scaling is approximate.** Doubling a recipe doubles the salt in the maths.
  Real cooking doesn't work that way — seasoning, leavening and reduction rarely
  scale linearly. Treat scaled quantities as a starting point.
- **Substitutes are declared, not invented.** PANTRY only swaps when the *recipe author*
  said a swap is valid. It will never guess that yoghurt can stand in for cream.
- **One-file pantry.** There's no database and no accounts — a pantry is a text file
  (or a browser tab). That's the point.

**Why `COUNT` units are separate from mass/volume:** count ↔ mass conversion needs a
per-ingredient density table (1 cup of flour ≠ 1 cup of honey). Rather than ship a
guess, PANTRY keeps them distinct and reports `unit mismatch` when they collide.

---

## License

MIT — see the repository root.

---

<p align="center">
  <sub>Built as <b>Daily Agent #17</b> in the <a href="https://github.com/Raza077-coder/daily-agents">daily-agents</a> collection.</sub><br>
  <sub>Deterministic · Offline · No API keys · Your pantry stays yours.</sub>
</p>

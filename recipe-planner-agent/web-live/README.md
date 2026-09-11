# 🥕 PANTRY — Live Browser HUD

The zero-install, fully client-side demo of the [PANTRY agent](../README.md).

**Live:** https://raza077-coder.github.io/daily-agents/recipe-planner-agent/web-live/

---

## What this is

A faithful **JavaScript port of the Python engine** — not a mock, not a stub.
Unit parsing, ingredient canonicalisation, pantry matching, meal planning and
shopping-list merging all follow the same rules as `pantry/units.py`,
`pantry/library.py`, `pantry/planner.py` and `pantry/shopping.py`, and the port
is checked against the Python engine for parity (`../tools/check_js_engine.js`).

Everything runs **in your browser**:

- ❌ no server, no API, no build step, no framework, no CDN, no webfonts
- ❌ no `fetch` / `XMLHttpRequest` / `WebSocket` / `sendBeacon` anywhere
- ❌ no analytics, no cookies, no localStorage
- ✅ your pantry lives in the tab and disappears when you close it

Open it from a static host, or straight off disk with `file://` — both work.

## Files

| File | Purpose |
|------|---------|
| `index.html` | HUD markup — pantry panel, four tabbed views, recipe modal |
| `style.css` | Warm dark kitchen theme; system fonts only (offline-safe) |
| `pantry-engine.js` | The engine port (~880 lines). Exposes `window.PantryEngine` |
| `app.js` | View layer — DOM wiring and rendering only, no maths |
| `recipes-data.js` | Generated: the 33-recipe library + sample pantry, embedded so `file://` works |

## Run it

```bash
# any static server works
python3 -m http.server 8000
# → http://localhost:8000
```

Or just open `index.html` in a browser.

## Features

- **Pantry chips** — add `200 g spaghetti`, `6 eggs` or a bare `olive oil`;
  remove with ✕; import/export `.txt`; export survives a reload as a file.
- **Cook now** — live-ranked list with a coverage meter, ✓/✕ readiness badge, and
  an itemised reason list: *buy X*, *need 200 g, have 120 g*, *using olive oil instead of butter*.
- **Meal plan** — 1–14 days × any slot combination, no repeated recipes, plus a
  **zero-shopping mode** that returns only meals needing nothing bought.
- **Shopping list** — merged across recipes, pantry stock subtracted, grouped by
  aisle, staples suppressed. Copy, or download as `.txt` / `.json`.
- **Use-it-up** — mark items expiring in N days; the planner then prioritises
  recipes that consume them first.
- **Library** — search across names, tags and ingredients; filter by cuisine;
  open any recipe to see scaled quantities and your stock status per ingredient.

## Parity checks

```bash
cd ..                          # agent root
node tools/check_js_engine.js
```

Prints the JS engine's view of the bundled data and asserts **determinism** —
two independently constructed engines must produce byte-identical plans. Exits
non-zero if the pantry silently stops matching.

## Regenerating the embedded data

`recipes-data.js` is generated. After editing `../pantry/data/recipes.json`:

```bash
python3 tools/build_web_data.py
```

## Accessibility

- Real landmarks (`header` / `nav` / `main` / `footer`) and ARIA tab semantics
- Skip link, visible focus rings, `aria-live` on the pantry chip list
- Keyboard: Enter adds a pantry item, `Esc` closes the recipe modal
- Honours `prefers-reduced-motion` (all transitions and the scanline texture off)
- Every control labelled — icon-only close button carries `aria-label`

## Deployment

Pure static files — any host works:

| Host | How |
|------|-----|
| **GitHub Pages** | Push this folder; Settings → Pages → main / (root). This is what the live URL uses. |
| **Vercel** | `vercel --prod` from this directory (`vercel.json` sits in the agent root) |
| **Netlify** | Drag-and-drop the folder onto app.netlify.com/drop |
| **Anywhere** | `scp` the folder to any web root, or run `python3 -m http.server` |

---

<p align="center">
  <sub>Part of <a href="https://github.com/Raza077-coder/daily-agents">daily-agents</a> · Daily Agent #17</sub><br>
  <sub>Deterministic · Offline · No API keys · Your pantry stays yours.</sub>
</p>

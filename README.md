# daily-agents

A new production-ready AI agent shipped every day. Each one is deterministic and offline-first,
with the same four surfaces: a **CLI**, a **Python library**, a **REST API**, and a **live browser demo**.
No API keys, no accounts, no hidden model calls — the logic is in the repo and you can read it.

## Agents

| # | Agent | What it does | Live demo | Tests |
|---|-------|--------------|-----------|-------|
| 19 | [**SplitKit**](expense-splitter-agent) — Shared Expense Splitter & Settle-Up | Splits group expenses six ways to the exact cent and reduces the ledger to the fewest possible transfers, with a proof that the plan clears every balance | [**live**](https://raza077-coder.github.io/daily-agents/expense-splitter-agent/web-live/) | 354 |

## What is in each agent folder

```
<agent>-agent/
├── README.md            # overview, features, setup, usage, config, deployment
├── AGENT.md             # the agent's system prompt / behaviour spec
├── persona.json         # voice and phrasing used by the CLI and reports
├── <package>/           # the engine — pure standard library, no required deps
│   ├── engine.py        # facade shared by the CLI, the API and the demo
│   ├── cli.py           # command-line entry point
│   └── ...
├── api/index.py         # FastAPI wrapper (deployable to Vercel as-is)
├── web-live/            # self-contained browser demo (no fetch, no CDN)
├── tests/               # pytest suite
├── tools/               # maintenance scripts (parity checks, data generation)
└── vercel.json
```

## Design principles

These hold across every agent in this repository:

- **Deterministic.** The same input always produces the same output. No randomness seeded by
  wall-clock time, no model call where an algorithm will do.
- **Offline.** The engine imports only the Python standard library. Installing the extra
  requirements is optional and only needed for the REST layer and the test suite.
- **No invented data.** When something is unknown the agent says so or refuses; it never
  fabricates a plausible-looking number.
- **Failures are loud.** Invalid input raises with the offending value named, rather than being
  silently coerced into something that looks fine.
- **The browser demo is not a mock.** It re-implements the engine in JavaScript and a parity
  harness runs both over identical inputs, so the page and the CLI cannot drift apart.

## Running any agent

```bash
git clone https://github.com/Raza077-coder/daily-agents.git
cd daily-agents/<agent>-agent
pip install -r requirements.txt     # optional: only for the API + tests
python3 -m pytest -q                # verify it works
```

Each agent's own README has the full command reference.

## Deployment

Every agent ships `vercel.json` + `api/index.py`, so it deploys to Vercel as a serverless
function without changes:

```bash
npm i -g vercel
cd <agent>-agent
vercel login
vercel --prod
```

The browser demos are static and already live on GitHub Pages — no build step, no CDN.

## License

MIT — see each agent folder for its own `LICENSE`.

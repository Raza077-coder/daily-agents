# JOBFLOW — Agent System Prompt & Behaviour Spec

This file is JOBFLOW's configuration: it defines the persona, the rules the
engine must follow, and the voice of every surface (CLI, library, REST, web
demo). The engine is deterministic, so this is a **specification**, not a
prompt sent to a language model at runtime — every rule below is implemented in
code and covered by the test suite.

---

## 1. Identity

> **You are JOBFLOW**, a job-application pipeline agent.
>
> You keep an honest ledger of a job search — every application, every rung it
> reached, every silence — and you turn that ledger into a short, ranked list of
> things worth doing today.
>
> You never guess. You never inflate. If the data cannot support a number, you
> say so instead of printing a confident zero.

---

## 2. Voice and tone

| Do | Don't |
|---|---|
| State the fact, then the action | Editorialise about the market |
| "60 days quiet in Applied (threshold 14)." | "Wow, they're ghosting you!" |
| Name the reason behind every action | Give advice with no evidence line |
| Say "not measurable yet" when the denominator is 0 | Print `0.0%` and let it read as failure |
| Congratulate sparingly and concretely | Cheerlead on every log |

The house style is **calm, specific, and non-judgemental**. A quiet pipeline is
a data point, not a character flaw.

---

## 3. Non-negotiable rules

These are the invariants the engine enforces. Each maps to code and to tests.

### R1 — Money is never a float
Salaries are stored as integer **minor units** (cents/paise). `120000` USD is
`12000000` in the vault. Rounding is half-up. A vault read is flagged
`salary_is_minor` exactly once, so a save/load cycle can never inflate a salary
by 100×.

### R2 — Every number is derivable from the event log
Current status alone is not enough. "Reached an interview" counts an
application rejected *after* the interview — the funnel tracks the **furthest
rung reached**, so late rejections do not retroactively erase progress.

### R3 — Respect the maturity window
A 3-day-old application is not a rejection. Cumulative rates divide by
applications old enough to have plausibly received a reply:

| Stage | Maturity window |
|---|---|
| Applied | 21 days |
| Recruiter Screen | 30 days |
| Interview | 45 days |
| Onsite / Final | 60 days |
| Offer | 75 days |
| Accepted | 90 days |

When the mature denominator is `0`, the response rate is reported as
**"not measurable yet"** — never as `0.0%`.

### R4 — Rungs cannot be skipped silently
`applied → onsite` is refused with the skipped stages named. Real cases
(a referral fast-track) must pass `force`. This keeps the funnel honest: a
skipped rung means the event log is wrong, not that the candidate is lucky.

### R5 — Money is never summed across currencies
Compensation is reported per currency. There is no FX table and no implied
conversion; mixing USD and PKR into one "average" would be fiction.

### R6 — Silence is classified, not repeated
The number of follow-ups already sent changes the advice:

| Follow-ups sent | Rule fired | Advice |
|---|---|---|
| 0 | `no_response_overdue` | Send a follow-up |
| 1 … `max_followups-1` | `second_followup` | Send one final note, then stop |
| ≥ `max_followups` | `stalled` | Stop chasing; mark it and move on |

An application is only ever nudged `max_followups` times (default 2).

### R7 — Every action carries its own justification
Each action returns a `terms` breakdown (severity, overdue days, staleness,
priority, stage) and an `explain` sentence. **No action may be produced without
a reason a human can read and argue with.**

### R8 — Determinism
No network calls, no clock reads inside calculations, no hash-order iteration.
Every entry point takes an explicit `today`; the only place the system clock is
consulted is `resolve_today()` when a caller omits it. Same vault + same date →
byte-identical output.

---

## 4. The daily plan — rule catalogue

Actions are ranked by score, then by rule priority. Severities:

| Severity | Meaning |
|---|---|
| `critical` | Time-boxed or money at risk — do it today |
| `high` | Clear momentum cost if ignored this week |
| `medium` | Worth doing, no hard deadline |
| `low` | Housekeeping |

| Rule | Severity | Fires when |
|---|---|---|
| `offer_expiring` | critical | An offer has been open ≥ 5 days |
| `deadline_today` | critical | A wishlist item's deadline is today |
| `interview_today` | critical | An interview is scheduled for today |
| `deadline_soon` | high/medium | Deadline within 3 days |
| `interview_soon` | high | Interview tomorrow |
| `offer_waiting` | high | Offer received, not yet acknowledged |
| `overdue_next_action` | critical/high/medium | A commitment you logged is past due |
| `no_response_overdue` | high/medium | Quiet past threshold, no follow-up sent |
| `close_the_loop` | high | Interview 1–3 days ago, no thank-you logged |
| `interview_prep` | medium | Interview in 2–7 days |
| `second_followup` | medium | Quiet past threshold, one follow-up sent |
| `rejection_review` | medium | Rejected at interview+ within 10 days, no post-mortem |
| `weekly_target` | high/medium | Behind the weekly application target |
| `wishlist_nudge` | low/medium | Wishlist item idle past `wishlist_idle_days` |
| `stalled` | low | Quiet past threshold, follow-ups exhausted |
| `log_wins` | low | An offer/accepted landed in the last 7 days |

### Scoring

```
score = severity_weight
      + min(overdue_days × 12, 240)
      + min(stale_days   ×  4, 160)
      + priority × 15
      + stage_index × 12
      + (imminence ? 30 : 0)
      + (shortfall × 5)        # weekly target only
```

`critical=1000`, `high=500`, `medium=200`, `low=60`.

---

## 5. Stall thresholds

Days of silence after which an open application is "going quiet":

| Status | Threshold |
|---|---|
| Applied | 14 |
| Recruiter Screen | 10 |
| Interview | 12 |
| Onsite / Final | 10 |
| Offer | 7 |
| *(anything else)* | 14 |

An **open offer is owned by the offer rules** — the silence rules skip
`status=offer` so the plan never issues two contradictory instructions about the
same application.

---

## 6. Pipeline vocabulary

```
wishlist → applied → screen → interview → onsite → offer → accepted
                                  ↘ rejected
                                  ↘ withdrawn
```

- **Active:** `applied`, `screen`, `interview`, `onsite`, `offer`
- **Pre-application:** `wishlist`
- **Closed:** `rejected`, `withdrawn`, `accepted`
- **Sources:** `referral`, `company_site`, `linkedin`, `job_board`,
  `recruiter`, `networking`, `other`

---

## 7. Surface contracts

| Surface | Entry point | Writes? |
|---|---|---|
| CLI | `python3 -m jobtracker.cli <cmd>` | yes |
| Python | `JobFlowEngine` | yes |
| REST | `api/app.py` (`/api/*`) | yes |
| Browser demo | `web-live/index.html` | no (read-only, in-memory) |

The browser demo is a **faithful re-implementation**, not a mock: `tools/parity_check.py`
asserts the JavaScript and Python engines agree field-for-field on two vaults.

### Exit codes (CLI)

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Domain error (validation, not found, illegal move) |
| 2 | Usage error (argparse) |

---

## 8. Configuration keys

| Key | Default | Meaning |
|---|---|---|
| `weekly_target` | 5 | Applications per ISO week |
| `followup_days` | 7 | Silence before a first follow-up |
| `second_followup_days` | 14 | Silence before the final follow-up |
| `wishlist_idle_days` | 10 | Wishlist rot threshold |
| `max_followups` | 2 | Hard ceiling on nudges |

Set via `JOBFLOW_*` environment variables or the vault's config block.

---

## 9. What JOBFLOW will not do

- It will not invent a response rate from an immature sample.
- It will not convert currencies or compare salaries across them.
- It will not tell you an application is "probably fine" — it reports quiet days
  and lets you decide.
- It will not contact anyone, scrape job boards, or send email. It is a ledger
  and a planner, not an autopilot.
- It will not read the clock mid-calculation, so its output is reproducible.

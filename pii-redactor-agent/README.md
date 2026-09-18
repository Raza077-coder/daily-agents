# VEIL — Deterministic PII Redaction Agent

**Find personal and secret data in text, apply an auditable policy, and verify the result.**

VEIL is a privacy tool built on one idea: redaction should be **explainable**. Every
finding comes from a named detector that ran a named validator, every transform is
one of six documented actions, and the "is this clean?" verdict is *measured* by
re-scanning the output rather than asserted.

No model. No API keys. No network calls. Same input, same output, every run.

```
$ veil scan vendor_email.txt
...
EMAIL          mask        1  ops@brightpath-consulting.com
               ↳              •••••••••••••••••••••••••.com
               ·              structurally valid address
PHONE          mask        1  +92 300 1234567
               ↳              ••••••••••••4567
               ·              leading + country code
CREDIT_CARD    mask        1  4111 1111 1111 1111
               ↳              ••••••••••••1111
               ·              passes the Luhn checksum
SECRET         remove      1  AKIASYNTHETICKEY0000
               ↳              (removed)
               ·              matched a known provider credential format

Risk: 113 (CRITICAL) · 18 findings · 6 entity types
```

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Key features](#key-features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Setup](#setup)
- [Usage](#usage)
- [Policy reference](#policy-reference)
- [Configuration](#configuration)
- [Verification and testing](#verification-and-testing)
- [Project layout](#project-layout)
- [Deployment](#deployment)
- [Limitations](#limitations)
- [License](#license)

---

## Why this exists

Most redaction tools give you an answer with no working. That is fine until
someone asks why a specific value was flagged, or why it wasn't, and the honest
reply is "the model decided".

VEIL takes the opposite position: **every decision is a rule, and the rules are
inspectable**. When it flags a card number it is because that number passed the
Luhn checksum. When it refuses to flag a 16-digit order number, it is because the
checksum failed. Both outcomes are reportable, and both are reproducible.

That matters in practice because redaction is usually a **review** task, not an
execution task. Someone has to sign off that a document is safe to share. VEIL is
built to give that person evidence.

---

## Key features

### Thirteen detectors, each with a validator

A permissive pattern proposes candidates; a validator decides. This two-stage
design is what keeps false positives down without making the patterns so tight
that they miss real data.

| Entity | Validator |
|---|---|
| `EMAIL` | Structural: no leading/trailing dot, no consecutive dots, valid TLD |
| `PHONE` | E.164 shape (leading `+`, parens, or 2+ separators) or a nearby phone keyword |
| `CREDIT_CARD` | **Luhn checksum** |
| `IBAN` | **ISO 7064 mod-97** |
| `SSN` | SSA rules: area ≠ 000/666/900+, group ≠ 00, serial ≠ 0000, not a known dummy |
| `NATIONAL_ID` | Pakistani CNIC format |
| `IPV4` | All four octets 0–255, no leading zeros |
| `IPV6` | Two or more hextet groups |
| `MAC` | Six hex octets, not glued to surrounding hex |
| `URL` | Dotted host, balanced brackets, trailing punctuation trimmed |
| `SECRET` | Provider formats (AWS, GitHub, Slack, Stripe, JWT, PEM, Bearer) + credential-shaped assignments |
| `DATE_OF_BIRTH` | Valid date **and** a birth-context keyword within 24 characters |
| `PERSON` | Honorific, or a known given name followed by a capitalised surname *(opt-in)* |

### Six actions

`mask` · `redact` · `remove` · `hash` · `tokenize` · `keep`

Only `tokenize` is reversible, and only with the vault it produces. That is
deliberate: reversibility should require an explicit, auditable artifact.

### Verification you can quote

After redacting, VEIL re-runs every detector over its own output. Anything still
detectable is a **residual** — a real failure. Anything exempted by the allowlist
is **preserved** — a decision. The two are reported separately, so "clean" means
"nothing survived that wasn't supposed to".

### Deterministic by construction

- Overlaps resolve by longest span → confidence → risk weight → document order.
  Never by dictionary iteration order.
- Masking has no random padding, so the same value masks identically every run.
- Hashing is salted HMAC-SHA256, so equal values map to equal digests.

### Risk scoring for triage

A weighted score over distinct findings and occurrence counts, banded into
`NONE → LOW → MODERATE → HIGH → CRITICAL`. It is a **heuristic for triage**, not a
measurement — the report says so, and so should anyone quoting it.

### Four surfaces, one engine

CLI (9 commands) · Python library · FastAPI REST · browser demo. The browser demo
is a JavaScript port, and `tools/parity_check.py` asserts the two agree field by
field on every run.

---

## How it works

```
                    ┌──────────────────────────┐
   input text ──────▶  DETECT                  │
                    │  13 detectors, each a    │
                    │  pattern + a validator   │
                    └────────────┬─────────────┘
                                 │ candidate spans
                                 ▼
                    ┌──────────────────────────┐
                    │  RESOLVE                 │
                    │  longest span →          │
                    │  confidence → weight →   │
                    │  document order          │
                    └────────────┬─────────────┘
                                 │ non-overlapping spans
                                 ▼
                    ┌──────────────────────────┐
                    │  POLICY                  │
                    │  action per entity,      │
                    │  allowlist, denylist     │
                    └────────────┬─────────────┘
                                 │ replacements
                                 ▼
                    ┌──────────────────────────┐
                    │  APPLY                   │
                    │  splice spans right-to-  │
                    │  left so offsets hold    │
                    └────────────┬─────────────┘
                                 │ redacted text
                                 ▼
                    ┌──────────────────────────┐
                    │  VERIFY                  │
                    │  re-scan the output;     │
                    │  residuals vs preserved  │
                    └──────────────────────────┘
```

**Why spans are applied right-to-left.** Replacing left-to-right shifts every
subsequent offset, which silently corrupts the later splices. Applying from the
end means each replacement happens before any offset it could affect.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Engine | Python 3.9+, **standard library only** | Runs anywhere, zero dependency risk, trivially auditable |
| YAML subset | Hand-written parser (`veil/miniyaml.py`) | PyYAML is a dependency and the engine has none; the supported subset is documented and tested |
| CLI | `argparse` | No dependency, and exit codes are a documented contract |
| REST | FastAPI + uvicorn *(optional extra)* | Only needed for the HTTP surface |
| Browser demo | Vanilla JS, no build step | Must run from `file://` behind a strict CSP |
| Tests | pytest — **372 passing** | |
| Parity | Python ↔ Node harness — **108 checks** | |
| Offline proof | Node smoke test — **175 checks** with the network stubbed to throw | |

The engine's zero-dependency property is load-bearing: it can be dropped into a
locked-down environment, a CI container, or an air-gapped host without a
dependency review.

---

## Setup

### Requirements

- Python **3.9+** (the engine uses `from __future__ import annotations`)
- Node.js **18+** — *only* for the parity check and the browser smoke test

### Install

```bash
git clone https://github.com/Raza077-coder/daily-agents.git
cd daily-agents/pii-redactor-agent
```

The engine needs nothing installed:

```bash
python3 -m veil --help
```

For the HTTP surface and the test suite:

```bash
pip install -r requirements.txt
```

---

## Usage

### Try it immediately — no input files needed

```bash
python3 -m veil demo
```

This writes a synthetic bundle into `./veil-demo/` and prints a tour. The bundle
contains three documents and three ready-to-use policies.

### Scan without changing anything

```bash
python3 -m veil scan veil-demo/app.log
```

Add `--redacted` to also produce the redacted text and a diff:

```bash
python3 -m veil scan veil-demo/app.log --redacted --diff
```

`--json` emits machine-readable output:

```bash
python3 -m veil scan veil-demo/app.log --json
```

### Redact

```bash
python3 -m veil redact veil-demo/vendor_email.txt -o clean.txt
```

With a policy and a report:

```bash
python3 -m veil redact veil-demo/app.log -o clean.log \
    --policy veil-demo/strict.yaml \
    --report report.md
```

```
$ veil redact veil-demo/deployment.env -o /tmp/clean.env

SECRET         remove      1  AKIASYNTHETICKEY0000
               ↳              (removed)
               ·              matched a known provider credential format
SECRET         remove      1  testkey-ZephyrCove-7193
               ↳              (removed)
               ·              value assigned to a credential-shaped key
EMAIL          mask        1  support@brightpath-consulting.com
               ↳              •••••••••••••••••••••••••••••.com
               ·              structurally valid address
IPV4           mask        1  192.168.14.22
               ↳              ••••••••••4.22
               ·              all four octets in range 0-255
URL            redact      1  https://hooks.brightpath-consul...
               ↳              [URL]
               ·              http(s) URL with a dotted host

Risk: 50 (CRITICAL) · 8 findings
Verification: CLEAN — no residual data detected in the output.

Wrote /tmp/clean.env
```

### Verify (redact and assert)

```bash
python3 -m veil verify veil-demo/app.log
```

Exits `1` if anything survived. Use this in a pipeline where a dirty result must
stop the job.

### Tokenize and detokenize

```bash
python3 -m veil redact notes.txt -o out.txt --policy veil-demo/pseudonymize.yaml
python3 -m veil detokenize out.txt --vault out.txt.veilvault.json
```

Inspect a vault without restoring it:

```bash
python3 -m veil vault out.txt.veilvault.json
```

### Gate CI on a tree

```bash
python3 -m veil check ./logs --fail-on high
```

```
$ veil check veil-demo --fail-on high
Policy default   files 6   with findings 6   worst CRITICAL

LEVEL      SCORE  DISTINCT  OCCUR  FILE
------------------------------------------------------------------------------
CRITICAL      59        11     11* veil-demo/app.log
CRITICAL      50         8      8* veil-demo/deployment.env
HIGH          10         1      1* veil-demo/pseudonymize.yaml
CRITICAL     113        18     18* veil-demo/vendor_email.txt
```

`--fail-on` accepts `NONE`, `LOW`, `MODERATE`, `HIGH`, `CRITICAL`, `any`, `never`
(case-insensitive). Exit codes are a contract:

| Code | Meaning |
|---|---|
| `0` | Success, threshold not reached |
| `1` | Findings present and threshold reached |
| `2` | Usage error (bad flag, missing file) |

### Inspect the catalogue

```bash
python3 -m veil entities      # what's on, and its action
python3 -m veil reference     # full entity + action reference tables
```

### Python library

```python
from veil import Scanner, from_yaml

scanner = Scanner()                      # default policy
result = scanner.redact(open("notes.txt").read())

print(result.text)
print(result.risk.level, result.risk.score)
print(result.verification.status)        # "CLEAN" or "RESIDUAL_FOUND"

for finding in result.findings:
    print(finding.entity, finding.action, finding.count, finding.detector)
```

With your own policy:

```python
from veil import Scanner, from_yaml

policy = from_yaml(open("my-policy.yaml").read())
result = Scanner(policy).redact(text)
```

Restoring tokenized output:

```python
from veil import detokenize
original = detokenize(result.text, result.token_map)
```

### REST API

```bash
pip install "fastapi>=0.110" "uvicorn>=0.27"
uvicorn api.index:app --reload --port 8000
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness, engine version, entity count |
| `GET` | `/entities` | The entity catalogue and default actions |
| `GET` | `/reference` | Entity and action reference tables |
| `GET` | `/demo` | List the built-in sample documents |
| `GET` | `/demo/{name}` | Fetch one sample document's text |
| `POST` | `/scan` | Detect without transforming |
| `POST` | `/redact` | Detect and transform |
| `POST` | `/verify` | Detect, transform, and report residuals |
| `POST` | `/detokenize` | Restore values from a supplied vault |

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/redact \
  -H 'Content-Type: application/json' \
  -d '{"text": "contact alice@example.com or +1 415 555 0132"}'
```

### Browser demo

Open `web-live/index.html`. It works from `file://` — the demo data is embedded
in `web-live/demo-data.js`, not fetched, so there is no CORS problem and no
network call to make.

Five tabs: **Scan**, **Redact**, **Verify**, **Stories**, **Entities**.

---

## Policy reference

A policy is a YAML document. Copy `config.example.yaml` as a starting point, or
use one of the three shipped policies from `veil demo`.

```yaml
name: shareable

entities:
  - ALL        # expand every entity
  - "!PERSON"  # then remove this one

actions:
  SECRET: remove
  EMAIL: mask
  PHONE: mask
  URL: redact

keep_first: 0
keep_last: 4
mask_char: "•"
hash_salt: ""
token_prefix: VEIL

allowlist:
  - support@example.com      # never touch this value

denylist:
  - ACME-INTERNAL            # always redact this literal

names:
  - Priya
  - Wei

case_sensitive_denylist: false
```

### The three shipped policies

| Policy | Posture | Use when |
|---|---|---|
| `strict.yaml` | Remove or obliterate | Handing a document outside the organisation |
| `pseudonymize.yaml` | Reversible tokens | Working with the data yourself, but not in the clear |
| `shareable.yaml` | Mask, keep readable | Sharing internally where context still matters |

### The supported YAML subset

`veil/miniyaml.py` is a small parser, not a general YAML implementation.
Supported: nested mappings, block sequences, inline lists, quoted and unquoted
scalars, comments, blank lines, booleans, integers, floats, `null`. A parse
failure names the line and column. Anything outside this subset raises rather
than being silently misread.

---

## Configuration

`config.example.yaml` documents every key. Precedence, lowest to highest:

```
built-in defaults → config file → VEIL_* environment variables → CLI flags
```

| Environment variable | Effect |
|---|---|
| `VEIL_ENTITIES` | Comma-separated entity list (`ALL`, `!PERSON` supported) |
| `VEIL_ACTIONS` | `ENTITY=action` pairs, comma-separated |
| `VEIL_KEEP_FIRST` | Characters visible at the start of a masked value |
| `VEIL_KEEP_LAST` | Characters visible at the end of a masked value |
| `VEIL_MASK_CHAR` | Character used for masking |
| `VEIL_HASH_SALT` | Salt for the `hash` action |
| `VEIL_TOKEN_PREFIX` | Prefix for `tokenize` output |
| `VEIL_ALLOWLIST` | Comma-separated values to leave untouched |
| `VEIL_DENYLIST` | Comma-separated literals to always redact |
| `VEIL_NAMES` | Extra given names for the `PERSON` detector |

**Set `VEIL_HASH_SALT` in production.** An unsalted digest of a phone number is
reversible by enumeration in seconds.

---

## Verification and testing

Every command below runs offline and needs no credentials.

```bash
python3 -m pytest                      # 372 tests
python3 tools/parity_check.py          # 108 Python↔JS checks
node tools/web_smoke.js                # 175 checks, network stubbed to throw
python3 tools/build_web_data.py --check # demo data matches a fresh build
python3 tools/readme_check.py          # every path in this README exists
```

### What each gate actually proves

**`pytest` — 372 tests.** Detectors (positive *and* negative cases for all 13),
validators, all six actions, the vault round-trip, the YAML subset, environment
overrides, overlap resolution, ordering determinism, the verification pass, all
report formats, the CLI as a subprocess, and every API route including its error
codes.

**`parity_check.py` — 108 checks.** The browser demo re-implements the engine in
JavaScript. Duplicated logic drifts, and a demo that quietly disagrees with the
CLI is worse than no demo, so both engines run over the same inputs and every
field is compared: detection spans, redacted text, risk score and level, finding
fields, mask output, token numbering, validator verdicts, and the **hash digests
themselves** — the JavaScript includes a hand-rolled SHA-256 so its HMAC matches
Python's `hashlib` exactly.

**`web_smoke.js` — 175 checks with the network removed.** `XMLHttpRequest`,
`fetch`, `WebSocket` and `EventSource` are replaced with functions that throw, and
the engine source is scanned for those identifiers. This is what makes "no
network calls" a checked claim rather than a marketing line.

**`build_web_data.py --check`** fails if `web-live/demo-data.js` has drifted from
what the Python engine would generate, so the demo's sample data cannot silently
diverge from the CLI's.

**`readme_check.py`** extracts every path-like token from this README, verifies
each one exists, then runs the four commands documented above. Documentation that
promises a file which was never committed is the failure mode this exists to catch.

---

## Project layout

```
pii-redactor-agent/
├── veil/                     # the engine — standard library only
│   ├── errors.py             #   typed errors that name the offending value
│   ├── models.py             #   Span, Finding, RedactionResult, entity catalogue
│   ├── detectors.py          #   13 detectors: pattern + validator
│   ├── miniyaml.py           #   the documented YAML subset
│   ├── policy.py             #   actions per entity, allowlist/denylist, env overrides
│   ├── actions.py            #   the six transforms
│   ├── vault.py              #   token vault — persistence and round-trip
│   ├── scanner.py            #   detect → resolve → apply → verify
│   ├── reports.py            #   terminal, Markdown, JSON
│   ├── demo.py               #   the synthetic sample bundle
│   └── cli.py                #   9 commands, CI-friendly exit codes
├── api/index.py              # FastAPI layer (thin: validates, delegates)
├── web-live/                 # browser demo — no build step
│   ├── index.html
│   ├── style.css
│   ├── veil-engine.js        #   the JS port (parity-checked)
│   ├── app.js                #   tab wiring and rendering
│   └── demo-data.js          #   generated from the Python engine
├── tools/
│   ├── build_web_data.py     #   generates demo-data.js from the engine
│   ├── parity_check.py       #   Python ↔ JS diff
│   ├── parity_harness.js     #   the Node side of that diff
│   ├── web_smoke.js          #   offline proof for the browser engine
│   └── readme_check.py       #   proves this README's claims
├── tests/                    # 372 tests across 7 modules
├── AGENT.md                  # system prompt
├── persona.json              # agent persona
├── config.example.yaml       # documented configuration
├── pyproject.toml
├── requirements.txt
├── pytest.ini
└── vercel.json
```

---

## Deployment

### As a CLI tool

```bash
pip install .
veil scan suspicious.log
```

### As a library

```bash
pip install veil-pii
```

```python
from veil import Scanner
Scanner().redact(text)
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.27"
EXPOSE 8000
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t veil .
docker run -p 8000:8000 veil
```

### Vercel (serverless API)

`vercel.json` and `api/index.py` are committed and ready:

```bash
npm install -g vercel
cd pii-redactor-agent
vercel login
vercel --prod
```

> **Caveat, stated plainly:** the HTTP surface is **stateless**. Every request
> carries its own text and (for `/detokenize`) its own vault. Nothing is written
> to disk. That is a deliberate fit for a redaction service — you do not want
> customer data persisted on an edge function — but it means there is no
> server-side job history. Persist results on your side if you need them.

### Static browser demo

Everything in `web-live/` is static and works from `file://`. To publish it,
copy that directory anywhere that serves files, or enable GitHub Pages on the
repository — the live demo for this project is served that way.

---

## Limitations

Stated up front, because a privacy tool that overstates its coverage is worse
than no tool.

- **Detection is rule-based, so it has edges.** Every detector is a pattern plus a
  validator. A format nobody wrote a rule for will not be found. This is the
  trade for auditability.
- **`PERSON` is opt-in and imprecise.** It uses honorifics and a dictionary of
  given names. It finds "Alice Johnson"; it misses names outside the dictionary,
  and it can match an ordinary capitalised pair. That is why it is off by default.
- **A bare date is not a date of birth.** DOB requires a birth-context keyword
  nearby, so `released 2026-09-18` is never flagged. An isolated birth date in a
  document with no such keyword will be missed.
- **Phone detection favours precision.** A formatted number (`+92 300 1234567`,
  `(415) 555-0132`) is caught on shape alone. An unformatted bare number needs a
  nearby phone keyword, so `call 4155550132` in a bare sentence may be missed.
- **`NATIONAL_ID` targets the Pakistani CNIC format.** Other national identifiers
  need their own pattern.
- **Masking hides, it does not remove.** `al•••••@example.com` still reveals the
  domain, the local-part prefix, and the exact length. If a value must not be
  inferable, use `remove`, `redact`, or `hash` — not `mask`.
- **A token after a credential key is re-detected.** `password=VEIL_SECRET_001` is
  matched by the assignment detector, because the value genuinely looks like a
  credential. Verification skips the tool's own tokens and reports clean, but a
  second pass over the output would flag it. Prefer `remove` for credentials.
- **The risk score is a heuristic.** It is weighted for triage, not calibrated as
  a probability of harm. Do not quote it as a measurement.
- **This is not a compliance certification.** VEIL produces evidence. A human
  certifies.
- **No document parsing.** VEIL redacts text. To redact inside PDF, DOCX or XLSX
  you must extract the text first, then run VEIL, then rebuild the document.

---

## License

MIT — see [LICENSE](LICENSE).

---

*Part of the [daily-agents](../../) collection. Built as a demonstration that a
privacy tool can be auditable end to end: 372 tests, a parity check against its
own browser port, and an offline proof that removes the network before asserting
that none is used.*

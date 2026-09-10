# 🔐 VAULTGUARD — Password Vault Agent

> Your secrets stay yours. Encrypted, offline, deterministic.

**VAULTGUARD** is a self-contained password vault agent. It keeps your credentials in a
single **encrypted file**, audits their hygiene, and generates strong replacements —
**without a single network call**. No telemetry, no breach-check API, no cloud. The
master password is the only thing standing between an attacker and the vault, and it
never leaves the machine.

Part of the [daily-agents](https://github.com/Raza077-coder/daily-agents) collection —
one production-quality AI agent per day.

---

## 📖 Table of Contents

- [Overview](#-overview)
- [Key Features](#-key-features)
- [How It Works](#-how-it-works)
- [Tech Stack](#-tech-stack)
- [Setup & Installation](#-setup--installation)
- [Usage Examples](#-usage-examples)
- [Configuration](#-configuration)
- [Security Model](#-security-model)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [Project Structure](#-project-structure)
- [FAQ](#-faq)

---

## 🎯 Overview

Most password managers ask you to trust a server. VAULTGUARD asks you to trust arithmetic.
It is a **deterministic, offline, explainable** vault: every score it reports can be traced
back to a rule you can read in the source.

| Aspect | Approach |
|---|---|
| Storage | One encrypted JSON file you own and can back up |
| Network | **Zero** outbound calls — verifiable in the source |
| Scoring | Deterministic rules, no ML, no black box |
| Dependencies | **None** in the core engine (Python standard library only) |
| Surfaces | CLI · Python library · REST API · browser demo |

The engine ships with the **VAULTGUARD persona** (`vaultguard_persona.json`) — a discreet,
precise security-officer voice that leads with findings and never overstates certainty.

---

## ✨ Key Features

### 🔒 Encrypted vault
- **PBKDF2-HMAC-SHA256** key derivation, 200,000 iterations by default (OWASP 2023 floor)
- **AEAD encryption** — `aes-256-gcm` when `cryptography` is installed, with a
  pure-standard-library `hmac-sha256-ctr-etm` fallback (encrypt-then-MAC)
- The vault **header is authenticated**, so tampering with the salt or iteration count
  is detected rather than silently honoured
- **Atomic writes** — a temp file is `fsync`ed and `os.replace`d over the target, so a
  crash mid-save never leaves a corrupt vault
- Files are created with `0600` permissions

### 🛡 Credential management
- Entry CRUD with categories, tags, favourites, URLs, notes and **TOTP seeds**
- **Secrets masked by default** (`abc••••••••`) — plaintext only on explicit request
- Lookup by ID *or* exact title
- Search across title, username, URL, notes, category and tags
- Sort by title, username, category, age, created or updated
- Filters: `--weak`, `--stale`, `--favorite`

### 🎲 Generation & scoring
- Character-class aware password generator that **guarantees every selected class appears**
  and shuffles with a CSPRNG so the guarantee does not leak positional order
- EFF-style **passphrase** generator and a **PIN** generator
- Deterministic **entropy scoring** with penalty rules for repetition, sequences, keyboard
  walks, dictionary words and date fragments
- **Crack-time estimation** at a configurable offline-guess budget
- Reuse fingerprints (keyed PBKDF2 digests) let the auditor spot duplicate passwords
  **without storing the plaintext twice**

### 🩺 Security audit
- Weighted 0–100 hygiene score with an A+…F grade and an explicit **deduction breakdown**
- Detects: weak passwords, reused passwords, stale passwords, missing 2FA, duplicate
  titles, missing identifiers, and whole-vault rotation neglect
- Findings ranked by severity (critical → low) with actionable recommendations

### 🧰 Operational
- **Atomic** encrypted saves; file-level **backup** that stays encrypted
- JSON **import/export** (with a masked export option)
- **Master-password rotation** that re-keys the vault with a fresh salt
- JSON output on **every** CLI command for scripting and CI

---

## ⚙️ How It Works

```
                    ┌──────────────────────────────────────────────┐
   user input ──────▶│  master password                             │
                    └───────────────┬──────────────────────────────┘
                                    │  PBKDF2-HMAC-SHA256 · 200k · salt
                                    ▼
                    ┌──────────────────────────────────────────────┐
                    │  vault key (32 bytes)                        │
                    └───────┬──────────────────────────┬───────────┘
                            │                          │
                   verifier │                          │ payload key
                            ▼                          ▼
              ┌──────────────────────┐   ┌────────────────────────────────┐
              │ encrypted token      │   │ AEAD-encrypt the entry list    │
              │ (cheap password check)│  │ aad = authenticated header     │
              └──────────────────────┘   └───────────────┬────────────────┘
                                                         ▼
                                        ┌─────────────────────────────────┐
                                        │ vault.json (0600, atomic write) │
                                        │ header + verifier + payload     │
                                        └─────────────────────────────────┘
```

**1 · Key derivation.** The master password is stretched with PBKDF2-HMAC-SHA256 against a
per-vault random salt. The salt and iteration count live in the plaintext header so the
vault can be opened without parsing hostile data — and the header is bound into the AEAD
associated data, so editing it invalidates the payload.

**2 · Fast verification.** A known token is encrypted with the derived key. Unlocking only
has to decrypt 23 bytes to answer "is this the right password?" — the entry list is only
decrypted once the password is confirmed.

**3 · Authenticated encryption.** Entries are serialised and sealed with AES-256-GCM (or
the stdlib fallback) using a **fresh nonce on every save**. Flipping a single bit of the
nonce, ciphertext, tag or header makes decryption raise instead of returning garbage.

**4 · Audit pipeline.** Reuse is detected by comparing keyed fingerprints. Each unique
password is then scored for entropy and penalties; findings are weighted per category,
scaled by prevalence, and subtracted from 100.

**5 · Masking.** Entries leave the engine masked unless `reveal=True` is passed explicitly.
Export is the only path that writes plaintext, and it warns you when it does.

---

## 🧱 Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Core engine | **Python 3.9+ standard library only** | Auditable, dependency-free, runs anywhere |
| KDF | `hashlib.pbkdf2_hmac` | FIPS-friendly, no third-party crypto needed |
| AEAD (fallback) | HMAC-SHA256 counter-mode + encrypt-then-MAC | Correct AEAD from stdlib primitives |
| AEAD (preferred) | `cryptography` → AES-256-GCM | Constant-time hardware-accelerated cipher |
| REST API | **FastAPI** + Pydantic v2 | Typed schemas, auto OpenAPI docs |
| CLI | `argparse` | Zero-dependency, scriptable, JSON output |
| Server | **Uvicorn** | Standard ASGI runtime |
| Tests | **pytest** | 265 tests, fully offline |
| Browser demo | Vanilla JS + **WebCrypto** | Same AEAD design, entirely client-side |

---

## 🚀 Setup & Installation

### Requirements
- Python **3.9** or newer (tested on 3.11)
- No dependencies required for the core vault, CLI or library

### Install

```bash
git clone https://github.com/Raza077-coder/daily-agents.git
cd daily-agents/password-vault-agent

# Option A — run in place (nothing to install)
python3 -m vaultguard.cli --help

# Option B — install as a package with the `vaultguard` command
pip install -e .

# Option C — with the REST API
pip install -e ".[api]"

# Option D — everything, including the AES-GCM backend and tests
pip install -r requirements.txt
```

### 60-second demo

```bash
# Build a throwaway vault with sample credentials and print a full audit
python3 -m vaultguard.cli demo
```

---

## 💻 Usage Examples

### Try it instantly

```bash
$ python3 -m vaultguard.cli demo
```

```
VAULTGUARD SECURITY AUDIT
==========================================================
Vault health : 74/100  (C · needs attention)
Entries      : 4
Strong       : 0
Reasonable   : 2
Weak         : 2
Reused       : 2
Stale        : 0
With 2FA     : 2

Deductions
----------------------------------------------------------
  - 18  weak
  -  6  reused
  -  2  no_2fa

Findings
----------------------------------------------------------
  [CRITICAL] Password reused across 2 entries
             Shared by: Bank Portal, GitHub. One breach exposes all of them.
  [CRITICAL] Weak password on 'Home WiFi'
             Score 0/100 (very weak); cracked in instantly. Use at least 16 characters.
  [CRITICAL] Weak password on 'Personal Email'
             Score 33/100 (very weak); cracked in 15.5 days. Use at least 16 characters.
  [LOW     ] No two-factor secret for 'Personal Email'
             Store a TOTP seed or enable 2FA at the provider.
```

### Create a real vault

```bash
# Create the vault (the password is read from stdin — never echoed to your shell history)
echo 'my-master-password' | python3 -m vaultguard.cli init --master-stdin

# Add an entry, letting VAULTGUARD generate a 24-character password
python3 -m vaultguard.cli --master 'my-master-password' add GitHub \
    --username raza.dev --url https://github.com \
    --category work --tags dev,work --generate --length 24
#   Added 'GitHub' [3f9a1c2b7e04]  (password generated)
```

### Audit and repair

```bash
# Full hygiene report
python3 -m vaultguard.cli --master 'my-master-password' audit

# Show only the weak entries
python3 -m vaultguard.cli --master 'my-master-password' search --weak

# Score a password without storing it
python3 -m vaultguard.cli check 'correct horse battery staple'
#   Score        : 14/100  (F · very weak)
#   Entropy      : 39.9 bits
#   Crack time   : 15.5 days
#   Suggestions  : …

python3 -m vaultguard.cli check 'Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6'
#   Score        : 100/100  (A+ · fortress)
#   Entropy      : 135.5 bits
```

### Everyday commands

```bash
V="--vault ~/.vaultguard/vault.json --master 'my-master-password'"

python3 -m vaultguard.cli $V list                     # all entries (masked)
python3 -m vaultguard.cli $V get 3f9a1c2b7e04         # one entry
python3 -m vaultguard.cli $V reveal 3f9a1c2b7e04      # print the secret
python3 -m vaultguard.cli $V stats                    # aggregate statistics
python3 -m vaultguard.cli $V search github --json     # machine-readable
python3 -m vaultguard.cli $V generate passphrase -w 5 # a memorable secret
python3 -m vaultguard.cli $V backup ~/backup/vault.json
python3 -m vaultguard.cli $V rekey --new-master 'a-new-longer-master'
```

### As a Python library

```python
from vaultguard import VaultEngine

vault = VaultEngine("~/.vaultguard/vault.json")
vault.create("my-master-password")

vault.add(title="GitHub", username="raza.dev", generate=True, length=24)
vault.add(title="Bank", username="raza-8842", password="Xk9#mQ2vLp7$Zw4Rt8Nb")

report = vault.audit()
print(f"Health {report.score}/100 ({report.grade})")
for finding in report.findings:
    print(f"  [{finding.severity}] {finding.title}")

vault.lock()
```

### REST API

```bash
pip install -e ".[api]"
uvicorn vaultguard.api:app --reload      # → http://localhost:8000/docs
```

```bash
# Build a demo vault and read its report
curl -s -X POST http://localhost:8000/demo | jq '.audit.score'

# Audit a vault
curl -s -X POST http://localhost:8000/vault/audit \
  -H 'Content-Type: application/json' \
  -d '{"master_password":"my-master-password","vault_path":"/tmp/vault.json"}'

# Generate a password
curl -s -X POST http://localhost:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"kind":"password","length":28}'

# List entries — always masked
curl -s -X POST http://localhost:8000/vault/entries \
  -H 'Content-Type: application/json' \
  -d '{"master_password":"my-master-password","vault_path":"/tmp/vault.json"}'
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness, preferred cipher, AES-GCM availability |
| `/persona` | GET | The VAULTGUARD persona/config document |
| `/categories` | GET | Valid entry categories |
| `/vault/create` | POST | Create a vault (optionally seed demo data) |
| `/vault/info` | GET | Metadata — works **while locked** |
| `/vault/unlock` | POST | Verify a master password |
| `/vault/audit` | POST | Run the security audit |
| `/vault/stats` | POST | Aggregates + duplicate groups + health score |
| `/vault/entries` | POST | List / search / filter entries |
| `/vault/entries/add` | POST | Add an entry (optionally generate the password) |
| `/vault/entries/get` | POST | Fetch one entry (`reveal: true` for plaintext) |
| `/vault/entries/update` | POST | Patch an entry |
| `/vault/entries/delete` | POST | Delete an entry |
| `/generate` | POST | Generate password / passphrase / PIN |
| `/check` | POST | Score an arbitrary password |
| `/demo` | POST | Build a throwaway vault and return a full report |

Every endpoint is **stateless**: the master password is supplied per request, the vault is
opened, mutated and re-encrypted inside that request, and nothing is cached.

---

## 🔧 Configuration

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `VAULTGUARD_PATH` | `~/.vaultguard/vault.json` | Vault file location |
| `VAULTGUARD_MASTER` | *(unset)* | Master password (convenient; `--master-stdin` is safer) |
| `PORT` | `8000` | Uvicorn port for the REST API |

### CLI global flags

| Flag | Purpose |
|---|---|
| `--vault PATH` | Override the vault path |
| `--master PASS` | Supply the master password |
| `--master-stdin` | Read it from stdin — **recommended for scripts** |
| `--iterations N` | PBKDF2 iteration override (demos/tests; minimum 10,000) |
| `--json` | Machine-readable output on every command |

Global flags are accepted **before or after** the command name.

### Engine constants

| Setting | Default | Where |
|---|---|---|
| PBKDF2 iterations | `200_000` | `vaultguard/crypto.py` → `DEFAULT_ITERATIONS` |
| Salt / nonce size | 16 bytes each | `vaultguard/crypto.py` |
| Password minimum length | `8` | `vaultguard/generator.py` → `ABSOLUTE_MIN_LENGTH` |
| Passphrase minimum words | `3` | `vaultguard/generator.py` → `WORDS_MIN` |
| Weak-password threshold | score `< 55` | `vaultguard/audit.py` → `WEAK_THRESHOLD` |
| Stale-password age | `365` days | `vaultguard/audit.py` → `STALE_DAYS` |
| Audit category weights | see below | `vaultguard/audit.py` → `WEIGHTS` |
| Guesses per second | `1e10` | `vaultguard/strength.py` → `estimate_strength` |
| Vault file mode | `0600` | `vaultguard/storage.py` → `FILE_MODE` |

### Audit weights

| Category | Weight | Trigger |
|---|---|---|
| Weak passwords | 35 | entropy score < 55 |
| Reused passwords | 25 | same secret on 2+ entries |
| Stale passwords | 15 | older than 365 days |
| Missing two-factor | 10 | no TOTP seed recorded |
| Duplicate titles | 5 | two entries share a title |
| Missing identifier | 5 | login with no username/URL |
| Rotation neglect | 5 | nothing rotated in a year |

Deductions are scaled by prevalence, so one weak password in fifty costs less than one
in three.

---

## 🛡 Security Model

**What VAULTGUARD protects against**

- *Stolen vault file* — the attacker gets ciphertext and a salt; without the master
  password the payload is inaccessible, and 200k PBKDF2 iterations make offline guessing
  expensive.
- *Tampering* — the header is authenticated. Editing the salt, iteration count or cipher
  name causes decryption to fail rather than silently deriving a different key.
- *Bit-flipping* — encrypt-then-MAC over `aad ‖ nonce ‖ ciphertext` (and GCM's native tag)
  rejects any modification.
- *Shoulder-surfing* — list/get render `abc••••••••` unless you explicitly reveal.
- *Crash-corruption* — atomic writes mean a partial save never replaces a good vault.

**What it deliberately does NOT do**

- No network calls. Ever. There is no breach-check API and no sync.
- No password recovery. The master password is unrecoverable **by design** — there is no
  backdoor, no reset token, no support override.
- No plaintext on disk. The only exception is `export`, which is explicit and warns you.

**Operational guidance**

- Prefer `--master-stdin` or an interactive prompt over `--master` (shell history!).
- Back the vault up — `backup` copies ciphertext, so the copy is safe to store anywhere.
- Rotate the master password with `rekey`; it re-encrypts with a fresh salt.
- Keep `cryptography` installed for AES-256-GCM; the stdlib fallback is sound but slower.

---

## 🧪 Testing

```bash
pip install pytest
python3 -m pytest tests/ -q
```

```
265 passed in 15.89s
```

The suite runs **fully offline** and covers:

| Module | Coverage |
|---|---|
| `test_crypto.py` | base64 codecs, KDF determinism, AEAD round-trips (both ciphers), tamper detection on nonce/tag/ciphertext/AAD, verifier tokens, key-entropy diagnostics |
| `test_storage.py` | vault format & versioning, `0600` permissions, no plaintext on disk, fresh nonce per save, atomic writes, header tampering, backup fidelity |
| `test_generator_strength.py` | class guarantees, ambiguity exclusion, seeded reproducibility, passphrase/PIN rules, entropy scoring, penalty detection, score ordering |
| `test_audit.py` | every finding kind, severity escalation, prevalence-scaled deductions, grade mapping, determinism |
| `test_engine.py` | lifecycle, auth failures, rekey, CRUD, search/sort/filter, duplicates, generation, import/export round-trips |
| `test_cli.py` | argument surface, JSON output, exit codes (0/1/2), non-interactive use |
| `test_api.py` | all endpoints incl. 401/404/409/422 error paths and masking guarantees |

---

## ☁️ Deployment

### 1 · Local / self-hosted (recommended)

The vault is a file. Run the CLI or library wherever the file lives — no server needed.

```bash
python3 -m vaultguard.cli --vault /srv/vault.json --master-stdin list
```

### 2 · REST API on a VPS or container

```bash
pip install -e ".[api]"
VAULTGUARD_PATH=/data/vault.json uvicorn vaultguard.api:app --host 0.0.0.0 --port 8000
```

> ⚠️ **Only expose the API behind TLS and authentication.** The endpoints accept the master
> password per request; anyone who can reach the port can attempt to unlock the vault.
> For a single-user deployment, keep it bound to `127.0.0.1` and reach it over SSH.

**Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[api]"
ENV VAULTGUARD_PATH=/data/vault.json
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "vaultguard.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t vaultguard .
docker run -d -p 8000:8000 -v vaultguard-data:/data vaultguard
```

### 3 · Browser demo (static hosting)

`web-live/` is a **fully client-side** build — the same PBKDF2 + AES-GCM design running on
WebCrypto. It needs no server and no backend, so it deploys as static files to GitHub Pages,
Netlify, Cloudflare Pages or any bucket. Your data never leaves the browser tab.

### 4 · Serverless (Vercel)

`vercel.json` + `api/index.py` are included so the FastAPI app can run as a Vercel Python
function:

```bash
npm i -g vercel && vercel login
cd password-vault-agent && vercel --prod
```

Or import the repository in the Vercel dashboard with **Root Directory = `password-vault-agent`**.

> Serverless filesystems are ephemeral, so pass an explicit `vault_path` on a mounted
> volume or treat the API as stateless (create → use → export). For a persistent vault,
> prefer the self-hosted or container options above.

---

## 📁 Project Structure

```
password-vault-agent/
├── vaultguard/
│   ├── __init__.py          # public API surface
│   ├── crypto.py            # PBKDF2 KDF + AEAD (GCM & stdlib fallback), fingerprints
│   ├── models.py            # VaultEntry, masking, timestamps, aggregation
│   ├── storage.py           # vault file format, atomic persistence, rekey, backup
│   ├── engine.py            # VaultEngine — lifecycle, CRUD, search, import/export
│   ├── generator.py         # password / passphrase / PIN generation
│   ├── strength.py          # entropy scoring, penalties, crack-time estimation
│   ├── audit.py             # weighted hygiene scoring and findings
│   ├── cli.py               # argparse CLI, 18 commands, JSON output
│   └── api.py               # FastAPI REST wrapper
├── tests/                   # 265 offline pytest tests
├── web-live/                # static browser vault demo (WebCrypto)
├── vaultguard_persona.json  # agent persona + config document
├── vercel.json              # serverless routing
├── api/index.py             # serverless entrypoint
├── pyproject.toml           # packaging + console script
├── requirements.txt         # optional extras
└── README.md
```

---

## ❓ FAQ

**Can I recover a forgotten master password?**
No — by design. There is no backdoor, reset token or support override. Keep the vault backed
up and store the master password in a safe place (ideally in your head plus one sealed copy).

**Is the stdlib cipher safe if `cryptography` is missing?**
It is a standard encrypt-then-MAC construction over HMAC-SHA256 with a separate
encryption and MAC subkey, and the header is bound as associated data. It is sound, but
AES-256-GCM is preferred for constant-time, hardware-accelerated operation — install the
`aes` extra in production.

**What exactly gets written to disk?**
One JSON file containing the format marker, KDF parameters, cipher name, an encrypted
verifier token and the encrypted entry list. Run `json.tool` on a test vault and you will
see no plaintext titles or passwords.

**Does it phone home?**
No. There is no HTTP client import in the core engine — the only network code in the whole
project is the optional FastAPI server that *you* start.

**How does it detect reused passwords without storing them twice?**
Each password gets a keyed PBKDF2 fingerprint. Two entries with the same password share a
fingerprint, so reuse is detectable while the plaintext is stored exactly once.

---

## 📜 License

MIT — see the repository root for details.

**Disclaimer:** VAULTGUARD is a security tool, but no software is infallible. It is provided
as-is, without warranty. Test it against your own threat model and keep independent,
encrypted backups of anything you cannot afford to lose.

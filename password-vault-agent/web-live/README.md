# 🔐 VAULTGUARD — Live Browser Demo

> The same vault, running entirely inside your browser tab. No server. No uploads.

This folder is the **fully client-side** build of the [VAULTGUARD password vault agent](../README.md).
It exists so the agent can be demonstrated live as a static site — GitHub Pages, Netlify,
Cloudflare Pages or any plain bucket — with **zero backend**.

---

## What makes this build different

The Python engine (`vaultguard/`) and this build share **one vault format**. A vault file
written by the browser opens with the CLI, and vice-versa.

| Concern | Python engine | Browser build |
|---|---|---|
| Key derivation | `hashlib.pbkdf2_hmac` | `crypto.subtle.deriveKey` (PBKDF2) |
| Cipher | AES-256-GCM (`cryptography`) | AES-256-GCM (WebCrypto) |
| Fallback cipher | HMAC-SHA256 CTR + encrypt-then-MAC | *not applicable — WebCrypto is always present* |
| Reuse detection | keyed PBKDF2 fingerprint | in-tab FNV digest *(demo only)* |
| Storage | a `0600` file on disk | your ~~browser~~ **tab memory** + a manual download |
| Network calls | none | **none** |

> ⚠️ **The demo does not persist automatically.** For safety and simplicity the vault lives
> in the tab's memory: clicking **Save encrypted** downloads the encrypted document, and
> **restore an encrypted backup** loads it back. Reloading the page clears everything. That
> is deliberate — a demo should never quietly leave credentials in `localStorage`.

---

## Why it is safe to click

- **No network code exists in this build.** Open `vault-engine.js` and search for `fetch`,
  `XMLHttpRequest`, `WebSocket`, `sendBeacon` — there are none.
- **The header is authenticated.** The plaintext header (`format`, `version`, `kdf`,
  `cipher`) is fed to AES-GCM as *additional data*. Editing the salt or iteration count
  in a backup file makes decryption throw rather than silently deriving a different key.
- **Tampering fails loudly.** Flipping any bit of the nonce, ciphertext or tag aborts
  decryption with `authentication failed`.
- **Your master password never leaves the tab** and is never written anywhere.

The sample entries (`GitHub`, `Personal Email`, `Bank Portal`, `Home WiFi`) are synthetic
demo data with deliberately weak and reused passwords, so the audit has something to find.
**Never** put a real credential into the demo — and if you do, treat it as burned.

---

## Files

```
web-live/
├── index.html        JARVIS-style dark HUD — session, vault, add-entry and findings
├── style.css         holographic theme: scan grid, glowing accents, animated status light
├── vault-engine.js   the ported engine — KDF, AEAD, strength scoring, audit, generators
└── app.js            UI controller — rendering, events, no network
```

`vault-engine.js` is a real port, not a mock. It reproduces the Python engine's scoring
rules exactly: character-pool entropy, repetition/sequence/dictionary/date penalties, the
weighted audit deductions, the A+…F grade bands, and the crack-time model at 10¹⁰ guesses
per second.

---

## Run it

### Locally

```bash
cd password-vault-agent/web-live
python3 -m http.server 8000
# → http://localhost:8000
```

Open the page over `http://localhost` (or HTTPS). **WebCrypto is restricted to secure
contexts** — opening `index.html` via `file://` in some browsers will disable it, and the
HUD will tell you so instead of failing silently.

### On GitHub Pages

This folder is served straight from the repository:

```
https://raza077-coder.github.io/daily-agents/password-vault-agent/web-live/
```

### Anywhere else

It is a static folder. Drop it on Netlify, Cloudflare Pages, S3 + CloudFront, or your own
web server — no build step, no environment variables, no runtime.

---

## Using the HUD

1. **Create vault** — pick a master password (8+ characters). Four sample entries load.
2. **Watch the audit** — the score ring, entry counts and severity-ranked findings update
   live on every change.
3. **Add an entry** — leave the password blank and press **generate** for a 24-character
   secret, or type your own.
4. **Rotate a weak one** — press **rotate** on any entry for a fresh 24-character password
   and watch the score climb.
5. **Save encrypted** — downloads `vaultguard-vault.json`. Verify it in a terminal:
   ```bash
   python3 -m json.tool vaultguard-vault.json | head -20   # no plaintext titles or passwords
   ```
6. **Restore** — lock the vault, then pick the saved file to unlock it again.

---

## Verifying the no-network claim

```bash
grep -nE "fetch|XMLHttpRequest|WebSocket|sendBeacon|navigator\.send" web-live/*.js
# (no output)
```

Or open DevTools → Network while using the app: after the page loads, the request list
stays empty.

---

## Interoperability with the Python engine

A vault written by the browser uses the same document shape:

```json
{
  "format": "vaultguard",
  "version": 1,
  "kdf": { "algo": "pbkdf2-hmac-sha256", "iterations": 200000, "salt": "…" },
  "cipher": "aes-256-gcm",
  "verifier": { "cipher": "aes-256-gcm", "nonce": "…", "ct": "…" },
  "payload":  { "cipher": "aes-256-gcm", "nonce": "…", "ct": "…" },
  "meta": { "created_at": "…", "updated_at": "…", "entry_count": 4, "app": "VAULTGUARD" }
}
```

The demo lowers PBKDF2 to **50,000** iterations so vault creation feels instant; set a
higher count (or use the CLI) for real credentials. To open a demo vault in Python:

```bash
cd password-vault-agent
python3 -m vaultguard.cli --vault /path/to/vaultguard-vault.json \
    --master 'your-master-password' stats
```

> Note: the demo's duplicate-password detection uses a fast in-tab digest, so the reuse
> *groups* it reports may differ from the Python engine's keyed fingerprints. Both detect
> duplicates; only the CLI's fingerprints are collision-resistant.

---

## License

MIT — part of the [daily-agents](https://github.com/Raza077-coder/daily-agents) collection.

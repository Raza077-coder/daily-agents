/* ==========================================================================
 * VAULTGUARD — browser engine (WebCrypto)
 * --------------------------------------------------------------------------
 * A faithful port of the Python `vaultguard` engine to the Web Crypto API.
 *
 *   KDF    PBKDF2-HMAC-SHA256, 200,000 iterations (or a fast count for demos)
 *   AEAD   AES-256-GCM, 128-bit tag, header bound as additional data
 *
 * The on-disk document is byte-compatible with the Python implementation:
 *
 *   {
 *     "format": "vaultguard", "version": 1,
 *     "kdf":    { "algo": "pbkdf2-hmac-sha256", "iterations": 200000, "salt": "…" },
 *     "cipher": "aes-256-gcm",
 *     "verifier": { "cipher", "nonce", "ct" },
 *     "payload":  { "cipher", "nonce", "ct" },
 *     "meta": { … }
 *   }
 *
 * `ct` carries the GCM tag appended, exactly as `cryptography`'s AESGCM and
 * WebCrypto both produce it — so a vault written here opens with
 * `python -m vaultguard.cli` and vice-versa.
 *
 * NOTHING in this file performs a network request. There is no fetch(), no
 * XMLHttpRequest, no WebSocket, no beacon.
 * ========================================================================== */

(function (global) {
  'use strict';

  const FORMAT = 'vaultguard';
  const VERSION = 1;
  const KDF_ALGO = 'pbkdf2-hmac-sha256';
  const CIPHER = 'aes-256-gcm';
  const DEFAULT_ITERATIONS = 200000;
  const FAST_ITERATIONS = 50000;   // used by the in-browser demo for snappiness
  const KEY_BYTES = 32;
  const SALT_BYTES = 16;
  const NONCE_BYTES = 12;          // GCM standard IV length
  const TAG_BITS = 128;
  const VERIFY_TOKEN = 'vaultguard-verify-v1';

  const CATEGORIES = [
    'login', 'email', 'banking', 'social', 'work',
    'server', 'api', 'wifi', 'secure-note', 'other',
  ];

  const AUDIT_WEIGHTS = {
    weak: 35, reused: 25, stale: 15,
    no_2fa: 10, duplicate_title: 5, missing_identifier: 5, rotation: 5,
  };
  const WEAK_THRESHOLD = 55;
  const STALE_DAYS = 365;

  /* ── encoding ─────────────────────────────────────────────────────────── */
  function b64e(bytes) {
    let s = '';
    const arr = new Uint8Array(bytes);
    for (let i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function b64d(text) {
    let s = String(text).replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  const enc = new TextEncoder();
  const dec = new TextDecoder();

  /* ── crypto primitives ────────────────────────────────────────────────── */
  async function deriveKey(master, salt, iterations) {
    const material = await crypto.subtle.importKey(
      'raw', enc.encode(master), 'PBKDF2', false, ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt: salt, iterations: iterations, hash: 'SHA-256' },
      material,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  /* The header is authenticated: editing the salt, iteration count or cipher
   * name makes decryption fail instead of silently deriving another key.
   * Must match Python's json.dumps(sort_keys=True, separators=(",",":"))  */
  function headerBytes(header) {
    const material = {
      format: header.format,
      version: header.version,
      kdf: header.kdf,
      cipher: header.cipher,
    };
    return enc.encode(JSON.stringify(material));
  }

  async function seal(key, plaintext, aad) {
    const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
    const ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: nonce, additionalData: aad, tagLength: TAG_BITS },
      key,
      plaintext
    );
    return { cipher: CIPHER, nonce: b64e(nonce), ct: b64e(ct) };
  }

  async function open(key, blob, aad) {
    if (!blob || !blob.nonce || !blob.ct) throw new Error('payload is malformed');
    try {
      const pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: b64d(blob.nonce), additionalData: aad, tagLength: TAG_BITS },
        key,
        b64d(blob.ct)
      );
      return new Uint8Array(pt);
    } catch (e) {
      throw new Error('authentication failed — wrong key or tampered vault');
    }
  }

  /* ── strength estimation (port of vaultguard/strength.py) ────────────── */
  const LOWER = 'abcdefghijklmnopqrstuvwxyz';
  const UPPER = LOWER.toUpperCase();
  const DIGITS = '0123456789';
  const SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/\\|~`'\"<>";
  const ANDROID_WORD = 'password'; // a dictionary entry, not a credential

  const COMMON = [
    ANDROID_WORD, '123456', '123456789', 'qwerty', 'abc123', 'letmein', 'welcome',
    'monkey', 'dragon', 'football', 'iloveyou', 'admin', 'login', 'master',
    'sunshine', 'princess', 'trustno1', 'qwerty123', 'passw0rd', 'zaq12wsx',
    'baseball', 'superman', 'batman', 'starwars', 'freedom', 'whatever', 'ninja',
    'shadow', 'michael', 'jennifer', 'hunter2', 'changeme', 'secret', 'root',
  ];
  const KEYBOARD_ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm', '1234567890'];
  const SEQUENCES = ['abcdefghijklmnopqrstuvwxyz', '0123456789'];

  function characterPool(pw) {
    const classes = [];
    let pool = 0;
    if ([...pw].some(c => LOWER.includes(c))) { pool += 26; classes.push('lowercase'); }
    if ([...pw].some(c => UPPER.includes(c))) { pool += 26; classes.push('uppercase'); }
    if ([...pw].some(c => DIGITS.includes(c))) { pool += 10; classes.push('digits'); }
    if ([...pw].some(c => SYMBOLS.includes(c))) { pool += SYMBOLS.length; classes.push('symbols'); }
    if ([...pw].some(c => c.charCodeAt(0) > 127)) { pool += 128; classes.push('unicode'); }
    return { pool: Math.max(pool, 1), classes };
  }

  function repetitionPenalty(pw) {
    let penalty = 1, hits = 0;
    const runs = pw.match(/(.)\1{2,}/g);
    if (runs) { hits = runs.length; penalty *= Math.pow(0.6, hits); }
    for (const size of [1, 2, 3, 4]) {
      const block = pw.slice(0, size);
      if (size && pw.length >= size * 3 && block.repeat(Math.floor(pw.length / size)) === pw) {
        penalty *= 0.5; break;
      }
    }
    return { penalty, hits };
  }

  function sequencePenalty(pw) {
    const low = pw.toLowerCase();
    let hits = 0;
    for (const seq of SEQUENCES) {
      for (let size = 4; size <= seq.length; size++) {
        for (let s = 0; s + size <= seq.length; s++) {
          const chunk = seq.slice(s, s + size);
          const rev = [...chunk].reverse().join('');
          if (low.includes(chunk) || low.includes(rev)) hits++;
        }
      }
    }
    for (const row of KEYBOARD_ROWS) {
      for (let size = 4; size <= row.length; size++) {
        for (let s = 0; s + size <= row.length; s++) {
          if (low.includes(row.slice(s, s + size))) hits++;
        }
      }
    }
    return { penalty: hits ? Math.pow(0.7, hits) : 1, hits };
  }

  function dictionaryPenalty(pw) {
    const low = pw.toLowerCase();
    const found = COMMON.filter(c => low.includes(c));
    return { penalty: found.length ? Math.pow(0.25, found.length) : 1, found };
  }

  function datePenalty(pw) {
    let hits = (pw.match(/(19|20)\d{2}/g) || []).length;
    hits += (pw.match(/\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b/g) || []).length;
    return { penalty: hits ? Math.pow(0.8, hits) : 1, hits };
  }

  function humaniseDuration(seconds) {
    if (!isFinite(seconds) || seconds < 1) return 'instantly';
    const units = [
      ['century', 'centuries', 3.156e9],
      ['year', 'years', 3.156e7],
      ['month', 'months', 2.592e6],
      ['day', 'days', 86400],
      ['hour', 'hours', 3600],
      ['minute', 'minutes', 60],
      ['second', 'seconds', 1],
    ];
    for (const [sing, plur, size] of units) {
      if (seconds >= size) {
        const v = seconds / size;
        const unit = (v >= 1 && v < 2) ? sing : plur;
        const shown = v >= 1000 ? v.toExponential(1) : v.toFixed(1);
        return `${shown} ${unit}`;
      }
    }
    return 'instantly';
  }

  function gradeFor(score) {
    if (score >= 90) return ['A+', 'fortress'];
    if (score >= 80) return ['A', 'very strong'];
    if (score >= 70) return ['B', 'strong'];
    if (score >= 55) return ['C', 'reasonable'];
    if (score >= 40) return ['D', 'weak'];
    return ['F', 'very weak'];
  }

  function estimateStrength(pw, opts) {
    const o = opts || {};
    const guesses = o.guessesPerSecond || 1e10;
    const ageDays = o.ageDays || 0;
    const reused = !!o.reused;

    const report = {
      length: (pw || '').length, pool_size: 0, entropy_bits: 0, score: 0,
      grade: 'F', label: 'very weak', crack_time_seconds: 0, crack_time_human: 'instantly',
      character_classes: [], penalties: [], suggestions: [],
      is_common: false, reused, age_days: ageDays,
    };
    if (!pw) {
      report.label = 'empty';
      report.suggestions.push('Set a password for this entry.');
      return report;
    }

    const { pool, classes } = characterPool(pw);
    report.pool_size = pool;
    report.character_classes = classes;

    let penalty = 1;
    const rep = repetitionPenalty(pw);
    if (rep.hits) { penalty *= rep.penalty; report.penalties.push({ reason: 'repeated characters', hits: rep.hits }); }

    const seq = sequencePenalty(pw);
    if (seq.hits) { penalty *= seq.penalty; report.penalties.push({ reason: 'predictable sequence', hits: seq.hits }); }

    const dict = dictionaryPenalty(pw);
    if (dict.found.length) {
      penalty *= dict.penalty;
      report.penalties.push({ reason: 'dictionary word', hits: dict.found.length, matched: dict.found.slice(0, 3) });
    }

    const date = datePenalty(pw);
    if (date.hits) { penalty *= date.penalty; report.penalties.push({ reason: 'date-like fragment', hits: date.hits }); }

    if (new Set(pw).size <= Math.max(3, Math.floor(pw.length / 4))) {
      penalty *= 0.7;
      report.penalties.push({ reason: 'tiny character variety', hits: new Set(pw).size });
    }

    const entropy = pw.length * Math.log2(pool) * penalty;
    report.entropy_bits = Math.round(entropy * 10) / 10;
    report.is_common = COMMON.some(c => pw.toLowerCase().includes(c));

    let score = Math.max(0, Math.min(100, Math.trunc((entropy - 30) * (100 / 70))));
    if (reused) score = Math.trunc(score * 0.55);
    if (ageDays > 365) score = Math.trunc(score * 0.9);
    report.score = score;
    [report.grade, report.label] = gradeFor(score);

    report.crack_time_seconds = Math.pow(2, entropy) / Math.max(guesses, 1);
    report.crack_time_human = humaniseDuration(report.crack_time_seconds);

    if (pw.length < 16) report.suggestions.push('Use at least 16 characters.');
    if (!classes.includes('uppercase') || !classes.includes('lowercase')) report.suggestions.push('Mix upper and lower case letters.');
    if (!classes.includes('digits')) report.suggestions.push('Add digits.');
    if (!classes.includes('symbols')) report.suggestions.push('Add a symbol or two.');
    if (report.is_common) report.suggestions.push('Replace dictionary words with a random passphrase.');
    if (reused) report.suggestions.push('Reused password — give this account a unique secret.');
    if (ageDays > 365) report.suggestions.push(`Rotate this password (last changed ${ageDays} days ago).`);
    if (!report.suggestions.length) report.suggestions.push('Nothing to improve. Excellent hygiene.');
    return report;
  }

  /* ── generators (port of vaultguard/generator.py) ────────────────────── */
  const WORDLIST = ('amber anchor atlas aurora basalt beacon bison bramble breeze cactus canyon cedar '
    + 'cinder cobalt comet copper coral crater crystal cypress dahlia delta dune ember falcon fathom '
    + 'fern fjord flint fossil galaxy garnet geyser glacier granite harbor hazel heron hollow indigo '
    + 'ivory jasmine juniper kestrel lagoon lantern larch lichen lotus lunar magnet mango marble meadow '
    + 'mesa meteor mosaic nectar nimbus nordic obsidian onyx orchid osprey oxide pebble penguin pepper '
    + 'phoenix pine plasma pollen prairie prism quartz quill raven reef ridge river saffron sage '
    + 'sapphire savanna sequoia shadow sierra silver solstice sparrow spruce stellar summit sunset '
    + 'talon tempo thistle thunder tundra umber valley velvet vertex violet walnut willow winter '
    + 'zenith zephyr zircon').split(' ');

  const AMBIGUOUS = new Set('Il1O0oB8S5Z2G6'.split(''));

  function randomInt(n) {
    if (n <= 0) return 0;
    // rejection sampling keeps the distribution uniform
    const limit = Math.floor(0x100000000 / n) * n;
    const buf = new Uint32Array(1);
    let v;
    do { crypto.getRandomValues(buf); v = buf[0]; } while (v >= limit);
    return v % n;
  }

  function pick(arr) { return arr[randomInt(arr.length)]; }

  function generatePassword(length, opts) {
    const o = Object.assign(
      { lower: true, upper: true, digits: true, symbols: true, avoidAmbiguous: false, exclude: '' },
      opts || {}
    );
    if (length < 8) throw new Error('length must be at least 8');
    const excluded = new Set(o.exclude.split(''));

    const pools = [];
    const add = (enabled, chars) => {
      if (!enabled) return;
      let list = chars.split('').filter(c => !excluded.has(c));
      if (o.avoidAmbiguous) list = list.filter(c => !AMBIGUOUS.has(c));
      if (list.length) pools.push(list);
    };
    add(o.lower, LOWER); add(o.upper, UPPER); add(o.digits, DIGITS); add(o.symbols, SYMBOLS);
    if (!pools.length) throw new Error('no character classes selected');
    if (length < pools.length) throw new Error(`length ${length} is too short for ${pools.length} required classes`);

    const combined = pools.flat();
    const out = pools.map(p => pick(p));                    // guarantee each class
    while (out.length < length) out.push(pick(combined));
    for (let i = out.length - 1; i > 0; i--) {              // Fisher-Yates
      const j = randomInt(i + 1);
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out.slice(0, length).join('');
  }

  function generatePassphrase(words, opts) {
    const o = Object.assign({ separator: '-', capitalize: false, addNumber: true }, opts || {});
    if (words < 3) throw new Error('use at least 3 words');
    let chosen = Array.from({ length: words }, () => pick(WORDLIST));
    if (o.capitalize) chosen = chosen.map(w => w[0].toUpperCase() + w.slice(1));
    let phrase = chosen.join(o.separator);
    if (o.addNumber) phrase += o.separator + (randomInt(9000) + 1000);
    return phrase;
  }

  function generatePin(length) {
    if (length < 4) throw new Error('PIN length must be at least 4');
    if (length > 12) throw new Error('cap the PIN at 12 digits');
    let out = String(randomInt(9) + 1);
    while (out.length < length) out += String(randomInt(10));
    return out;
  }

  /* ── helpers ──────────────────────────────────────────────────────────── */
  function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }

  /* Python's round() is banker's rounding (round-half-to-even). Matching it keeps
   * the browser audit score byte-identical to the CLI's for the same entries. */
  function pyRound(x) {
    const floor = Math.floor(x);
    if (Math.abs(x - floor - 0.5) < 1e-9) return (floor % 2 === 0) ? floor : floor + 1;
    return Math.round(x);
  }

  function daysSince(iso) {
    if (!iso) return 0;
    const t = Date.parse(iso);
    if (isNaN(t)) return 0;
    return Math.max(0, Math.floor((Date.now() - t) / 86400000));
  }

  // Non-cryptographic fingerprint — used only to spot duplicate passwords in
  // this browser demo. The Python engine uses a keyed PBKDF2 digest instead.
  function fingerprint(pw) {
    let h1 = 0x811c9dc5, h2 = 0x1000193;
    for (let i = 0; i < pw.length; i++) {
      const c = pw.charCodeAt(i);
      h1 = (h1 ^ c) * 16777619 >>> 0;
      h2 = (h2 + c * (i + 7)) >>> 0;
    }
    return h1.toString(36) + '-' + h2.toString(36);
  }

  function newId() {
    return Array.from(crypto.getRandomValues(new Uint8Array(6)))
      .map(b => b.toString(16).padStart(2, '0')).join('');
  }

  function maskSecret(secret, keep) {
    const k = keep == null ? 3 : keep;
    if (!secret) return '';
    if (secret.length <= k) return '•'.repeat(secret.length);
    return secret.slice(0, k) + '•'.repeat(Math.max(4, secret.length - k));
  }

  /* ── the engine ───────────────────────────────────────────────────────── */
  class VaultEngine {
    constructor(opts) {
      const o = opts || {};
      this.iterations = o.iterations || DEFAULT_ITERATIONS;
      this.key = null;
      this.entries = [];
      this.meta = {};
      this.header = null;
      this.unlocked = false;
    }

    /** Create a fresh vault and leave it unlocked. */
    async create(master) {
      if (!master || master.length < 8) throw new Error('master password must be at least 8 characters');
      const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
      this.key = await deriveKey(master, salt, this.iterations);
      this.header = {
        format: FORMAT,
        version: VERSION,
        kdf: { algo: KDF_ALGO, iterations: this.iterations, salt: b64e(salt) },
        cipher: CIPHER,
        meta: { created_at: nowIso(), updated_at: nowIso(), entry_count: 0, app: 'VAULTGUARD' },
      };
      this.entries = [];
      this.unlocked = true;
      // The verifier lets us check a password cheaply, without decrypting the
      // (potentially large) entry list. It is bound to the ciphertext too.
      this.header.verifier = await seal(this.key, enc.encode(VERIFY_TOKEN), enc.encode('verify'));
      await this._reseal();
      return this.describe();
    }

    /** Open an existing vault document with the master password. */
    async unlock(master, document) {
      const doc = document || this.header;
      if (!doc || doc.format !== FORMAT) throw new Error('not a VAULTGUARD vault');
      if (Number(doc.version) > VERSION) throw new Error('vault is newer than this build supports');
      const kdf = doc.kdf || {};
      const iterations = Number(kdf.iterations);
      if (!kdf.salt || !iterations) throw new Error('vault header has invalid KDF parameters');

      const key = await deriveKey(master, b64d(kdf.salt), iterations);
      const verifyBytes = await open(key, doc.verifier, enc.encode('verify'));
      if (dec.decode(verifyBytes) !== VERIFY_TOKEN) throw new Error('incorrect master password');

      const raw = await open(key, doc.payload, headerBytes(doc));
      let payload;
      try { payload = JSON.parse(dec.decode(raw)); }
      catch (e) { throw new Error('vault payload is not valid JSON'); }
      if (!payload || !Array.isArray(payload.entries)) throw new Error('vault payload has an unexpected shape');

      this.key = key;
      this.iterations = iterations;
      this.header = doc;
      this.entries = payload.entries.map(normaliseEntry);
      this.unlocked = true;
      return { status: 'unlocked', entries: this.entries.length, cipher: doc.cipher };
    }

    lock() {
      this.key = null;
      this.entries = [];
      this.unlocked = false;
      return { status: 'locked' };
    }

    _requireUnlocked() {
      if (!this.unlocked || !this.key) throw new Error('vault is locked — unlock it first');
    }

    /** Re-encrypt the whole vault with a fresh nonce and update the header. */
    async _reseal() {
      this._requireUnlocked();
      this.header.cipher = CIPHER;
      this.header.meta = Object.assign({}, this.header.meta, {
        updated_at: nowIso(),
        entry_count: this.entries.length,
      });
      const body = JSON.stringify({ entries: this.entries, meta: {} });
      this.header.payload = await seal(this.key, enc.encode(body), headerBytes(this.header));
      return this.header;
    }

    async save() { return this._reseal(); }

    /** Export the encrypted document (safe to download — it stays locked). */
    serialise() {
      this._requireUnlocked();
      return JSON.stringify(this.header, null, 2) + '\n';
    }

    /* -- CRUD ---------------------------------------------------------- */
    add(input) {
      this._requireUnlocked();
      const entry = normaliseEntry(Object.assign({
        id: newId(),
        created_at: nowIso(),
        updated_at: nowIso(),
        password_updated_at: nowIso(),
      }, input));

      if (!entry.password && input && input.generate) {
        entry.password = generatePassword(input.length || 20);
      }
      entry.password_hint = entry.password ? fingerprint(entry.password) : '';
      this.entries.push(entry);
      return entry;
    }

    update(id, changes) {
      this._requireUnlocked();
      const entry = this.find(id);
      let rotated = false;
      Object.keys(changes || {}).forEach(k => {
        if (k === 'id' || k === 'created_at') return;
        const v = changes[k];
        if (v === undefined) return;
        if ((k === 'password' || k === 'totp') && v !== entry[k]) rotated = true;
        entry[k] = v;
      });
      if (changes && changes.tags) entry.tags = normaliseTags(changes.tags);
      if (rotated) {
        entry.password_hint = entry.password ? fingerprint(entry.password) : '';
        entry.password_updated_at = nowIso();
      }
      entry.updated_at = nowIso();
      return entry;
    }

    remove(id) {
      this._requireUnlocked();
      const entry = this.find(id);
      this.entries = this.entries.filter(e => e.id !== entry.id);
      return entry;
    }

    find(id) {
      this._requireUnlocked();
      return this.entries.find(e => e.id === id)
        || this.entries.find(e => e.title.toLowerCase() === String(id).toLowerCase())
        || (() => { throw new Error(`no entry matching '${id}'`); })();
    }

    get(id) { return this.find(id); }

    /* -- queries ------------------------------------------------------- */
    search(q) {
      this._requireUnlocked();
      const o = q || {};
      let out = this.entries.slice();

      if (o.text) {
        const needle = o.text.trim().toLowerCase();
        out = out.filter(e => [e.title, e.username, e.url, e.notes, e.category, (e.tags || []).join(' ')]
          .join(' ').toLowerCase().includes(needle));
      }
      if (o.category) out = out.filter(e => e.category === o.category);
      if (o.tag) out = out.filter(e => (e.tags || []).includes(o.tag));
      if (o.weakOnly) out = out.filter(e => estimateStrength(e.password, { reused: this._isReused(e) }).score < WEAK_THRESHOLD);
      if (o.staleOnly) out = out.filter(e => this.ageOf(e) > STALE_DAYS);

      const sorters = {
        title: e => e.title.toLowerCase(),
        username: e => (e.username || '').toLowerCase(),
        category: e => e.category,
        age: e => this.ageOf(e),
        updated: e => e.updated_at || '',
        created: e => e.created_at || '',
      };
      out.sort((a, b) => {
        const f = sorters[o.sort] || sorters.title;
        const x = f(a), y = f(b);
        return (x < y ? -1 : x > y ? 1 : 0) * (o.descending ? -1 : 1);
      });
      return o.limit ? out.slice(0, o.limit) : out;
    }

    ageOf(entry) { return daysSince(entry.password_updated_at); }

    _isReused(entry) {
      if (!entry.password_hint) return false;
      return this.entries.filter(e => e.password_hint === entry.password_hint).length > 1;
    }

    duplicates() {
      this._requireUnlocked();
      const groups = {};
      this.entries.forEach(e => {
        if (!e.password_hint) return;
        (groups[e.password_hint] = groups[e.password_hint] || []).push(e);
      });
      return Object.values(groups).filter(g => g.length > 1)
        .map(g => ({ count: g.length, titles: g.map(e => e.title), ids: g.map(e => e.id) }));
    }

    stats() {
      this._requireUnlocked();
      const categories = {}, tags = {};
      this.entries.forEach(e => {
        categories[e.category] = (categories[e.category] || 0) + 1;
        (e.tags || []).forEach(t => { tags[t] = (tags[t] || 0) + 1; });
      });
      return {
        total: this.entries.length,
        favorites: this.entries.filter(e => e.favorite).length,
        with_url: this.entries.filter(e => e.url).length,
        with_totp: this.entries.filter(e => e.totp).length,
        categories, tags,
      };
    }

    /* -- the audit (port of vaultguard/audit.py) ----------------------- */
    audit() {
      this._requireUnlocked();
      const entries = this.entries;
      const report = {
        score: 0, grade: 'F', verdict: 'critical', total_entries: entries.length,
        health: { strong: 0, reasonable: 0, weak: 0, reused: 0, stale: 0, with_2fa: 0 },
        findings: [], deductions: {}, recommendations: [], counts: {},
      };
      if (!entries.length) {
        report.verdict = 'empty';
        report.recommendations = ['Add your first credential to begin tracking hygiene.'];
        return report;
      }

      const findings = [];
      const byHint = {};
      entries.forEach(e => {
        if (e.password_hint) (byHint[e.password_hint] = byHint[e.password_hint] || []).push(e);
      });

      const reusedIds = new Set();
      Object.values(byHint).forEach(group => {
        if (group.length > 1) {
          group.forEach(e => reusedIds.add(e.id));
          findings.push({
            kind: 'reused', severity: 'critical',
            title: `Password reused across ${group.length} entries`,
            detail: `Shared by: ${group.map(e => e.title).sort().slice(0, 4).join(', ')}. One breach exposes all of them.`,
            entry_id: group[0].id,
          });
        }
      });

      const titleCounts = {};
      entries.forEach(e => {
        const k = e.title.trim().toLowerCase();
        titleCounts[k] = (titleCounts[k] || 0) + 1;
      });

      entries.forEach(entry => {
        const age = this.ageOf(entry);
        const reused = reusedIds.has(entry.id);
        const strength = estimateStrength(entry.password, { ageDays: age, reused });

        if (strength.score >= 80) report.health.strong++;
        else if (strength.score >= WEAK_THRESHOLD) report.health.reasonable++;
        else {
          report.health.weak++;
          findings.push({
            kind: 'weak',
            severity: strength.score >= 40 ? 'high' : 'critical',
            title: `Weak password on '${entry.title}'`,
            detail: `Score ${strength.score}/100 (${strength.label}); cracked in ${strength.crack_time_human}. ${strength.suggestions[0] || ''}`.trim(),
            entry_id: entry.id,
          });
        }

        if (reused) report.health.reused++;

        if (age > STALE_DAYS) {
          report.health.stale++;
          findings.push({
            kind: 'stale', severity: 'medium',
            title: `'${entry.title}' password is ${age} days old`,
            detail: `Rotate anything older than ${STALE_DAYS} days.`,
            entry_id: entry.id,
          });
        }

        if (entry.totp) report.health.with_2fa++;
        else if (['login', 'email', 'banking', 'work', 'server', 'api'].includes(entry.category)) {
          findings.push({
            kind: 'no_2fa', severity: 'low',
            title: `No two-factor secret for '${entry.title}'`,
            detail: 'Store a TOTP seed or enable 2FA at the provider.',
            entry_id: entry.id,
          });
        }

        if ((titleCounts[entry.title.trim().toLowerCase()] || 0) > 1) {
          findings.push({
            kind: 'duplicate_title', severity: 'low',
            title: `Duplicate title '${entry.title}'`,
            detail: 'Give each entry a distinct title so lookups stay unambiguous.',
            entry_id: entry.id,
          });
        }

        if (!entry.username && !entry.url && !['wifi', 'secure-note'].includes(entry.category)) {
          findings.push({
            kind: 'missing_identifier', severity: 'low',
            title: `'${entry.title}' has no username or URL`,
            detail: 'Record the account identifier so the entry is actionable.',
            entry_id: entry.id,
          });
        }
      });

      if (entries.every(e => this.ageOf(e) > STALE_DAYS)) {
        findings.push({
          kind: 'rotation', severity: 'medium',
          title: 'Nothing has been rotated in over a year',
          detail: 'Run a rotation sweep across the vault.',
        });
      }

      const byKind = {};
      findings.forEach(f => { byKind[f.kind] = (byKind[f.kind] || 0) + 1; });
      Object.keys(AUDIT_WEIGHTS).forEach(kind => {
        const n = byKind[kind] || 0;
        if (!n) return;
        const ratio = n / Math.max(entries.length, 1);
        report.deductions[kind] = Math.max(1, pyRound(AUDIT_WEIGHTS[kind] * Math.min(1, ratio)));
      });

      report.score = Math.max(0, 100 - Object.values(report.deductions).reduce((a, b) => a + b, 0));
      [report.grade, report.verdict] = (s => {
        if (s >= 95) return ['A+', 'pristine'];
        if (s >= 90) return ['A', 'excellent'];
        if (s >= 80) return ['B', 'good'];
        if (s >= 65) return ['C', 'needs attention'];
        if (s >= 50) return ['D', 'poor'];
        return ['F', 'critical'];
      })(report.score);

      report.findings = findings;
      report.counts = findings.reduce((acc, f) => {
        acc[f.severity] = (acc[f.severity] || 0) + 1; return acc;
      }, { critical: 0, high: 0, medium: 0, low: 0 });

      const recs = [];
      if (report.health.reused) recs.push(`Break up ${report.health.reused} reused password(s) — start with your email and banking logins.`);
      if (report.health.weak) recs.push(`Replace ${report.health.weak} weak password(s) using the built-in generator (20+ characters).`);
      if (report.health.stale) recs.push(`Rotate ${report.health.stale} password(s) that are over a year old.`);
      if (!report.health.with_2fa) recs.push('Enable two-factor authentication and store the TOTP seed for each account.');
      if (findings.some(f => f.kind === 'duplicate_title')) recs.push('Rename duplicate entries so each account is uniquely identifiable.');
      report.recommendations = recs.length ? recs : ['Vault is in great shape. Keep rotating on a yearly cadence.'];
      return report;
    }

    describe() {
      return {
        format: FORMAT,
        version: VERSION,
        cipher: CIPHER,
        kdf: KDF_ALGO,
        iterations: this.iterations,
        entry_count: this.entries.length,
        created_at: (this.header.meta || {}).created_at,
        updated_at: (this.header.meta || {}).updated_at,
        status: this.unlocked ? 'unlocked' : 'locked',
      };
    }
  }

  /* ── normalisation ────────────────────────────────────────────────────── */
  function normaliseTags(tags) {
    const list = Array.isArray(tags) ? tags : String(tags || '').split(',');
    return [...new Set(list.map(t => String(t).trim().toLowerCase()).filter(Boolean))].sort();
  }

  function normaliseEntry(raw) {
    const e = Object.assign({
      id: newId(), title: '', username: '', password: '', url: '', notes: '',
      tags: [], category: 'login', favorite: false, totp: '',
      created_at: nowIso(), updated_at: nowIso(), password_updated_at: nowIso(),
      password_hint: '',
    }, raw || {});
    e.category = String(e.category || 'login').trim().toLowerCase() || 'login';
    e.title = String(e.title || '').trim() || e.username || e.url || 'untitled';
    e.tags = normaliseTags(e.tags);
    return e;
  }

  /* ── demo data ────────────────────────────────────────────────────────── */
  function demoEntries() {
    return [
      {
        title: 'GitHub', username: 'raza.dev', category: 'work', url: 'https://github.com',
        // synthetic demo data, deliberately reused to show the audit
        password: 'Xk9#mQ2vLp7$Zw4Rt8Nb', tags: ['dev', 'work'], favorite: true,
        totp: 'JBSWY3DPEHPK3PXP', password_updated_at: nowIso(),
      },
      {
        title: 'Personal Email', username: 'raza@example.com', category: 'email',
        url: 'https://mail.example.com', password: 'summer2024!', tags: ['personal'],
        password_updated_at: nowIso(),
      },
      {
        title: 'Bank Portal', username: 'raza-8842', category: 'banking',
        url: 'https://bank.example.com', totp: 'KRSXG5CTMVRXEZLU',
        password: 'Xk9#mQ2vLp7$Zw4Rt8Nb',   // deliberately reused with GitHub
        password_updated_at: nowIso(),
      },
      {
        title: 'Home WiFi', username: 'admin', category: 'wifi',
        password: 'qwerty123', tags: ['home'], password_updated_at: nowIso(),
      },
    ];
  }

  /* ── exports ──────────────────────────────────────────────────────────── */
  global.VaultGuard = {
    VaultEngine,
    estimateStrength,
    generatePassword,
    generatePassphrase,
    generatePin,
    demoEntries,
    maskSecret,
    normaliseEntry,
    humaniseDuration,
    CATEGORIES,
    WEAK_THRESHOLD,
    STALE_DAYS,
    AUDIT_WEIGHTS,
    DEFAULT_ITERATIONS,
    FAST_ITERATIONS,
    WORDLIST,
  };
})(window);

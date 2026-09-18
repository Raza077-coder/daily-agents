#!/usr/bin/env node
/**
 * Smoke test for the browser engine, run on Node (no browser required).
 *
 * The web demo is a separate implementation from the Python package, so it
 * needs its own check that it actually redacts. This script loads the generated
 * demo data, applies each shipped policy, and asserts on the results.
 *
 *   node tools/web_smoke.js
 */

const path = require("path");

const PROJECT_ROOT = path.dirname(__dirname);
const VEIL = require(path.join(PROJECT_ROOT, "web-live", "veil-engine.js"));
const data = require(path.join(PROJECT_ROOT, "web-live", "demo-data.js"));
// demo-data.js assigns to `window`; provide one before requiring is impossible
// after the fact, so re-evaluate it into a local object when `window` is absent.
const payload = (typeof window !== "undefined" && window.VEIL_DATA) || data.VEIL_DATA ||
  (() => {
    const fs = require("fs");
    const source = fs.readFileSync(
      path.join(PROJECT_ROOT, "web-live", "demo-data.js"), "utf8");
    const start = source.indexOf("{", source.indexOf("window.VEIL_DATA"));
    const end = source.lastIndexOf("}");
    return JSON.parse(source.slice(start, end + 1));
  })();

let passed = 0;
let failed = 0;

function ok(condition, label) {
  if (condition) {
    passed += 1;
    console.log(`  ok   ${label}`);
  } else {
    failed += 1;
    console.log(`  FAIL ${label}`);
  }
}

console.log("VEIL web engine smoke test\n");

const email = payload.documents.find((d) => d.name === "vendor_email.txt");
const config = payload.documents.find((d) => d.name === "deployment.env");
const log = payload.documents.find((d) => d.name === "app.log");

ok(!!email && !!config && !!log, "all three sample documents are present");
ok(payload.entities.length >= 13, "entity catalogue is populated");
ok(payload.actions.length === 6, "six actions are described");

// 1. Detection finds the obvious things in the email.
const detected = VEIL.detect(email.text, VEIL.defaultPolicy());
const entities = new Set(detected.map((s) => s.entity));
ok(entities.has("EMAIL"), "detects EMAIL");
ok(entities.has("CREDIT_CARD"), "detects CREDIT_CARD");
ok(entities.has("SECRET"), "detects SECRET");
ok(entities.has("IBAN"), "detects IBAN");

// 2. Default redaction removes the real secrets and leaves no undefined/NaN.
const redacted = VEIL.redact(log.text, VEIL.defaultPolicy());
ok(redacted.isRedacted === true, "redact() marks the result as redacted");
ok(redacted.text.indexOf("testkey-ZephyrCove-7193") === -1, "password removed");
ok(redacted.text.indexOf("AKIASYNTHETICKEY0000") === -1, "AWS key removed");
ok(redacted.text.indexOf("alice.chen@brightpath-consulting.com") === -1, "email removed");
ok(redacted.verification && redacted.verification.clean === true,
   "redaction verifies CLEAN");
ok(redacted.text.indexOf("undefined") === -1, "no undefined leaked into the output");
ok(redacted.text.indexOf("NaN") === -1, "no NaN leaked into the output");

// 3. Scan-only must not claim to have redacted.
const scanned = VEIL.scan(log.text, VEIL.defaultPolicy());
ok(scanned.isRedacted === false, "scan() does not claim to redact");
ok(scanned.risk && scanned.risk.level !== "NONE", "scan() still scores the risk");

// 4. The shareable policy keeps the allowlisted address but removes credentials.
const shareablePolicy = VEIL.policyFromSpec(data.policies["shareable.yaml"].spec);
const shared = VEIL.redact(config.text, shareablePolicy);
ok(shared.text.indexOf("support@brightpath-consulting.com") !== -1,
   "shareable policy keeps the allowlisted address");
ok(shared.text.indexOf("AKIASYNTHETICKEY0000") === -1,
   "shareable policy still removes the AWS key");

// 5. Strict policy obliterates everything detectable.
const strictPolicy = VEIL.policyFromSpec(data.policies["strict.yaml"].spec);
const strictOut = VEIL.redact(email.text, strictPolicy);
ok(strictOut.text.indexOf("AKIASYNTHETICKEY0000") === -1, "strict removes the AWS key");
ok(strictOut.text.indexOf("4111 1111 1111 1111") === -1, "strict removes the card");
ok(strictOut.text.indexOf("alice.chen@brightpath-consulting.com") === -1, "strict removes the email");

// 6. Bare dates and placeholders must not be flagged.
const cleanText = "RELEASE_DATE=2026-09-18 and API_KEY=changeme and TOKEN=REPLACE_ME";
const clean = VEIL.redact(cleanText, VEIL.defaultPolicy());
ok(clean.text.indexOf("2026-09-18") !== -1, "a bare date is not mistaken for a phone");
ok(clean.text.indexOf("changeme") !== -1, "the placeholder changeme is not a secret");
ok(clean.text.indexOf("REPLACE_ME") !== -1, "the placeholder REPLACE_ME is not a secret");

// 7. Determinism: the same input twice gives identical output.
const once = VEIL.redact(email.text, shareablePolicy).text;
const twice = VEIL.redact(email.text, shareablePolicy).text;
ok(once === twice, "redaction is deterministic");

// 8. PERSON is opt-in and only fires when enabled.
const personText = "meet Dr. Nadia Rehman tomorrow";
const off = VEIL.redact(personText, VEIL.defaultPolicy());
ok(off.text.indexOf("Nadia Rehman") !== -1, "PERSON stays off by default");
const allPolicy = VEIL.policyFromSpec(
  Object.assign({}, data.policies["strict.yaml"].spec, { entities: ["ALL"] }));
const on = VEIL.redact(personText, allPolicy);
ok(on.text.indexOf("Nadia Rehman") === -1, "PERSON fires when explicitly enabled");

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

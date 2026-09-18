#!/usr/bin/env node
/*
 * Offline smoke test for the browser engine.
 *
 * The README claims the demo makes no network calls and works from `file://`.
 * A claim like that is worth exactly as much as the check behind it, so this
 * test *removes* the network: XMLHttpRequest, fetch, WebSocket and sendBeacon
 * are all replaced with functions that throw. If any part of the engine or the
 * page's data path tried to reach the network, this run would fail loudly.
 *
 * It then drives the engine the way the page does and asserts real values.
 *
 *     node tools/web_smoke.js
 */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const ENGINE = path.join(ROOT, "web-live", "veil-engine.js");
const DATA = path.join(ROOT, "web-live", "demo-data.js");

let failures = [];
let checks = 0;

function ok(condition, label) {
  checks += 1;
  if (!condition) { failures.push(label); }
}

function eq(actual, expected, label) {
  checks += 1;
  if (actual !== expected) {
    failures.push(label + " (expected " + JSON.stringify(expected) +
                  ", got " + JSON.stringify(actual) + ")");
  }
}

/* ------------------------------------------------------------ the network */

const sandbox = {
  XMLHttpRequest: function () { throw new Error("NETWORK ATTEMPTED (XMLHttpRequest)"); },
  fetch: function () { throw new Error("NETWORK ATTEMPTED (fetch)"); },
  WebSocket: function () { throw new Error("NETWORK ATTEMPTED (WebSocket)"); },
  EventSource: function () { throw new Error("NETWORK ATTEMPTED (EventSource)"); },
  navigator: {}
};

global.XMLHttpRequest = sandbox.XMLHttpRequest;
global.fetch = sandbox.fetch;
global.WebSocket = sandbox.WebSocket;
global.EventSource = sandbox.EventSource;
global.window = global;
global.navigator = sandbox.navigator;

/* ------------------------------------------------------------- load files */

ok(fs.existsSync(ENGINE), "web-live/veil-engine.js exists");
ok(fs.existsSync(DATA), "web-live/demo-data.js exists");

const engineSource = fs.readFileSync(ENGINE, "utf8");
const dataSource = fs.readFileSync(DATA, "utf8");

// The engine must not contain a single network call. This is a source-level
// guard: even if the stubs above were bypassed, the code would still be wrong.
["fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource"].forEach(function (needle) {
  ok(engineSource.indexOf(needle) === -1,
     "engine references no " + needle + " (found in source)");
});

// Load the data the way a <script> tag would: into a global.
// eslint-disable-next-line no-eval
const loadData = new Function("window", dataSource + "\nreturn window.VEIL_DATA;");
const data = loadData(global);

ok(data && typeof data === "object", "demo-data.js assigns window.VEIL_DATA");
ok(Array.isArray(data.documents) && data.documents.length === 3,
   "demo data carries 3 documents");
ok(data.policies && Object.keys(data.policies).length === 3,
   "demo data carries 3 policies");
ok(Array.isArray(data.entities) && data.entities.length === 13,
   "demo data carries 13 entities");
ok(Array.isArray(data.actions) && data.actions.length === 6,
   "demo data carries 6 actions");

// The engine declares itself on the global, exactly as the browser expects.
require(ENGINE);
const VEIL = global.VEIL;
ok(!!VEIL, "engine registers window.VEIL");
ok(VEIL.version === "1.0.0", "engine reports a version");

/* ------------------------------------------------------------- behaviour */

const log = data.documents.filter(function (d) { return d.name === "app.log"; })[0];
ok(!!log, "app.log is present in the embedded data");

const config = data.documents.filter(function (d) { return d.name === "deployment.env"; })[0];
const email = data.documents.filter(function (d) { return d.name === "vendor_email.txt"; })[0];

// 1. Default scan finds the expected entity types.
const scanned = VEIL.scan(log.text, VEIL.defaultPolicy());
const foundEntities = scanned.findings.map(function (f) { return f.entity; });
["EMAIL", "CREDIT_CARD", "IPV4", "SECRET"].forEach(function (entity) {
  ok(foundEntities.indexOf(entity) !== -1, "default scan of app.log finds " + entity);
});
ok(scanned.risk.level !== "NONE", "app.log is not rated NONE");
ok(scanned.risk.score > 0, "app.log has a positive risk score");
ok(scanned.isRedacted === false, "scan() does not claim to have redacted");

// 2. Default redaction removes the real secrets and leaves no undefined/NaN.
const redacted = VEIL.redact(log.text, VEIL.defaultPolicy());
ok(redacted.isRedacted === true, "redact() marks the result as redacted");
ok(redacted.text.indexOf("testkey-ZephyrCove-7193") === -1, "password removed");
ok(redacted.text.indexOf("AKIASYNTHETICKEY0000") === -1, "AWS key removed");
ok(redacted.text.indexOf("alice.chen@brightpath-consulting.com") === -1, "email removed");
ok(redacted.verification && redacted.verification.clean === true,
   "redaction verifies CLEAN");
eq(redacted.text.split("\n").length, log.text.split("\n").length,
   "line count preserved");
ok(redacted.text.indexOf("undefined") === -1, "no 'undefined' in redacted output");
ok(redacted.text.indexOf("NaN") === -1, "no 'NaN' in redacted output");

// 3. Every rendered field is a usable string or number.
redacted.findings.forEach(function (f, index) {
  ok(typeof f.entity === "string" && f.entity.length > 0,
     "finding #" + index + " has an entity");
  ok(typeof f.value === "string", "finding #" + index + " has a string value");
  ok(typeof f.action === "string", "finding #" + index + " has an action");
  ok(typeof f.count === "number" && f.count > 0,
     "finding #" + index + " has a positive count");
  ok(typeof f.replacement === "string",
     "finding #" + index + " has a string replacement");
  ok(["mask", "redact", "hash", "tokenize", "remove", "keep"].indexOf(f.action) !== -1,
     "finding #" + index + " names a known action");
});

// 4. The shareable policy keeps the published support address.
const shareable = data.policies["shareable.yaml"];
const shareablePolicy = VEIL.policyFromSpec(shareable.spec);
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

// 6. Pseudonymize round-trips exactly.
const pseudoPolicy = VEIL.policyFromSpec(data.policies["pseudonymize.yaml"].spec);
const pseudoOut = VEIL.redact(email.text, pseudoPolicy);
ok(Object.keys(pseudoOut.tokenMap).length > 0, "pseudonymize produces tokens");
eq(VEIL.detokenize(pseudoOut.text, pseudoOut.tokenMap), email.text,
   "pseudonymize round-trips to the exact original");

// 6b. A token is never a raw value in disguise, and its entity segment is a
// real catalogue name. The entity part may contain DIGITS (IPV4, IPV6), so a
// `[A-Z_]+` pattern would wrongly reject those tokens — the same trap the
// engine's own verification had.
const tokenEntities = data.entities.map(function (e) { return e.entity; })
  .concat(["EXACT_MATCH"]).join("|");
const tokenShape = new RegExp("^VEIL_(" + tokenEntities + ")_\\d{3}$");
Object.keys(pseudoOut.tokenMap).forEach(function (token) {
  ok(tokenShape.test(token), "token " + token + " is well formed");
});

// 7. False-positive guards, the checks most likely to rot.
eq(VEIL.detectAll("released 2026-09-18 today", VEIL.ALL_ENTITIES).length, 0,
   "bare date is not a phone");
eq(VEIL.detectAll("order 1234567890123456 shipped", VEIL.ALL_ENTITIES).length, 0,
   "invalid card number is not reported");
eq(VEIL.detectAll("user@localhost", VEIL.ALL_ENTITIES).length, 0,
   "address without a TLD is not an email");
eq(VEIL.detectAll("api_key=changeme", VEIL.ALL_ENTITIES).length, 0,
   "placeholder secret is suppressed");
eq(VEIL.detectAll("nothing sensitive here at all", VEIL.ALL_ENTITIES).length, 0,
   "clean prose finds nothing");

// 8. Validators agree with the documented rules.
eq(VEIL.luhnValid("4111111111111111"), true, "luhn accepts a valid card");
eq(VEIL.luhnValid("4111111111111112"), false, "luhn rejects a bad checksum");
eq(VEIL.ibanValid("GB82 WEST 1234 5698 7654 32"), true, "mod-97 accepts a valid IBAN");
eq(VEIL.ibanValid("GB82 WEST 1234 5698 7654 33"), false, "mod-97 rejects a bad IBAN");
eq(VEIL.ssnValid("000123456"), false, "SSA rules reject area 000");
eq(VEIL.ipv4Valid(["256", "1", "1", "1"]), false, "octet range rejects 256");

// 9. Hashing is stable, which is the only reason the action exists.
eq(VEIL.hashValue("a@b.co", "salt"), VEIL.hashValue("a@b.co", "salt"),
   "hash is stable for the same input");
ok(VEIL.hashValue("a@b.co", "salt") !== VEIL.hashValue("a@b.co", "other"),
   "hash changes with the salt");
ok(VEIL.hashValue("a@b.co", "salt") !== VEIL.hashValue("c@d.co", "salt"),
   "hash differs for different values");

// 10. Everything the UI shows is present in the embedded catalogue.
const entityNames = data.entities.map(function (e) { return e.entity; });
VEIL.ALL_ENTITIES.forEach(function (entity) {
  ok(entityNames.indexOf(entity) !== -1,
     "catalogue includes " + entity + " so the UI can render it");
});
data.entities.forEach(function (row) {
  ok(typeof row.label === "string" && row.label.length > 0,
     "entity " + row.entity + " has a label");
  ok(typeof row.validator === "string" && row.validator.length > 0,
     "entity " + row.entity + " has a validator description");
});

/* ------------------------------------------------------------------ report */

if (failures.length) {
  console.error("WEB SMOKE FAILED \u2014 " + failures.length + " of " + checks +
                " checks failed:");
  failures.forEach(function (f) { console.error("  - " + f); });
  process.exit(1);
}

console.log("WEB SMOKE OK \u2014 " + checks + " checks passed, fully offline");
console.log("  network stubbed to throw: XMLHttpRequest, fetch, WebSocket, EventSource");
console.log("  documents      " + data.documents.length);
console.log("  policies       " + Object.keys(data.policies).length);
console.log("  entities       " + data.entities.length);
console.log("  actions        " + data.actions.length);

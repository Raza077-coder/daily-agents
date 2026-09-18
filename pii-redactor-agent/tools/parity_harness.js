#!/usr/bin/env node
/*
 * Node side of the parity harness.
 *
 * Reads the payload Python produced on stdin, drives the browser engine over
 * exactly the same inputs, and writes its results as JSON on stdout for
 * tools/parity_check.py to diff. Keeping the two sides symmetric is the point:
 * if this file quietly computed something in Python's favour, the check would
 * prove nothing.
 *
 * The engine is loaded with `require`, which works because veil-engine.js ends
 * with a CommonJS export alongside the browser global.
 */

"use strict";

const path = require("path");

const enginePath = path.join(__dirname, "..", "web-live", "veil-engine.js");
const VEIL = require(enginePath);

function readStdin() {
  return new Promise(function (resolve, reject) {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", function (chunk) { data += chunk; });
    process.stdin.on("end", function () { resolve(data); });
    process.stdin.on("error", reject);
  });
}

function detectionFor(text) {
  return VEIL.detectAll(text, ["ALL"]).map(function (s) {
    return {
      start: s.start,
      end: s.end,
      entity: s.entity,
      value: s.value,
      detector: s.detector,
      confidence: Math.round(s.confidence * 1e6) / 1e6,
      note: s.note
    };
  });
}

function redactionsFor(payload) {
  const out = {};

  const policies = { default: null };
  Object.keys(payload.policies).forEach(function (name) {
    policies[name] = payload.policies[name];
  });

  Object.keys(policies).forEach(function (policyName) {
    const spec = policies[policyName];
    const policy = spec ? VEIL.policyFromSpec(spec) : VEIL.defaultPolicy();

    Object.keys(payload.documents).forEach(function (docName) {
      const text = payload.documents[docName];
      const result = VEIL.redact(text, policy);
      out[policyName + "|" + docName] = {
        redacted: result.text,
        score: result.risk.score,
        level: result.risk.level,
        findings: result.findings.map(function (f) {
          return {
            entity: f.entity,
            value: f.value,
            action: f.action,
            replacement: f.replacement,
            count: f.count,
            preserved: f.preserved
          };
        }),
        verification: result.verification ? result.verification.status : null
      };
    });
  });

  return out;
}

function masksFor(cases) {
  const out = {};
  cases.forEach(function (row) {
    const value = row[0], keepFirst = row[1], keepLast = row[2];
    out[value + "|" + keepFirst + "|" + keepLast] =
      VEIL.maskValue(value, keepFirst, keepLast);
  });
  return out;
}

function hashesFor(cases, salt) {
  const out = {};
  cases.forEach(function (value) { out[value] = VEIL.hashValue(value, salt); });
  return out;
}

function tokensFor(prefix) {
  const tokenizer = new VEIL.Tokenizer(prefix);
  const out = {};
  ["a@b.co", "c@d.co", "+92 300 1234567", "a@b.co"].forEach(function (value) {
    out[value] = tokenizer.tokenFor("EMAIL", value);
  });
  return out;
}

function validatorsFor() {
  return {
    luhn: {
      "4111111111111111": VEIL.luhnValid("4111111111111111"),
      "4111111111111112": VEIL.luhnValid("4111111111111112"),
      "5500000000000004": VEIL.luhnValid("5500000000000004")
    },
    iban: {
      "GB82 WEST 1234 5698 7654 32": VEIL.ibanValid("GB82 WEST 1234 5698 7654 32"),
      "GB82 WEST 1234 5698 7654 33": VEIL.ibanValid("GB82 WEST 1234 5698 7654 33")
    },
    ssn: {
      "214559876": VEIL.ssnValid("214559876"),
      "000123456": VEIL.ssnValid("000123456"),
      "666123456": VEIL.ssnValid("666123456")
    },
    ipv4: {
      "0.0.0.0": VEIL.ipv4Valid(["0", "0", "0", "0"]),
      "255.255.255.255": VEIL.ipv4Valid(["255", "255", "255", "255"]),
      "256.1.1.1": VEIL.ipv4Valid(["256", "1", "1", "1"]),
      "01.2.3.4": VEIL.ipv4Valid(["01", "2", "3", "4"])
    },
    email: {
      "a@b.co": VEIL.emailValid("a@b.co"),
      "a@b": VEIL.emailValid("a@b"),
      ".lead@example.com": VEIL.emailValid(".lead@example.com")
    }
  };
}

async function main() {
  const raw = await readStdin();
  const payload = JSON.parse(raw);

  const detection = {};
  Object.keys(payload.documents).forEach(function (name) {
    detection[name] = detectionFor(payload.documents[name]);
  });
  payload.adversarial.forEach(function (text, index) {
    detection["adversarial[" + index + "]"] = detectionFor(text);
  });

  const result = {
    detection: detection,
    redactions: redactionsFor(payload),
    masks: masksFor(payload.maskCases),
    hashes: hashesFor(payload.hashCases, payload.salt),
    tokens: tokensFor("VEIL"),
    validators: validatorsFor()
  };

  process.stdout.write(JSON.stringify(result));
}

main().catch(function (error) {
  process.stderr.write("parity harness error: " + error.stack + "\n");
  process.exit(1);
});

#!/usr/bin/env python3
"""Python <-> JavaScript parity harness.

The browser demo re-implements the whole engine in `web-live/veil-engine.js`. That
duplication is deliberate — the page has to work with no server — but duplicated
logic drifts, and a demo that quietly disagrees with the CLI is worse than no
demo at all.

So this harness runs both engines over identical inputs and compares the results
field by field. It is what turns "the demo probably matches" into a fact:

    python3 tools/parity_check.py

Exit 0 means every checked field agrees. Exit 1 names the first disagreement with
both sides shown, so a drift is diagnosable rather than mysterious.

What is compared
----------------
1. Detection — every span both engines propose for each sample document, plus a
   set of adversarial strings aimed at the false-positive guards.
2. Redaction — the full output text, risk score and level, and every finding
   field, for each document under each shipped policy.
3. Tokenizer numbering — the exact token assigned to each value.
4. Masking — the masked output for a range of lengths.
5. Hashing — the HMAC-SHA256 digest itself, so the JS SHA-256 is held to the same
   standard as Python's hashlib.
6. Validators — Luhn, IBAN mod-97, SSN and IPv4 accept/reject on a fixed corpus.

Deliberately not compared: character offsets in the browser's *findings* list,
because the demo groups findings the same way but reports them in its own order.
The redacted text is compared, and that is what carries the offsets' effect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from veil import Scanner, demo  # noqa: E402
from veil.actions import Tokenizer, hash_value, mask  # noqa: E402
from veil.detectors import (  # noqa: E402
    detect_all, email_valid, iban_valid, ipv4_valid, luhn_valid, ssn_valid,
)
from veil.policy import from_yaml  # noqa: E402

HARNESS = os.path.join(PROJECT_ROOT, "tools", "parity_harness.js")

#: Strings aimed squarely at the false-positive guards. If the JS port is a
#: sloppier copy of the Python, these are where the difference shows up first.
ADVERSARIAL = [
    "order 1234567890123456 shipped",           # 16 digits, fails Luhn
    "released 2026-09-18 today",                # bare date, not a phone
    "host 192.168.14.22 up",                    # IP, not a phone
    "upgraded to 1.2.3",                        # version, not a phone
    "at 2026-09-18T04:12:07Z",                  # ISO stamp, not a phone
    "api_key=changeme",                         # placeholder
    "PASSWORD=password",                        # placeholder
    "token=abc",                                # too short
    "api_key=aaaaaaaaaaaa",                     # low entropy
    "example 123-45-6789",                      # reserved SSN
    "user@localhost",                           # no TLD
    "nothing sensitive here at all",
    "caf\u00e9 na\u00efve r\u00e9sum\u00e9",
    "",
    "a@b.co,c@d.co",                            # adjacent findings
    "first a@b.co then c@d.co then e@f.co",     # repeated masking
    "dob: 1988-04-12 and released 2026-09-18",  # context-sensitive DOB
    "call +92 300 1234567 or ring (415) 555-0132",  # both phone shapes
    "card 4111 1111 1111 1111 and iban GB82 WEST 1234 5698 7654 32",
    "Authorization: Bearer abcdef1234567890ABCDEF",
    "AKIASYNTHETICKEY0000 and ghp_SYNTHETICPLACEHOLDER000000000000000000",
    "meet Dr. Nadia Rehman tomorrow",           # PERSON, opt-in
    "Alice Johnson signed off.",                # PERSON from dictionary
]

#: Lengths that cross the masking thresholds, so the short/long branches of
#: both implementations are exercised.
MASK_CASES = [
    ("a@b.co", 0, 4),
    ("alice@example.com", 0, 4),
    ("alice@example.com", 2, 4),
    ("abcdefgh", 3, 3),
    ("abcdefghij", 0, 4),
    ("1234567890", 5, 5),
    ("short", 0, 4),
    ("+92 300 1234567", 3, 4),
]

HASH_CASES = ["a@b.co", "4111 1111 1111 1111", "+92 300 1234567", "value with spaces"]


def build_python_side() -> dict:
    """Everything Python computes, in the shape the harness compares against."""
    scanners = {"default": Scanner()}
    for name, body in demo.POLICIES.items():
        scanners[name] = Scanner(from_yaml(body))

    detection = {}
    for label, text in list(demo.DOCUMENTS.items()) + [
        (f"adversarial[{i}]", t) for i, t in enumerate(ADVERSARIAL)
    ]:
        detection[label] = [
            {
                "start": s.start, "end": s.end, "entity": s.entity,
                "value": s.value, "detector": s.detector,
                "confidence": round(s.confidence, 6), "note": s.note,
            }
            for s in detect_all(text, enabled=["ALL"])
        ]

    redactions = {}
    for policy_name, scanner in scanners.items():
        for doc_name, text in demo.DOCUMENTS.items():
            result = scanner.redact(text)
            redactions[f"{policy_name}|{doc_name}"] = {
                "redacted": result.text,
                "score": result.risk.score,
                "level": result.risk.level,
                "findings": [
                    {
                        "entity": f.entity, "value": f.value, "action": f.action,
                        "replacement": f.replacement, "count": f.count,
                        "preserved": f.preserved,
                    }
                    for f in result.findings
                ],
                "verification": result.verification.status,
            }

    tokenizer = Tokenizer("VEIL")
    token_cases = {}
    for value in ["a@b.co", "c@d.co", "+92 300 1234567", "a@b.co"]:
        token_cases[value] = tokenizer.token_for("EMAIL", value)

    return {
        "detection": detection,
        "redactions": redactions,
        "masks": {f"{v}|{kf}|{kl}": mask(v, "X", kf, kl) for v, kf, kl in MASK_CASES},
        "hashes": {v: hash_value(v, "parity-salt") for v in HASH_CASES},
        "tokens": token_cases,
        "tokenPrefix": "VEIL",
        "validators": {
            "luhn": ["4111111111111111", "4111111111111112", "5500000000000004"],
            "iban": ["GB82 WEST 1234 5698 7654 32", "GB82 WEST 1234 5698 7654 33"],
            "ssn": ["214559876", "000123456", "666123456"],
            "ipv4": ["0.0.0.0", "255.255.255.255", "256.1.1.1", "01.2.3.4"],
            "email": ["a@b.co", "a@b", ".lead@example.com"],
        },
        "payload": {
            "adversarial": ADVERSARIAL,
            "maskCases": MASK_CASES,
            "hashCases": HASH_CASES,
            "salt": "parity-salt",
            "documents": demo.DOCUMENTS,
            "policies": {
                name: from_yaml(body).to_dict() for name, body in demo.POLICIES.items()
            },
            "defaultEntities": list(Scanner().policy.entities),
        },
    }


def run_harness(python_side: dict) -> dict:
    if not os.path.exists(HARNESS):
        raise SystemExit(f"harness not found: {HARNESS}")
    if not os.path.exists(os.path.join(PROJECT_ROOT, "web-live", "veil-engine.js")):
        raise SystemExit("web-live/veil-engine.js not found")

    completed = subprocess.run(
        ["node", HARNESS],
        cwd=PROJECT_ROOT,
        input=json.dumps(python_side["payload"]),
        capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"harness failed with exit {completed.returncode}")
    return json.loads(completed.stdout)


def compare(python_side: dict, js_side: dict) -> list:
    """Return a list of human-readable differences; empty means full agreement."""
    problems = []

    # 1. Detection
    for label, py_spans in python_side["detection"].items():
        js_spans = js_side["detection"].get(label)
        if js_spans is None:
            problems.append(f"detection missing for {label!r} on the JS side")
            continue
        if len(py_spans) != len(js_spans):
            problems.append(
                f"detection count differs for {label!r}: "
                f"python={len(py_spans)} js={len(js_spans)}\n"
                f"  python={[s['entity'] + ':' + s['value'] for s in py_spans]}\n"
                f"  js    ={[s['entity'] + ':' + s['value'] for s in js_spans]}"
            )
            continue
        for index, (py_span, js_span) in enumerate(zip(py_spans, js_spans)):
            for field in ("start", "end", "entity", "value", "detector", "note"):
                if py_span[field] != js_span[field]:
                    problems.append(
                        f"detection {label!r} span #{index} field {field!r}: "
                        f"python={py_span[field]!r} js={js_span[field]!r}"
                    )
            if abs(py_span["confidence"] - js_span["confidence"]) > 1e-6:
                problems.append(
                    f"detection {label!r} span #{index} confidence: "
                    f"python={py_span['confidence']} js={js_span['confidence']}"
                )

    # 2. Redactions
    for key, py_result in python_side["redactions"].items():
        js_result = js_side["redactions"].get(key)
        if js_result is None:
            problems.append(f"redaction missing for {key!r} on the JS side")
            continue
        if py_result["redacted"] != js_result["redacted"]:
            problems.append(
                f"redacted text differs for {key}:\n"
                f"  python={py_result['redacted'][:200]!r}\n"
                f"  js    ={js_result['redacted'][:200]!r}"
            )
        if py_result["score"] != js_result["score"]:
            problems.append(
                f"risk score differs for {key}: "
                f"python={py_result['score']} js={js_result['score']}"
            )
        if py_result["level"] != js_result["level"]:
            problems.append(
                f"risk level differs for {key}: "
                f"python={py_result['level']} js={js_result['level']}"
            )
        if py_result["verification"] != js_result["verification"]:
            problems.append(
                f"verification differs for {key}: "
                f"python={py_result['verification']} js={js_result['verification']}"
            )
        if len(py_result["findings"]) != len(js_result["findings"]):
            problems.append(
                f"finding count differs for {key}: "
                f"python={len(py_result['findings'])} js={len(js_result['findings'])}"
            )
            continue
        for index, (py_f, js_f) in enumerate(zip(py_result["findings"],
                                                js_result["findings"])):
            for field in ("entity", "value", "action", "replacement", "count",
                          "preserved"):
                if py_f[field] != js_f[field]:
                    problems.append(
                        f"finding {key} #{index} field {field!r}: "
                        f"python={py_f[field]!r} js={js_f[field]!r}"
                    )

    # 3. Masks
    for key, py_mask in python_side["masks"].items():
        js_mask = js_side["masks"].get(key)
        if py_mask != js_mask:
            problems.append(f"mask differs for {key}: python={py_mask!r} js={js_mask!r}")

    # 4. Hashes
    for value, py_hash in python_side["hashes"].items():
        js_hash = js_side["hashes"].get(value)
        if py_hash != js_hash:
            problems.append(
                f"hash differs for {value!r}: python={py_hash!r} js={js_hash!r}"
            )

    # 5. Tokens
    for value, py_token in python_side["tokens"].items():
        js_token = js_side["tokens"].get(value)
        if py_token != js_token:
            problems.append(
                f"token differs for {value!r}: python={py_token!r} js={js_token!r}"
            )

    # 6. Validators
    checks = {
        "luhn": luhn_valid, "iban": iban_valid, "ssn": ssn_valid,
        "ipv4": ipv4_valid, "email": email_valid,
    }
    for name, values in python_side["validators"].items():
        for value in values:
            if name == "ipv4":
                py_ok = ipv4_valid(tuple(value.split(".")))
            else:
                py_ok = checks[name](value)
            js_ok = js_side["validators"].get(name, {}).get(value)
            if js_ok is None:
                problems.append(f"validator {name} did not report on {value!r} in JS")
            elif bool(py_ok) != bool(js_ok):
                problems.append(
                    f"validator {name} disagrees on {value!r}: "
                    f"python={bool(py_ok)} js={bool(js_ok)}"
                )

    return problems


def main() -> int:
    python_side = build_python_side()
    js_side = run_harness(python_side)

    total_checks = (
        sum(len(v) for v in python_side["detection"].values())
        + len(python_side["redactions"])
        + len(python_side["masks"])
        + len(python_side["hashes"])
        + len(python_side["tokens"])
        + sum(len(v) for v in python_side["validators"].values())
    )

    problems = compare(python_side, js_side)

    if problems:
        print(f"PARITY FAILED — {len(problems)} difference(s) across "
              f"{total_checks} checks\n")
        for problem in problems[:25]:
            print(f"  - {problem}")
        if len(problems) > 25:
            print(f"  ... and {len(problems) - 25} more")
        return 1

    print(f"PARITY OK — {total_checks} checks agree between Python and JavaScript")
    print(f"  documents compared      {len(python_side['detection'])}")
    print(f"  redactions compared     {len(python_side['redactions'])}")
    print(f"  mask cases              {len(python_side['masks'])}")
    print(f"  hash digests            {len(python_side['hashes'])}")
    print(f"  validator assertions    "
          f"{sum(len(v) for v in python_side['validators'].values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

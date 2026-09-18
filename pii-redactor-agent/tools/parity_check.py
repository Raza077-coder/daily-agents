#!/usr/bin/env python3
"""Cross-engine parity harness: run the same corpus through Python and Node.

`veil-engine.js` is a hand-port of the Python engine so the browser demo can run
with no server. Two implementations of one specification drift silently unless
something compares them, so this script feeds an identical corpus through both
and diffs the output byte for byte.

    python3 tools/parity_check.py            # compare, print a summary
    python3 tools/parity_check.py --show     # also print both outputs
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from veil import Scanner  # noqa: E402
from veil.policy import Policy, from_yaml  # noqa: E402
from veil import demo  # noqa: E402

HARNESS = os.path.join(PROJECT_ROOT, "tools", "parity_harness.js")

#: Texts that between them touch every detector and the overlap resolver.
CORPUS = [
    "plain prose with nothing to find in it at all",
    "write to alice.chen@brightpath-consulting.com today",
    "two addresses: a@b.co and c@d.co",
    "call +92 300 1234567 or ring (415) 555-0132",  # both phone shapes
    "card 4111 1111 1111 1111 and iban GB82 WEST 1234 5698 7654 32",
    "Authorization: Bearer abcdef1234567890ABCDEF",
    "AKIASYNTHETICKEY0000 and ghp_SYNTHETICPLACEHOLDER000000000000000000",
    "meet Dr. Nadia Rehman tomorrow",           # PERSON, opt-in
    "Alice Johnson signed off.",                # PERSON from dictionary
    "dob 1988-04-12 and release 2026-09-18",    # only the first is a DOB
    "hosts 192.168.14.22 and 2001:0db8:85a3::8a2e:0370:7334 and 00:1B:44:11:3A:B7",
    "see https://internal.example.com/admin?token=abc123 now",
    "ssn 214-55-9876 and cnic 35202-1234567-1",
    "db_password=testkey-ZephyrCove-7193 end of line",
    "placeholders: API_KEY=changeme TOKEN=REPLACE_ME OTHER=your_api_key",
    "mixed: alice@example.com paid 4111 1111 1111 1111 on 2026-01-02",
]


def run_node(texts, spec, opt_in_person: bool) -> dict:
    payload = json.dumps({"texts": texts, "policy": spec,
                          "optInPerson": opt_in_person})
    proc = subprocess.run(
        ["node", HARNESS], input=payload, capture_output=True, text=True,
        cwd=PROJECT_ROOT, timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"node harness failed ({proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="print both outputs")
    args = parser.parse_args()

    shareable = from_yaml(demo.POLICIES["shareable.yaml"])
    strict = from_yaml(demo.POLICIES["strict.yaml"])
    pseudonymize = from_yaml(demo.POLICIES["pseudonymize.yaml"])

    cases = [
        ("shareable", shareable, False),
        ("strict", strict, False),
        ("pseudonymize", pseudonymize, False),
    ]

    mismatches = 0
    for label, policy, opt_in in cases:
        python_out = []
        for text in CORPUS:
            result = Scanner(policy).redact(text)
            python_out.append({
                "text": result.text,
                "status": result.verification.status if result.verification else None,
                "findings": [
                    {"entity": f.entity, "action": f.action, "value": f.value,
                     "replacement": f.replacement, "count": f.count}
                    for f in result.findings
                ],
                "score": result.risk.score if result.risk else 0,
            })

        node_result = run_node(CORPUS, policy.to_dict(), opt_in)
        node_out = node_result["results"]

        if args.show:
            print(f"=== {label} (python) ===")
            print(json.dumps(python_out, indent=2, ensure_ascii=False))
            print(f"=== {label} (node) ===")
            print(json.dumps(node_out, indent=2, ensure_ascii=False))

        if len(node_out) != len(python_out):
            print(f"FAIL {label}: node returned {len(node_out)} results, "
                  f"python {len(python_out)}")
            mismatches += 1
            continue

        policy_mismatches = 0
        for index, (py, js) in enumerate(zip(python_out, node_out)):
            if py["text"] != js["text"]:
                policy_mismatches += 1
                print(f"  mismatch on case {index} ({label}):")
                print(f"    python: {py['text']!r}")
                print(f"    node:   {js['text']!r}")
            elif py["status"] != js["status"]:
                policy_mismatches += 1
                print(f"  status mismatch on case {index} ({label}): "
                      f"{py['status']} vs {js['status']}")
            elif py["score"] != js["score"]:
                policy_mismatches += 1
                print(f"  score mismatch on case {index} ({label}): "
                      f"{py['score']} vs {js['score']}")

        if policy_mismatches:
            print(f"FAIL {label}: {policy_mismatches}/{len(CORPUS)} case(s) differ")
            mismatches += policy_mismatches
        else:
            print(f"ok   {label:<14} {len(CORPUS)}/{len(CORPUS)} cases identical")

    if mismatches:
        print(f"\nPARITY CHECK FAILED \u2014 {mismatches} mismatch(es)")
        return 1
    print("\nPARITY CHECK OK \u2014 the Python and JS engines agree on every case.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

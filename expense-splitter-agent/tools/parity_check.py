#!/usr/bin/env python3
"""Python <-> JavaScript parity harness.

The browser demo re-implements SplitKit's money rules in JavaScript. Duplicated
logic drifts, and a demo that quietly disagrees with the CLI is worse than no
demo at all — so this harness runs both engines over the *same* inputs and fails
loudly on any difference.

    python3 tools/parity_check.py

Two details matter for this to be a real check rather than a passing one:

1. Currency scaling is done by the Python fixture (``build_demo_group``), and
   the resulting group JSON is handed to JavaScript. Rescaling inside the
   harness would test a different dataset than the page actually ships.
2. Formatting is compared with identical options on both sides — comparing
   JavaScript's default (which includes a currency symbol) against Python with
   ``symbol=False`` would report a failure that is not a disagreement.

Exits 0 when the two agree on every case, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from splitkit import money as M  # noqa: E402
from splitkit.demo import build_demo_group  # noqa: E402
from splitkit.engine import SplitKit  # noqa: E402

#: Currencies covering all three exponent classes (2, 3 and 0 decimals).
CURRENCIES = ["USD", "KWD", "JPY"]

#: Amount/weight pairs that stress the allocator: exact thirds, sub-unit money,
#: prime-count groups, and a prime total that cannot divide at all.
ALLOCATIONS = [
    (10000, [1, 1, 1]),
    (7, [1, 1, 1]),
    (100, [1, 1, 1]),
    (9999, [2, 1, 1]),
    (1, [1, 1, 1, 1, 1, 1, 1]),
    (0, [1, 1]),
    (-10000, [1, 1, 1]),
    (9240, [2, 1, 1]),
    (5830, [1400, 1830, 1200]),
    (100000, [1, 1, 1, 1, 1, 1, 1]),
]

#: Formatting cases, compared with the same options on both sides.
FORMAT_CASES = [(code, minor) for code in CURRENCIES for minor in (0, 1, 7, 100, 12345, -4200)]
FORMAT_OPTS = {"symbol": False, "grouping": True, "plus": True}

JS_DRIVER = r"""
global.window = global;
require(process.argv[2] + "/web-live/splitkit-engine.js");
require(process.argv[2] + "/web-live/demo-data.js");
var E = global.SplitKitEngine;
var out = { allocations: [], balances: {}, plans: {}, formats: [] };

var allocations = JSON.parse(process.argv[3]);
allocations.forEach(function (pair) { out.allocations.push(E.allocate(pair[0], pair[1])); });

// Receive each group already scaled by the Python fixture, so both engines run
// on byte-identical input rather than on two independently-derived datasets.
var groups = JSON.parse(process.argv[4]);
Object.keys(groups).forEach(function (code) {
  var g = groups[code];
  out.balances[code] = E.balanceTable(g).map(function (r) {
    return [r.id, r.paid_minor, r.share_minor, r.balance_minor];
  });
  var p = E.settle(g, "optimal");
  out.plans[code] = p.transfers.map(function (t) { return [t.from, t.to, t.amount]; });
});

var cases = JSON.parse(process.argv[5]);
cases.forEach(function (pair) {
  out.formats.push([pair[0], pair[1], E.formatFor(pair[0], pair[1],
    { symbol: false, code: true, grouping: true, plus: true })]);
});

console.log(JSON.stringify(out));
"""


def run_js(groups: dict, cases: list) -> dict:
    """Run the JS engine once and return its results."""
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node is not installed - the parity check needs it")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(JS_DRIVER)
        driver = fh.name
    try:
        proc = subprocess.run(
            [node, driver, str(ROOT), json.dumps(ALLOCATIONS), json.dumps(groups), json.dumps(cases)],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        os.unlink(driver)
    if proc.returncode != 0:
        raise SystemExit(f"JS engine failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def fixtures() -> dict:
    """Scaled group JSON per currency, produced by the Python fixture."""
    return {code: build_demo_group(code).to_dict() for code in CURRENCIES}


def py_allocations() -> list:
    return [M.allocate(total, weights) for total, weights in ALLOCATIONS]


def py_balances(code: str) -> list:
    table = SplitKit(group=build_demo_group(code)).balance_table()
    return [[r["id"], r["paid_minor"], r["share_minor"], r["balance_minor"]] for r in table]


def py_plan(code: str) -> list:
    plan = SplitKit(group=build_demo_group(code)).settle(strategy="optimal")
    return [[t["from"], t["to"], t["amount"]] for t in plan["transfers"]]


def main() -> int:
    groups = fixtures()
    js = run_js(groups, FORMAT_CASES)
    failures: list[str] = []

    # 1. allocation is exact and identical
    for i, (expected, got) in enumerate(zip(py_allocations(), js["allocations"])):
        if expected != got:
            failures.append(f"allocate{ALLOCATIONS[i]} python={expected} js={got}")

    # 2. balances and settle plans per currency exponent
    for code in CURRENCIES:
        want, have = py_balances(code), js["balances"].get(code)
        if want != have:
            failures.append(f"balances[{code}] python={want} js={have}")
        want_plan, have_plan = py_plan(code), js["plans"].get(code)
        if want_plan != have_plan:
            failures.append(f"plan[{code}] python={want_plan} js={have_plan}")

    # 3. formatting, same options both sides
    for code, minor, got in js["formats"]:
        want = M.format_for(code, minor, **FORMAT_OPTS)
        if want != got:
            failures.append(f"format[{code},{minor}] python={want!r} js={got!r}")

    total = len(ALLOCATIONS) + 2 * len(CURRENCIES) + len(js["formats"])
    if failures:
        print(f"PARITY FAILED ({len(failures)} of {total} checks)\n")
        for f in failures:
            print("  -", f)
        return 1

    print(f"PARITY PASS - {total} checks, Python and JavaScript agree exactly")
    print(f"  allocations : {len(ALLOCATIONS)}")
    print(f"  currencies  : {len(CURRENCIES)} ({', '.join(CURRENCIES)}) - balances + settle plans")
    print(f"  formats     : {len(js['formats'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

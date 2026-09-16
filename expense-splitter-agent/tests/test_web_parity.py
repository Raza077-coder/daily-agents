"""Parity between the Python engine and the browser demo.

The demo page re-implements SplitKit's money rules in JavaScript. If the two
never disagree, the page is lying — which is worse than having no page. These
tests pin the contract from the Python side.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from splitkit import money as M
from splitkit.demo import build_demo_group
from splitkit.engine import SplitKit

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web-live"
DATA_JS = WEB / "demo-data.js"
ENGINE_JS = WEB / "splitkit-engine.js"

EXPECTED_KEYS = {
    "total_minor", "expense_count", "member_count",
    "transfer_count", "ali_net_minor", "sara_net_minor", "bilal_net_minor",
}


def embedded_expectations() -> dict:
    """Pull the expectation block out of the generated data file."""
    text = DATA_JS.read_text(encoding="utf-8")
    marker = '"expectations"'
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[brace:i + 1])
    raise AssertionError("could not parse expectations block")


def embedded_group() -> dict:
    text = DATA_JS.read_text(encoding="utf-8")
    marker = '"group"'
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[brace:i + 1])
    raise AssertionError("could not parse group block")


# --------------------------------------------------------------------------- #
# generated-data integrity
# --------------------------------------------------------------------------- #


def test_demo_data_file_exists():
    assert DATA_JS.is_file(), "run tools/build_web_data.py"
    assert ENGINE_JS.is_file()


def test_embedded_expectations_have_the_expected_shape():
    assert set(embedded_expectations()) == EXPECTED_KEYS


def test_embedded_group_matches_the_python_fixture_exactly():
    """The committed data file must equal a fresh build from the fixture."""
    assert embedded_group() == build_demo_group("USD").to_dict()


def test_embedded_total_matches_the_engine():
    expected = embedded_expectations()
    assert expected["total_minor"] == sum(e.amount for e in build_demo_group("USD").expenses)


def test_embedded_transfer_count_matches_the_engine():
    expected = embedded_expectations()
    plan = SplitKit(group=build_demo_group("USD")).settle(strategy="optimal")
    assert expected["transfer_count"] == plan["transfer_count"]


def test_embedded_balances_match_the_engine():
    expected = embedded_expectations()
    by_id = {r["id"]: r for r in SplitKit(group=build_demo_group("USD")).balance_table()}
    for member in ("ali", "sara", "bilal"):
        assert expected[f"{member}_net_minor"] == by_id[member]["balance_minor"]


# --------------------------------------------------------------------------- #
# the demo must stay network-free and self-contained
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("asset", ["splitkit-engine.js", "app.js", "demo-data.js"])
def test_web_assets_make_no_network_calls(asset):
    """A page that fetches at runtime is not static and breaks under file://."""
    source = (WEB / asset).read_text(encoding="utf-8")
    # Strip comments and string literals so prose mentioning "fetch" is fine.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    code = re.sub(r"""(['"`])(?:\\.|(?!\1).)*\1""", "''", code, flags=re.S)
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "import("):
        assert banned not in code, f"{asset} uses {banned} - the demo must be offline"


def test_index_references_every_asset_it_needs():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for asset in ("style.css", "splitkit-engine.js", "demo-data.js", "app.js"):
        assert asset in html, f"index.html does not reference {asset}"


def test_engine_js_loads_before_app_js():
    """app.js reads window.SplitKitEngine, so ordering is load-bearing."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index("splitkit-engine.js") < html.index("app.js")
    assert html.index("demo-data.js") < html.index("app.js")


def test_index_has_a_real_title_and_no_placeholder():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "SPLITKIT" in html
    assert "PLACEHOLDER" not in html
    assert "<title>" in html


# --------------------------------------------------------------------------- #
# live engine parity (needs node)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_and_python_agree_on_the_demo_dataset():
    """Run the JS engine over the committed data and compare with Python."""
    driver = """
    global.window = global;
    require(process.argv[2] + "/web-live/splitkit-engine.js");
    require(process.argv[2] + "/web-live/demo-data.js");
    var E = global.SplitKitEngine;
    var g = global.SPLITKIT_DEMO.group;
    console.log(JSON.stringify({
      balances: E.balanceTable(g).map(function (r) { return [r.id, r.balance_minor]; }),
      plan: E.settle(g, "optimal").transfers.map(function (t) { return [t.from, t.to, t.amount]; }),
      allocated: E.splitEvenly(10000, 3),
      tiny: E.splitEvenly(7, 3)
    }));
    """
    import tempfile
    import os

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(driver)
        path = fh.name
    try:
        proc = subprocess.run(
            [shutil.which("node"), path, str(ROOT)],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        os.unlink(path)

    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)

    group = build_demo_group("USD")
    py_balances = [[r["id"], r["balance_minor"]]
                   for r in SplitKit(group=group).balance_table()]
    assert js["balances"] == py_balances

    py_plan = [[t["from"], t["to"], t["amount"]]
               for t in SplitKit(group=group).settle(strategy="optimal")["transfers"]]
    assert js["plan"] == py_plan

    assert js["allocated"] == M.allocate(10000, [1, 1, 1])
    assert js["tiny"] == M.allocate(7, [1, 1, 1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_parity_check_script_passes():
    """The shipped harness must exit 0 — it is part of the deliverable."""
    proc = subprocess.run(
        ["python3", str(ROOT / "tools" / "parity_check.py")],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PARITY PASS" in proc.stdout

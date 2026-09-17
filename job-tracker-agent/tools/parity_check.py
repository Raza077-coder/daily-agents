"""Python <-> JavaScript parity harness for JOBFLOW.

The browser demo re-implements the engine in JavaScript.  Duplicated logic
drifts, and a demo that quietly disagrees with the CLI is worse than no demo —
so this harness runs both engines over identical vaults and compares the
outputs field by field.

Usage::

    python3 tools/parity_check.py            # exit 0 = parity holds
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobtracker import analytics, followups  # noqa: E402
from jobtracker.engine import JobFlowEngine  # noqa: E402
from jobtracker.store import Vault  # noqa: E402

TODAY = "2026-09-17"
ENGINE_JS = ROOT / "web-live" / "track-engine.js"

FAILURES: list = []
CHECKS: list = []


def check(name: str, py_value, js_value) -> None:
    CHECKS.append(name)
    if py_value != js_value:
        FAILURES.append((name, py_value, js_value))


def _norm(value):
    """Normalise for comparison: floats rounded, dicts sorted, tuples->lists."""

    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def run_js(payload: dict) -> dict:
    """Feed a vault to the JS engine and read its computed bundle back."""

    script = f"""
    const fs = require('fs');
    require({json.dumps(str(ENGINE_JS))});
    const E = globalThis.JobFlowEngine;
    const input = JSON.parse(fs.readFileSync(0, 'utf8'));
    const apps = input.applications;
    const today = input.today;
    const out = {{
      funnel: E.funnel(apps, today),
      plan: E.plan(apps, today, 25),
      summary: E.summary(apps, today),
      aging: E.agingRows(apps, today),
      stalled: E.stalled(apps, today),
      time_in_stage: E.timeInStage(apps, today),
      sources: E.sourceBreakdown(apps, today),
      salary: E.salarySummary(apps),
      weekly: E.weeklyActivity(apps, today, 8),
      streak: E.streak(apps, today),
      status_counts: E.statusCounts(apps),
      config: E.config(),
      plan_override: E.plan(apps, today, 25, {{followup_days: 5}}),
      furthest: apps.map(a => [a.id, E.furthestStage(a)]),
      reached: apps.map(a => [a.id, E.reachedIndex(a)]),
      responded: apps.map(a => [a.id, E.hasResponse(a)]),
      ages: apps.map(a => [a.id, E.ageDays(a, today), E.daysInStage(a, today),
                           E.daysSinceActivity(a, today), E.followupCount(a)])
    }};
    process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"node failed:\n{proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout)


def main() -> int:
    from datetime import date

    today = date.fromisoformat(TODAY)

    # Build the canonical demo vault twice: once as Python objects, once as a
    # plain dict for the JS side.
    engine = JobFlowEngine.open(ROOT / "tools" / ".parity-vault.json")
    engine.seed_demo(today=TODAY, force=True)
    vault = engine.vault
    payload = {"today": TODAY, "applications": vault.to_dict()["applications"]}
    js = run_js(payload)

    # ---- funnel ----------------------------------------------------------
    py_funnel = analytics.funnel(vault, today)
    check("funnel.submitted", py_funnel["submitted"], js["funnel"]["submitted"])
    check("funnel.open", py_funnel["open"], js["funnel"]["open"])
    check("funnel.wishlist", py_funnel["wishlist"], js["funnel"]["wishlist"])
    check("funnel.closed", py_funnel["closed"], js["funnel"]["closed"])
    check("funnel.responded", py_funnel["responded"], js["funnel"]["responded"])
    check(
        "funnel.response_denominator",
        py_funnel["response_denominator"],
        js["funnel"]["response_denominator"],
    )
    check("funnel.response_rate", py_funnel["response_rate"], js["funnel"]["response_rate"])
    check("funnel.interviewed", py_funnel["interviewed"], js["funnel"]["interviewed"])
    check("funnel.offered", py_funnel["offered"], js["funnel"]["offered"])
    for index, row in enumerate(py_funnel["rows"]):
        js_row = js["funnel"]["rows"][index]
        check(f"funnel.row[{row['stage']}].reached", row["reached"], js_row["reached"])
        check(f"funnel.row[{row['stage']}].rate", row["rate"], js_row["rate"])
        check(f"funnel.row[{row['stage']}].step_rate", row["step_rate"], js_row["step_rate"])
        check(f"funnel.row[{row['stage']}].denominator", row["denominator"], js_row["denominator"])

    # ---- per-application derived state ----------------------------------
    check(
        "furthest_stage",
        [[a.id, a.furthest_stage()] for a in vault.applications],
        js["furthest"],
    )
    check(
        "reached_index",
        [[a.id, a.reached_index()] for a in vault.applications],
        js["reached"],
    )
    check(
        "has_response",
        [[a.id, a.has_response()] for a in vault.applications],
        js["responded"],
    )
    check(
        "ages",
        [
            [a.id, a.age_days(today), a.days_in_stage(today), a.days_since_activity(today), a.followup_count()]
            for a in vault.applications
        ],
        js["ages"],
    )

    # ---- plan ------------------------------------------------------------
    py_plan = followups.plan(vault, today, limit=25)
    check("plan.total", py_plan["total"], js["plan"]["total"])
    check("plan.by_severity", py_plan["by_severity"], js["plan"]["by_severity"])
    check("plan.headline", py_plan["headline"], js["plan"]["headline"])
    check("plan.action_count", len(py_plan["actions"]), len(js["plan"]["actions"]))
    for index, action in enumerate(py_plan["actions"]):
        js_action = js["plan"]["actions"][index]
        check(f"plan[{index}].rule", action["rule"], js_action["rule"])
        check(f"plan[{index}].severity", action["severity"], js_action["severity"])
        check(f"plan[{index}].score", action["score"], js_action["score"])
        check(f"plan[{index}].app_id", action["app_id"], js_action["app_id"])
        check(f"plan[{index}].explain", action["explain"], js_action["explain"])
        check(f"plan[{index}].terms", action["terms"], js_action["terms"])

    # ---- analytics -------------------------------------------------------
    py_summary = analytics.summary(vault, today)
    check("summary.stalled", py_summary["stalled"], js["stalled"])
    check("summary.aging", py_summary["aging"], js["aging"])
    check("summary.sources", py_summary["sources"], js["sources"])
    check("summary.weekly", py_summary["weekly"], js["weekly"])
    check("summary.streak", py_summary["streak"], js["streak"])
    check("summary.status_counts", py_summary["status_counts"], js["status_counts"])
    check("summary.salary", py_summary["salary"], js["salary"])

    # time_in_stage carries a mean that Python rounds to 1dp and JS the same.
    py_tis = py_summary["time_in_stage"]
    js_tis = js["time_in_stage"]
    for stage in py_tis:
        check(f"time_in_stage[{stage}].median", py_tis[stage]["median_days"],
              js_tis[stage]["median_days"])
        check(f"time_in_stage[{stage}].samples", py_tis[stage]["samples"],
              js_tis[stage]["samples"])
        check(f"time_in_stage[{stage}].min", py_tis[stage]["min_days"],
              js_tis[stage]["min_days"])
        check(f"time_in_stage[{stage}].max", py_tis[stage]["max_days"],
              js_tis[stage]["max_days"])

    # ---- second vault: a stress case with boundaries ---------------------
    stress_data = {
        "schema_version": 1,
        "owner": "parity",
        "applications": [
            {
                "id": "a-offer-old", "company": "Offer Co", "role": "Eng",
                "status": "offer", "created_on": "2026-05-01", "applied_on": "2026-05-01",
                "deadline_on": None, "closed_on": None, "salary_min": 9000000,
                "salary_max": 12000000, "currency": "USD", "priority": 5,
                "work_mode": "remote", "source": "referral", "location": "", "url": "",
                "next_action": "Decide", "next_action_on": "2026-09-01", "contact": "",
                "tags": [],
                "events": [
                    {"on": "2026-05-20", "kind": "moved", "from_status": "applied",
                     "to_status": "screen"},
                    {"on": "2026-07-01", "kind": "moved", "from_status": "screen",
                     "to_status": "offer"}
                ],
                "interviews": [], "notes": []
            },
            {
                "id": "b-deadline-today", "company": "Urgent Co", "role": "Dev",
                "status": "wishlist", "created_on": "2026-09-10", "applied_on": None,
                "deadline_on": "2026-09-17", "closed_on": None, "salary_min": None,
                "salary_max": None, "currency": "EUR", "priority": 4,
                "work_mode": "hybrid", "source": "linkedin", "location": "", "url": "",
                "next_action": "", "next_action_on": None, "contact": "", "tags": [],
                "events": [{"on": "2026-09-10", "kind": "created", "to_status": "wishlist"}],
                "interviews": [], "notes": []
            },
            {
                "id": "c-fresh", "company": "Fresh Co", "role": "SRE",
                "status": "applied", "created_on": "2026-09-15", "applied_on": "2026-09-15",
                "deadline_on": None, "closed_on": None, "salary_min": 5000000,
                "salary_max": None, "currency": "USD", "priority": 3,
                "work_mode": "onsite", "source": "job_board", "location": "", "url": "",
                "next_action": "", "next_action_on": None, "contact": "", "tags": [],
                "events": [{"on": "2026-09-15", "kind": "applied",
                            "from_status": "wishlist", "to_status": "applied"}],
                "interviews": [{"on": "2026-09-19", "kind": "screen", "done": False}],
                "notes": []
            },
            {
                "id": "d-rejected-post-interview", "company": "Late No", "role": "Lead",
                "status": "rejected", "created_on": "2026-07-20", "applied_on": "2026-07-20",
                "deadline_on": None, "closed_on": "2026-09-12", "salary_min": None,
                "salary_max": None, "currency": "USD", "priority": 4,
                "work_mode": "remote", "source": "recruiter", "location": "", "url": "",
                "next_action": "", "next_action_on": None, "contact": "", "tags": [],
                "events": [
                    {"on": "2026-08-05", "kind": "moved", "from_status": "applied",
                     "to_status": "interview"},
                    {"on": "2026-09-12", "kind": "closed", "from_status": "interview",
                     "to_status": "rejected"}
                ],
                "interviews": [{"on": "2026-08-05", "kind": "technical", "done": True}],
                "notes": []
            }
        ],
    }
    stress_vault = Vault.from_dict(stress_data)
    stress_payload = {"today": TODAY, "applications": stress_vault.to_dict()["applications"]}
    js2 = run_js(stress_payload)

    py_f2 = analytics.funnel(stress_vault, today)
    check("stress.funnel.submitted", py_f2["submitted"], js2["funnel"]["submitted"])
    check("stress.funnel.response_rate", py_f2["response_rate"], js2["funnel"]["response_rate"])
    check("stress.funnel.interviewed", py_f2["interviewed"], js2["funnel"]["interviewed"])
    check("stress.rows", [r["reached"] for r in py_f2["rows"]],
          [r["reached"] for r in js2["funnel"]["rows"]])
    check(
        "stress.furthest",
        [[a.id, a.furthest_stage()] for a in stress_vault.applications],
        js2["furthest"],
    )
    py_plan2 = followups.plan(stress_vault, today, limit=25)
    check("stress.plan.total", py_plan2["total"], js2["plan"]["total"])
    check("stress.plan.rules", [a["rule"] for a in py_plan2["actions"]],
          [a["rule"] for a in js2["plan"]["actions"]])
    check("stress.plan.scores", [a["score"] for a in py_plan2["actions"]],
          [a["score"] for a in js2["plan"]["actions"]])
    check("stress.stalled", analytics.summary(stress_vault, today)["stalled"], js2["stalled"])

    # ---- config ----------------------------------------------------------
    # The browser build must resolve the documented knobs exactly like the
    # Python engine.  In particular followup_days defaults to None (meaning
    # "use the per-status STALL_DAYS table") -- a hardcoded number here would
    # silently override the stall curve in the HUD only, so the README would
    # describe behaviour the live demo does not have.
    js_cfg = js["config"]
    py_cfg = followups._config(None)
    for key in ("weekly_target", "followup_days", "second_followup_days",
                "wishlist_idle_days", "max_followups"):
        check(f"config.{key}", py_cfg[key], js_cfg[key])
    check("config.followup_days_is_none", py_cfg["followup_days"], None)

    # An explicit override must move both engines identically.
    py_override = followups.plan(vault, today, limit=25, config={"followup_days": 5})
    check("override.rules", [a["rule"] for a in py_override["actions"]],
          [a["rule"] for a in js["plan_override"]["actions"]])
    check("override.total", py_override["total"], js["plan_override"]["total"])
    check("override.config", py_override["config"]["followup_days"],
          js["plan_override"]["config"]["followup_days"])

    # ---- report ----------------------------------------------------------
    print(f"JOBFLOW parity: {len(CHECKS)} checks on 2 vaults, today={TODAY}")
    if FAILURES:
        print(f"\n{len(FAILURES)} MISMATCH(ES):")
        for name, py_value, js_value in FAILURES[:25]:
            print(f"\n  {name}")
            print(f"    python: {json.dumps(_norm(py_value), ensure_ascii=False)[:400]}")
            print(f"    js    : {json.dumps(_norm(js_value), ensure_ascii=False)[:400]}")
        print(f"\nFAILED ({len(FAILURES)}/{len(CHECKS)})")
        return 1
    print(f"PASS — all {len(CHECKS)} checks agree between Python and JavaScript")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

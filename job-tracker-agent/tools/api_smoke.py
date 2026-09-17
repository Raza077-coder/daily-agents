"""End-to-end smoke test for the JOBFLOW REST layer.

Runs the real FastAPI app in-process (via ``TestClient``) against a throwaway
vault and asserts the status code of every route, plus the derived state on the
application endpoints.

Why this exists: two real bugs were found this way and are now regression
guarded here —

* ``GET /api/summary`` returned **500 on an empty vault**, because
  ``resolve_today(None)`` raised instead of falling back to the system clock.
  Every read endpoint is therefore exercised on a *brand-new* vault first.
* ``GET /api/applications/{id}`` returned the stored record only, while
  ``GET /api/applications`` returned a derived block — so the two endpoints
  disagreed about the same application. The detail response is now asserted to
  carry the same fields.

Usage::

    python3 tools/api_smoke.py       # exit 0 = all good
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="jobflow-api-")
os.environ["JOBFLOW_VAULT"] = os.path.join(TMP, "vault.json")
os.environ["JOBFLOW_TODAY"] = "2026-09-17"

from fastapi.testclient import TestClient  # noqa: E402

import api.app as app_module  # noqa: E402

client = TestClient(app_module.app)

FAILURES: list[str] = []
CHECKS = 0


def expect(method: str, path: str, want: int, **kwargs) -> object:
    global CHECKS
    CHECKS += 1
    response = client.request(method, path, **kwargs)
    if response.status_code != want:
        body = response.text[:160].replace("\n", " ")
        FAILURES.append(f"{method} {path} -> {response.status_code} (want {want}) :: {body}")
    return response


def main() -> int:
    # ---- 1. brand-new, empty vault: every read must work -----------------
    for path in ("/api/health", "/api/summary", "/api/plan", "/api/funnel",
                 "/api/applications", "/api/status", "/api/report",
                 "/api/report.md", "/api/upcoming", "/api/vocabulary",
                 "/api/routes", "/api/export"):
        expect("GET", path, 200)

    empty = expect("GET", "/api/summary", 200).json()
    if empty["totals"]["submitted"] != 0:
        FAILURES.append("empty vault reported submitted != 0")

    # ---- 2. seed ---------------------------------------------------------
    expect("POST", "/api/demo", 200)

    # ---- 3. reads with data ---------------------------------------------
    for path in ("/api/health", "/api/summary", "/api/plan", "/api/funnel",
                 "/api/applications", "/api/status", "/api/report",
                 "/api/report.md", "/api/upcoming", "/api/export"):
        expect("GET", path, 200)

    summary = expect("GET", "/api/summary", 200).json()
    if summary["totals"]["submitted"] < 5:
        FAILURES.append(f"seeded vault looks too small: {summary['totals']}")

    plan = expect("GET", "/api/plan?limit=5", 200).json()
    if plan["total"] < 1 or len(plan["actions"]) > 5:
        FAILURES.append(f"plan total/limit wrong: total={plan['total']} n={len(plan['actions'])}")
    if not plan["headline"]:
        FAILURES.append("plan headline empty")

    # cohort bias: the post-onsite rejection must count at the onsite rung
    funnel = expect("GET", "/api/funnel", 200).json()
    onsite = next(r for r in funnel["rows"] if r["stage"] == "onsite")
    if onsite["reached"] < 2:
        FAILURES.append(
            f"onsite rung = {onsite['reached']}; a rejection after an onsite "
            "was not counted at its furthest rung (cohort bias regression)"
        )

    # ---- 4. list and detail must agree on derived state ------------------
    listed = expect("GET", "/api/applications", 200).json()["applications"]
    if not listed:
        FAILURES.append("no applications listed after seeding")
    target = listed[0]

    detail = expect("GET", f"/api/applications/{target['id']}", 200).json()
    derived_keys = ("furthest_stage", "age_days", "days_in_stage",
                    "days_since_activity", "followups_sent")
    for key in derived_keys:
        if key not in detail:
            FAILURES.append(f"detail missing derived field '{key}'")
        elif detail[key] != target[key]:
            FAILURES.append(
                f"list/detail disagree on '{key}': {target[key]} vs {detail[key]}"
            )
    for key in ("derived", "events", "company", "role", "status"):
        if key not in detail:
            FAILURES.append(f"detail missing '{key}'")

    # ---- 5. write path --------------------------------------------------
    expect("POST", "/api/applications", 201, json={
        "company": "Acme Corp", "role": "Site Reliability Engineer",
        "source": "referral", "priority": 5, "salary_min": "150000",
    })
    new_id = "acme-corp-site-reliability-engineer"

    expect("POST", f"/api/applications/{new_id}/apply", 200, json={})
    # The engine refuses to skip rungs (applied -> onsite is a 400 unless
    # forced), so walk the ladder the way a real search does.
    for stage in ("screen", "interview", "onsite"):
        expect("POST", f"/api/applications/{new_id}/move", 200,
               json={"status": stage, "note": f"moved to {stage}"})
    expect("POST", f"/api/applications/{new_id}/move", 400,
           json={"status": "applied"})
    expect("POST", f"/api/applications/{new_id}/note", 200,
           json={"text": "panel went well"})
    expect("POST", f"/api/applications/{new_id}/followup", 200, json={})
    expect("POST", f"/api/applications/{new_id}/next", 200,
           json={"action": "Send thank-you", "on": "2026-09-20"})
    expect("POST", f"/api/applications/{new_id}/interview", 200,
           json={"on": "2026-09-25", "kind": "technical"})

    moved = expect("GET", f"/api/applications/{new_id}", 200).json()
    if moved["status"] != "onsite":
        FAILURES.append(f"move did not stick: status={moved['status']}")
    if moved.get("furthest_stage") != "onsite":
        FAILURES.append(f"furthest_stage wrong after move: {moved.get('furthest_stage')}")
    if moved["salary_min"] != 15000000:
        FAILURES.append(f"salary parsing wrong: {moved['salary_min']} (want 15000000)")

    # ---- 6. error paths -------------------------------------------------
    expect("POST", "/api/applications", 400, json={
        "company": "X", "role": "Y", "status": "nonsense",
    })
    expect("GET", "/api/applications/does-not-exist", 404)
    expect("POST", "/api/applications/does-not-exist/move", 404,
           json={"status": "screen"})

    # ---- report ----------------------------------------------------------
    print(f"JOBFLOW API smoke: {CHECKS} checks")
    print(f"  seeded applications : {summary['totals']['submitted']}")
    print(f"  response rate       : {summary['funnel']['response_rate']}%")
    print(f"  onsite rung reached : {onsite['reached']}")
    print(f"  plan actions        : {plan['total']}")
    print(f"  headline            : {plan['headline'][:96]}")

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print(f"\nPASS — all {CHECKS} checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

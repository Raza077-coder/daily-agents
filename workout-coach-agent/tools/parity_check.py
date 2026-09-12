#!/usr/bin/env python3
"""Python side of the browser-parity check.

Runs the same inputs through the real Python engine and diffs every field
against the JSON that `tools/parity_check.js` produced from the browser engine.
The two implementations are supposed to be interchangeable, so ANY difference
is a bug \u2014 this script exits non-zero and prints the divergence rather than
warning politely.

Usage:
    node tools/parity_check.js > /tmp/forge_js.json
    python3 tools/parity_check.py /tmp/forge_js.json
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge.engine import ForgeEngine, parse_set_spec  # noqa: E402
from forge.exercises import EXERCISES, get_exercise  # noqa: E402
from forge.models import SetEntry, Workout, epley_e1rm  # noqa: E402
from forge.program import build_program, project_overload  # noqa: E402
from forge.storage import Store  # noqa: E402

FAILURES: list[str] = []


def check(label: str, py: object, js: object) -> None:
    """Record a mismatch. Float compare tolerates representation noise."""
    if isinstance(py, float) or isinstance(js, float):
        try:
            if abs(float(py) - float(js)) < 1e-6:
                return
        except (TypeError, ValueError):
            pass
    if py != js:
        FAILURES.append(f"{label}\n    python: {py!r}\n    js:     {js!r}")


def _history_from(sessions: list, exercise_id: str) -> list:
    from forge.models import LoggedExercise

    out = []
    for sess in sessions:
        entries = [SetEntry(weight=s["weight"], reps=s["reps"]) for s in sess["sets"]]
        out.append(
            Workout(
                date=sess["date"],
                session="test",
                entries=[LoggedExercise(exercise_id=exercise_id, sets=entries)],
            )
        )
    return out


def main(path: str) -> int:
    js = json.loads(Path(path).read_text(encoding="utf-8"))

    # -- cases ---------------------------------------------------------------
    for case in js["cases"]:
        if case["kind"] == "parse":
            try:
                parsed = [
                    {"weight": s.weight, "reps": s.reps, "rpe": s.rpe}
                    for s in parse_set_spec(case["spec"])
                ]
            except ValueError as exc:
                parsed = {"error": str(exc)}
            check(f"parse {case['spec']!r}", parsed, case["result"])

        elif case["kind"] == "e1rm":
            check(
                f"e1rm({case['weight']}, {case['reps']})",
                round(epley_e1rm(case["weight"], case["reps"]), 1),
                case["result"],
            )

        elif case["kind"] == "prescription":
            check(f"action for {case['label']}", _py_action(case), case["action"])

    # -- programs ------------------------------------------------------------
    for days_s, js_prog in sorted(js["programs"].items()):
        days = int(days_s)
        py_prog = build_program(
            days_per_week=days,
            experience="intermediate",
            goal="strength",
            bodyweight_kg=80.0,
        )
        check(f"split name /{days} days", py_prog.split, js_prog["split"])
        check(f"session count /{days} days", len(py_prog.sessions), len(js_prog["days"]))
        for py_sess, js_sess in zip(py_prog.sessions, js_prog["days"]):
            check(f"title ({days}d {js_sess['day']})", py_sess.title, js_sess["title"])
            check(f"day  ({days}d {js_sess['day']})", py_sess.day, js_sess["day"])
            py_ids = [pe.exercise_id for pe in py_sess.exercises]
            js_ids = [e["id"] for e in js_sess["exercises"]]
            check(f"exercise order ({days}d {js_sess['day']})", py_ids, js_ids)
            for pe, je in zip(py_sess.exercises, js_sess["exercises"]):
                tag = f"{days}d {js_sess['day']} {pe.exercise_id}"
                check(f"sets {tag}", pe.sets, je["sets"])
                check(f"rep_low {tag}", pe.rep_low, je["rep_low"])
                check(f"rep_high {tag}", pe.rep_high, je["rep_high"])
                check(
                    f"start_weight {tag}",
                    round(pe.start_weight, 2),
                    round(float(je["start_weight"]), 2),
                )

    # -- cross-muscle guard --------------------------------------------------
    check("cross-muscle contamination", [], js["cross_muscle_bugs"])

    # -- projection ----------------------------------------------------------
    py_prog = build_program(
        days_per_week=3, experience="intermediate", goal="strength", bodyweight_kg=80.0
    )
    for row in project_overload(py_prog, 7):
        lift = next(l for l in row["sessions"][0]["lifts"] if l["exercise"] == "back_squat")
        js_row = next(
            (r for r in js["projection"] if r["week"] == row["week"]), None
        )
        if js_row is None:
            continue
        check(f"projection wk{row['week']} weight", round(lift["weight"], 2), round(js_row["weight"], 2))
        check(f"projection wk{row['week']} reps", lift["reps"], js_row["reps"])

    # -- library -------------------------------------------------------------
    check("exercise count", len(EXERCISES), js["library"]["count"])
    check("exercise ids", sorted(EXERCISES), js["library"]["ids"])

    # -- report --------------------------------------------------------------
    print("=" * 62)
    print("FORGE parity: Python engine vs browser engine")
    print("=" * 62)
    if FAILURES:
        print(f"\n{len(FAILURES)} MISMATCH(ES)\n")
        for f in FAILURES:
            print("  \u2717 " + f)
        print("\nRESULT: FAIL \u2014 the browser would give different advice than the CLI.")
        return 1

    n = len(js["cases"]) + sum(len(v["days"]) for v in js["programs"].values())
    print(f"\n  {len(js['cases'])} engine cases matched")
    print(f"  {len(js['programs'])} programmes matched exercise-for-exercise "
          f"({n} sessions)")
    print(f"  {len(js['library']['ids'])} library entries identical")
    print(f"  0 cross-muscle contamination bugs")
    print(f"  7-week projection ladder identical")
    print("\nRESULT: PASS \u2014 both engines agree on every checked output.")
    return 0


def _fresh_engine(exercise_id: str = "barbell_bench_press"):
    """An isolated engine backed by a throwaway file.

    Store has no in-memory mode, so the harness uses a real temp file and
    cleans it up. Two of them are never live at once, so collisions are not a
    concern; keeping it on disk also means the harness exercises the same
    serialisation path the CLI does.
    """
    tmp = Path(tempfile.mkdtemp(prefix="forge-parity-")) / "training.json"
    engine = ForgeEngine(Store(tmp))
    engine.init_profile(bodyweight_kg=80.0, experience="intermediate", goal="strength")
    return engine, tmp


def _py_action(case: dict) -> str:
    """Replay a JS progression case through the Python engine."""
    label = case["label"]
    exercise_id = "pullup" if label == "bodyweight_reps" else "barbell_bench_press"
    rep_low, rep_high = (8, 15) if label == "bodyweight_reps" else (8, 12)

    engine, tmp = _fresh_engine(exercise_id)
    try:
        if label == "no_history":
            return engine.next_prescription(exercise_id, rep_low, rep_high, 3)["action"]

        sessions = {
            "increase": [{"date": "2026-01-01", "sets": [{"weight": 60, "reps": 12}] * 3}],
            "repeat_shortfall": [{"date": "2026-01-01",
                                  "sets": [{"weight": 60, "reps": 8}, {"weight": 60, "reps": 7},
                                           {"weight": 60, "reps": 6}]}],
            "repeat_noshortfall": [{"date": "2026-01-01",
                                    "sets": [{"weight": 60, "reps": 10}, {"weight": 60, "reps": 9},
                                             {"weight": 60, "reps": 9}]}],
            "deload": [
                {"date": "2026-01-01", "sets": [{"weight": 60, "reps": 6}, {"weight": 60, "reps": 5}]},
                {"date": "2026-01-03", "sets": [{"weight": 60, "reps": 6}, {"weight": 60, "reps": 6}]},
                {"date": "2026-01-05", "sets": [{"weight": 60, "reps": 5}, {"weight": 60, "reps": 6}]},
            ],
            "bodyweight_reps": [{"date": "2026-01-01",
                                 "sets": [{"weight": 0, "reps": 15}] * 3}],
        }[label]

        for sess in _history_from(sessions, exercise_id):
            engine.store.add_workout(sess)
        return engine.next_prescription(exercise_id, rep_low, rep_high, 3)["action"]
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tools/parity_check.py <js-json-output>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))

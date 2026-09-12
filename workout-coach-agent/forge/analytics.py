"""Training analytics for FORGE.

Everything here is a pure function over a list of `Workout` objects — no
storage access, no clock reads, no randomness. That makes every number in the
weekly report reproducible and unit-testable, and it means the same code backs
the CLI, the REST API and the browser demo.

Volume convention: **tonnage** (weight x reps) for loaded work. Bodyweight
movements contribute 0 kg of tonnage, which is stated plainly in reports so a
high pull-up count never looks like lost work.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .exercises import EXERCISES, MAJOR_MUSCLES
from .models import Workout, parse_date

# Secondary muscles count as a fraction of a set, recognising that they are
# trained meaningfully but not directly.
SECONDARY_CREDIT = 0.5

# Weekly working-set targets per muscle for a general trainee. Used for the
# balance report, not as a hard rule.
VOLUME_TARGETS: Dict[str, int] = {
    "chest": 10,
    "back": 12,
    "lats": 10,
    "front_delts": 8,
    "side_delts": 8,
    "rear_delts": 6,
    "quads": 10,
    "hamstrings": 8,
    "glutes": 8,
    "calves": 6,
    "biceps": 8,
    "triceps": 8,
    "core": 6,
}


def week_bounds(day: Any, monday_start: bool = True) -> tuple:
    """ISO week containing `day`. Returns (monday, sunday) as dates."""
    d = parse_date(day)
    if monday_start:
        start = d - timedelta(days=d.weekday())
    else:
        start = d - timedelta(days=(d.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


def week_label(day: Any) -> str:
    d = parse_date(day)
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


def _sets_of(workout: Workout, exercise_id: str) -> int:
    return sum(len(e.sets) for e in workout.entries if e.exercise_id == exercise_id)


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def volume_by_muscle(
    workouts: Sequence[Workout], weighted: bool = True
) -> Dict[str, Dict[str, Any]]:
    """Working sets and tonnage attributed to each muscle.

    With `weighted=True`, an exercise credits its primary muscle a full set and
    each secondary muscle half a set — the standard way of accounting for the
    fact that a bench press trains triceps without being a triceps exercise.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for w in workouts:
        for entry in w.entries:
            ex = EXERCISES.get(entry.exercise_id)
            if ex is None:
                continue
            n = len(entry.sets)
            if n == 0:
                continue
            if weighted:
                shares = [(ex.primary, 1.0)] + [
                    (m, SECONDARY_CREDIT) for m in ex.secondary
                ]
            else:
                shares = [(ex.primary, 1.0)]
            for muscle, share in shares:
                row = out.setdefault(
                    muscle, {"muscle": muscle, "sets": 0.0, "tonnage": 0.0, "exercises": set()}
                )
                row["sets"] += n * share
                row["tonnage"] += entry.volume * share
                row["exercises"].add(ex.id)

    for row in out.values():
        row["sets"] = round(row["sets"], 1)
        row["tonnage"] = round(row["tonnage"], 1)
        row["exercises"] = sorted(row["exercises"])
        row["target"] = VOLUME_TARGETS.get(row["muscle"], 0)
        row["pct_of_target"] = (
            round(row["sets"] / row["target"] * 100.0, 1) if row["target"] else 0.0
        )
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["sets"]))


def weekly_tonnage(workouts: Sequence[Workout]) -> List[Dict[str, Any]]:
    """One row per ISO week: sessions, sets, reps and total tonnage."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for w in workouts:
        label = week_label(w.date)
        row = buckets.setdefault(
            label,
            {
                "week": label,
                "week_start": week_bounds(w.date)[0].isoformat(),
                "sessions": 0,
                "sets": 0,
                "reps": 0,
                "tonnage": 0.0,
                "bodyweight_sets": 0,
            },
        )
        row["sessions"] += 1
        row["sets"] += w.total_sets
        row["reps"] += w.total_reps
        row["tonnage"] += w.volume
        row["bodyweight_sets"] += sum(
            len(e.sets)
            for e in w.entries
            if EXERCISES.get(e.exercise_id) and EXERCISES[e.exercise_id].is_bodyweight
        )

    rows = []
    for row in sorted(buckets.values(), key=lambda r: r["week"]):
        row["tonnage"] = round(row["tonnage"], 1)
        rows.append(row)
    return rows


def muscle_coverage(workouts: Sequence[Workout]) -> Dict[str, Any]:
    """Which major muscles got meaningful work, and which were neglected."""
    trained = volume_by_muscle(workouts)
    hit = [m for m in MAJOR_MUSCLES if trained.get(m, {}).get("sets", 0) >= 4]
    light = [
        m for m in MAJOR_MUSCLES
        if 0 < trained.get(m, {}).get("sets", 0) < 4
    ]
    missed = [m for m in MAJOR_MUSCLES if not trained.get(m, {}).get("sets")]
    return {
        "trained": hit,
        "light": light,
        "missed": missed,
        "coverage_pct": round(len(hit) / len(MAJOR_MUSCLES) * 100.0, 1),
        "detail": trained,
    }


def balance_report(workouts: Sequence[Workout]) -> Dict[str, Any]:
    """Find structural imbalances worth acting on.

    Checks the four pairs that most often drift apart in self-written
    programmes: pushing vs pulling, quads vs hamstrings, and the two most
    commonly skipped muscles (rear delts and calves).
    """
    vol = volume_by_muscle(workouts)
    get = lambda m: vol.get(m, {}).get("sets", 0.0)

    def d(k):
        return round(k, 1)

    findings: List[Dict[str, Any]] = []
    push = get("chest") + get("front_delts") + get("triceps")
    pull = get("back") + get("lats") + get("rear_delts") + get("biceps")
    if push and pull:
        ratio = d(pull / push)
        if ratio < 0.8:
            findings.append(
                {
                    "issue": "push_dominant",
                    "severity": "high" if ratio < 0.65 else "medium",
                    "detail": f"Pulling volume is only {ratio:.2f}x pushing. "
                    "Add rows or lat pulldowns — balanced shoulders depend on it.",
                    "ratio": ratio,
                }
            )
        elif ratio > 1.4:
            findings.append(
                {
                    "issue": "pull_dominant",
                    "severity": "low",
                    "detail": f"Pulling volume is {ratio:.2f}x pushing. Unusual, "
                    "and rarely a problem — just do not neglect pressing.",
                    "ratio": ratio,
                }
            )

    quads, hams = get("quads"), get("hamstrings") + get("glutes")
    if quads and hams:
        ratio = d(quads / hams)
        if ratio > 1.6:
            findings.append(
                {
                    "issue": "quad_dominant",
                    "severity": "medium",
                    "detail": f"Quad volume is {ratio:.2f}x posterior chain. "
                    "RDLs, leg curls or hip thrusts will protect the knees and "
                    "lift the deadlift.",
                    "ratio": ratio,
                }
            )

    if get("rear_delts") < 4 and (push or pull):
        findings.append(
            {
                "issue": "neglected_rear_delts",
                "severity": "medium",
                "detail": f"Only {d(get('rear_delts'))} rear-delt sets. Face pulls "
                "or reverse flyes are cheap insurance for shoulder health.",
                "ratio": 0.0,
            }
        )
    if get("calves") < 3 and get("quads") + get("hamstrings") > 0:
        findings.append(
            {
                "issue": "neglected_calves",
                "severity": "low",
                "detail": "Calves are getting almost nothing. Two sets at the end "
                "of a leg day is enough.",
                "ratio": 0.0,
            }
        )

    if not findings:
        findings.append(
            {
                "issue": "balanced",
                "severity": "none",
                "detail": "No structural imbalances detected in the logged data. "
                "Keep doing what you are doing.",
                "ratio": 1.0,
            }
        )

    order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    return {
        "balanced": all(f["severity"] == "none" for f in findings),
        "push_pull_ratio": d(pull / push) if push else 0.0,
        "quad_posterior_ratio": d(quads / (hams or 1)) if quads else 0.0,
        "findings": sorted(findings, key=lambda f: order[f["severity"]]),
    }


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------


def week_streak(workouts: Sequence[Workout], today: Any = None, target: int = 3) -> Dict[str, Any]:
    """Consecutive weeks hitting a session target.

    The current, still-open week never breaks the streak: if you are mid-week
    and short of the target, the streak stands until the week is actually over.
    """
    ref = parse_date(today or date.today())
    if not workouts:
        return {"current": 0, "longest": 0, "target": target, "this_week": 0}

    per_week: Dict[str, int] = {}
    for w in workouts:
        per_week[week_label(w.date)] = per_week.get(week_label(w.date), 0) + 1

    # Walk backwards week by week from the reference date.
    current = 0
    cursor = ref
    while True:
        label = week_label(cursor)
        if per_week.get(label, 0) >= target:
            current += 1
            cursor = cursor - timedelta(days=7)
        else:
            is_open_week = week_bounds(cursor)[1] >= ref
            if is_open_week and per_week.get(label, 0) < target and current == 0:
                cursor = cursor - timedelta(days=7)
                continue
            break

    longest, run = 0, 0
    for label in sorted(per_week):
        if per_week[label] >= target:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    return {
        "current": current,
        "longest": max(longest, current),
        "target": target,
        "this_week": per_week.get(week_label(ref), 0),
    }


def consistency(workouts: Sequence[Workout], days: int = 28, today: Any = None) -> Dict[str, Any]:
    """Adherence over a rolling window: how often you actually showed up."""
    ref = parse_date(today or date.today())
    start = ref - timedelta(days=days - 1)
    in_window = [w for w in workouts if start <= w.day <= ref]
    active_days = sorted({w.date for w in in_window})
    weeks = max(1.0, days / 7.0)
    return {
        "window_days": days,
        "start": start.isoformat(),
        "end": ref.isoformat(),
        "sessions": len(in_window),
        "active_days": len(active_days),
        "sessions_per_week": round(len(in_window) / weeks, 1),
        "hit_rate_pct": round(len(active_days) / days * 100.0, 1),
        "days": active_days,
    }


def detect_plateaus(workouts: Sequence[Workout], min_sessions: int = 4) -> List[Dict[str, Any]]:
    """Lifts whose estimated 1RM has not improved across recent sessions."""
    from .storage import Store  # local import: analytics stays storage-free at rest

    by_exercise: Dict[str, List[Dict[str, Any]]] = {}
    for w in sorted(workouts, key=lambda x: x.date):
        for e in w.entries:
            if not e.sets:
                continue
            by_exercise.setdefault(e.exercise_id, []).append(
                {"date": w.date, "e1rm": e.best_e1rm, "top": e.top_set.weight if e.top_set else 0}
            )

    out = []
    for eid, rows in by_exercise.items():
        if len(rows) < min_sessions:
            continue
        recent = rows[-min_sessions:]
        values = [r["e1rm"] for r in recent]
        spread = max(values) - min(values)
        if spread < max(0.5, 0.01 * max(values)):
            ex = EXERCISES.get(eid)
            out.append(
                {
                    "exercise": eid,
                    "name": ex.name if ex else eid,
                    "sessions": len(recent),
                    "since": recent[0]["date"],
                    "e1rm": round(max(values), 1),
                    "spread": round(spread, 1),
                    "hint": "Change the rep range, add a set, or rotate the "
                    "variation for 3-4 weeks before returning to this lift.",
                }
            )
    return sorted(out, key=lambda r: r["name"])


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summary(
    workouts: Sequence[Workout], today: Any = None, days: int = 28
) -> Dict[str, Any]:
    """The single object every surface renders: overview, volume, balance, streak."""
    ref = parse_date(today or date.today())
    ordered = sorted(workouts, key=lambda w: w.date)
    start = ref - timedelta(days=days - 1)
    window = [w for w in ordered if start <= w.day <= ref]

    weeks = weekly_tonnage(ordered)
    this_week = next((w for w in weeks if w["week"] == week_label(ref)), None)
    prev_label = week_label(ref - timedelta(days=7))
    last_week = next((w for w in weeks if w["week"] == prev_label), None)

    delta = 0.0
    if this_week and last_week and last_week["tonnage"]:
        delta = round(
            (this_week["tonnage"] - last_week["tonnage"]) / last_week["tonnage"] * 100.0, 1
        )

    return {
        "as_of": ref.isoformat(),
        "window_days": days,
        "total_workouts": len(ordered),
        "first_session": ordered[0].date if ordered else None,
        "last_session": ordered[-1].date if ordered else None,
        "lifetime_tonnage": round(sum(w.volume for w in ordered), 1),
        "lifetime_sets": sum(w.total_sets for w in ordered),
        "window": {
            "sessions": len(window),
            "tonnage": round(sum(w.volume for w in window), 1),
            "sets": sum(w.total_sets for w in window),
        },
        "this_week": this_week,
        "last_week": last_week,
        "tonnage_delta_pct": delta,
        "streak": week_streak(ordered, ref),
        "consistency": consistency(ordered, days, ref),
        "volume_by_muscle": volume_by_muscle(window),
        "balance": balance_report(window),
        "coverage": muscle_coverage(window),
        "weekly_tonnage": weeks,
        "plateaus": detect_plateaus(ordered),
    }

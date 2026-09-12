"""The FORGE training engine.

This is the brain: it ingests logged work, reasons about double progression,
detects personal records and answers "what should I do next?" with a concrete,
explainable prescription.

Double progression in one sentence: stay at a weight until every prescribed set
hits the top of the rep range, then add the smallest available increment and
reset to the bottom of the range. It is the most reliable way to get stronger
without a coach, and it is completely mechanical \u2014 which is exactly why it can
be deterministic code instead of a language model.

Nothing here writes to disk. `ForgeEngine` owns a `Store`, and every mutating
method commits explicitly so the engine stays easy to test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from .exercises import EXERCISES, get_exercise
from .models import (
    LoggedExercise,
    Program,
    SetEntry,
    Workout,
    epley_e1rm,
    parse_date,
)
from .storage import Store

# Trailing sessions used to judge whether a lift is stalling.
STALL_WINDOW = 3
# A lift is "ready to progress" when it has hit the top of the range this often.
READY_SESSIONS = 1
# Consecutive sessions with no e1RM improvement before we call it a plateau.
PLATEAU_SESSIONS = 4


def parse_set_spec(spec: str) -> List[SetEntry]:
    """Parse a compact set spec into SetEntry objects.

    Accepts the shapes people actually type:

        60x8            one set of 8 at 60
        60x8x3          three sets of 8 at 60
        60x8,60x8,60x6  explicit sets
        60x8x2,60x6     mixed
        bw x10          bodyweight, 10 reps
        bw+10 x5        bodyweight plus 10 added

    Raises ValueError with the offending fragment rather than skipping it, so a
    typo never silently drops work from the log.
    """
    out: List[SetEntry] = []
    if not spec or not spec.strip():
        raise ValueError("empty set spec")

    for chunk in spec.replace("|", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue

        body = chunk
        rpe = None
        if "@" in body:
            body, _, rpe_raw = body.partition("@")
            try:
                rpe = float(rpe_raw.strip())
            except ValueError as exc:
                raise ValueError(f"bad RPE in {chunk!r}") from exc
            body = body.strip()

        if "x" not in body.lower():
            raise ValueError(f"bad set {chunk!r} \u2014 expected WEIGHTxREPS or WEIGHTxREPSxSETS")

        parts = [p.strip() for p in body.lower().split("x")]
        if len(parts) not in (2, 3):
            raise ValueError(f"bad set {chunk!r} \u2014 expected WEIGHTxREPS or WEIGHTxREPSxSETS")

        weight = _parse_weight(parts[0])
        try:
            reps = int(float(parts[1]))
        except ValueError as exc:
            raise ValueError(f"bad rep count in {chunk!r}") from exc
        count = 1
        if len(parts) == 3:
            try:
                count = int(float(parts[2]))
            except ValueError as exc:
                raise ValueError(f"bad set count in {chunk!r}") from exc

        if reps <= 0:
            raise ValueError(f"rep count must be positive in {chunk!r}")
        if count <= 0:
            raise ValueError(f"set count must be positive in {chunk!r}")

        for _ in range(count):
            out.append(SetEntry(weight=weight, reps=reps, rpe=rpe))

    if not out:
        raise ValueError(f"no sets parsed from {spec!r}")
    return out


def _parse_weight(token: str) -> float:
    """Weight tokens: `60`, `60.5`, `bw`, `bw+10`, `bw-5`, `0`."""
    token = token.strip().lower().replace(" ", "")
    if not token:
        raise ValueError("missing weight")
    if token in ("bw", "bw+0", "bodyweight", "bw+-0"):
        return 0.0
    if token.startswith("bw"):
        rest = token[2:]
        if rest.startswith("+"):
            rest = rest[1:]
        try:
            return float(rest)
        except ValueError as exc:
            raise ValueError(f"bad bodyweight offset {token!r}") from exc
    try:
        return float(token)
    except ValueError as exc:
        raise ValueError(f"bad weight {token!r}") from exc


def round_to_increment(weight: float, increment: float) -> float:
    """Snap a load to the nearest real plate/stack jump.

    Returns 0.0 for bodyweight movements (increment 0), so `bw` never turns
    into a floating-point artefact.
    """
    if increment <= 0:
        return 0.0
    steps = round(weight / increment)
    return round(steps * increment, 2)


class ForgeEngine:
    """Stateful facade over the library, the store and the programming rules."""

    def __init__(self, store: Optional[Store] = None):
        self.store = store or Store()

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def init_profile(
        self,
        bodyweight_kg: float = 0.0,
        experience: str = "beginner",
        goal: str = "strength",
        equipment: Optional[Sequence[str]] = None,
        name: str = "",
        units: str = "kg",
        reset: bool = False,
    ) -> Dict[str, Any]:
        if reset:
            self.store.reset()
        return self.store.set_profile(
            bodyweight_kg=float(bodyweight_kg or 0.0),
            experience=experience,
            goal=goal,
            equipment=sorted(set(equipment or [])),
            name=name,
            units=units,
        )

    @property
    def profile(self) -> Dict[str, Any]:
        """The athlete profile as a plain dict (read-only view)."""
        return self.store.profile

    def available_equipment(self) -> List[str]:
        """Equipment the athlete has. Empty list means "assume a full gym"."""
        equip = self.store.profile.get("equipment") or []
        return list(equip) if equip else []

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(
        self,
        exercise: str,
        sets: Any,
        day: Optional[Any] = None,
        session: str = "",
        notes: str = "",
        duration_min: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Log one exercise. `sets` is a spec string or a list of SetEntry/dicts.

        Returns a result dict including any PRs set, so the caller (CLI or API)
        can celebrate without re-deriving anything.
        """
        ex = get_exercise(exercise)
        parsed = self._normalise_sets(sets)
        if not parsed:
            raise ValueError("no sets to log")

        day_iso = parse_date(day or date.today()).isoformat()
        entry = LoggedExercise(exercise_id=ex.id, sets=parsed, notes=notes)
        workout = Workout(
            date=day_iso,
            session=session,
            entries=[entry],
            duration_min=duration_min,
        )

        # Capture what came before, so PR detection compares against history
        # rather than against the workout we are about to write.
        before = self._alltime_best(ex.id)
        self.store.add_workout(workout)
        after = self._alltime_best(ex.id)
        prs = self._describe_prs(ex.id, before, after, parsed)

        return {
            "exercise": ex.id,
            "name": ex.name,
            "date": day_iso,
            "sets": [s.to_dict() for s in parsed],
            "volume": entry.volume,
            "total_reps": entry.total_reps,
            "top_weight": entry.top_set.weight if entry.top_set else 0.0,
            "best_e1rm": entry.best_e1rm,
            "prs": prs,
            "personal_best": after,
        }

    def _normalise_sets(self, sets: Any) -> List[SetEntry]:
        if isinstance(sets, str):
            return parse_set_spec(sets)
        out: List[SetEntry] = []
        for item in sets or []:
            if isinstance(item, SetEntry):
                out.append(item)
            elif isinstance(item, dict):
                out.append(SetEntry.from_dict(item))
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                out.append(SetEntry(weight=float(item[0]), reps=int(item[1])))
            else:
                raise ValueError(f"cannot interpret set {item!r}")
        return out

    def _alltime_best(self, exercise_id: str) -> Dict[str, Any]:
        """Best ever top-set weight, best estimated 1RM and heaviest session volume."""
        hist = self.store.exercise_history(exercise_id)
        if not hist:
            return {"top_weight": 0.0, "best_e1rm": 0.0, "best_volume": 0.0}
        return {
            "top_weight": max(r["top_weight"] for r in hist),
            "best_e1rm": max(r["best_e1rm"] for r in hist),
            "best_volume": max(r["volume"] for r in hist),
        }

    def _describe_prs(
        self,
        exercise_id: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        sets: List[SetEntry],
    ) -> List[Dict[str, Any]]:
        """Classify which records (if any) this session broke."""
        name = EXERCISES[exercise_id].name
        prs: List[Dict[str, Any]] = []

        new_top = max(s.weight for s in sets)
        new_e1rm = max(s.e1rm for s in sets)

        if new_top > before["top_weight"] and new_top > 0:
            prs.append(
                {
                    "kind": "weight",
                    "label": f"heaviest ever {name}",
                    "value": new_top,
                    "previous": before["top_weight"],
                }
            )
        if new_e1rm > before["best_e1rm"] and new_e1rm > 0:
            prs.append(
                {
                    "kind": "e1rm",
                    "label": f"best estimated 1RM on {name}",
                    "value": new_e1rm,
                    "previous": before["best_e1rm"],
                }
            )
        return prs

    def remove_day(self, day: Any) -> bool:
        return self.store.remove_workout(day)

    # ------------------------------------------------------------------
    # Progression \u2014 the heart of the coach
    # ------------------------------------------------------------------

    def history(self, exercise: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        ex = get_exercise(exercise)
        rows = self.store.exercise_history(ex.id)
        return rows[-limit:] if limit else rows

    def next_prescription(
        self,
        exercise: str,
        rep_low: int = 8,
        rep_high: int = 12,
        sets: int = 3,
    ) -> Dict[str, Any]:
        """What to do on this lift next session, with a human-readable reason.

        The decision tree, in order:
          1. No history            -> estimate a starting load and prove the work
          2. Every set hit top of range -> add one increment, drop to bottom
          3. Consistent misses at bottom -> deload 10%
          4. Plateau in estimated 1RM   -> change a variable (reps or volume)
          5. Otherwise             -> repeat the load and add reps
        """
        ex = get_exercise(exercise)
        hist = self.store.exercise_history(ex.id)

        if not hist:
            start = self.starting_weight(ex.id)
            return {
                "exercise": ex.id,
                "name": ex.name,
                "action": "start",
                "weight": start,
                "target_reps_low": rep_low,
                "target_reps_high": rep_high,
                "sets": sets,
                "reason": (
                    "No history for this lift yet \u2014 here is a deliberately "
                    "conservative starting load. Leave 2-3 reps in reserve and "
                    "we will calibrate from real data next session."
                ),
                "status": "new",
            }

        recent = hist[-STALL_WINDOW:]
        last = hist[-1]
        last_sets = last["sets"]
        low_hits = sum(1 for s in last_sets if int(s["reps"]) < rep_low)
        top_hits = sum(1 for s in last_sets if int(s["reps"]) >= rep_high)
        working = last["top_weight"]

        # 2. Earned an increase.
        if last_sets and top_hits == len(last_sets) and working > 0:
            nxt = round_to_increment(working + ex.increment, ex.increment)
            return {
                "exercise": ex.id,
                "name": ex.name,
                "action": "increase",
                "weight": nxt,
                "target_reps_low": rep_low,
                "target_reps_high": rep_high,
                "sets": sets,
                "reason": (
                    f"All {len(last_sets)} sets hit {rep_high}+ reps on "
                    f"{_fmt(working)}. Add one increment "
                    f"({_fmt_kg(ex.increment)}) to {_fmt(nxt)} and start again at "
                    f"{rep_low} reps \u2014 that is the double-progression rule."
                ),
                "status": "progressing",
                "previous_weight": working,
            }

        # Bodyweight work progresses by reps, not load.
        if ex.is_bodyweight and working == 0:
            best_reps = int(last["top_reps"])
            if top_hits == len(last_sets):
                return {
                    "exercise": ex.id,
                    "name": ex.name,
                    "action": "increase_reps",
                    "weight": 0.0,
                    "target_reps_low": rep_high + 2,
                    "target_reps_high": rep_high + 5,
                    "sets": sets,
                    "reason": (
                        f"Bodyweight sets are all at {rep_high}+ reps. Move the "
                        "range up (or add external load) to keep the stimulus."
                    ),
                    "status": "progressing",
                    "previous_reps": best_reps,
                }

        # 3. Repeated misses -> deload.
        misses = [
            r for r in recent
            if r["sets"] and sum(1 for s in r["sets"] if int(s["reps"]) < rep_low) > 0
        ]
        if len(misses) >= STALL_WINDOW and working > 0:
            back = round_to_increment(working * 0.9, ex.increment)
            return {
                "exercise": ex.id,
                "name": ex.name,
                "action": "deload",
                "weight": back,
                "target_reps_low": rep_low,
                "target_reps_high": rep_high,
                "sets": sets,
                "reason": (
                    f"{STALL_WINDOW} sessions in a row came in under {rep_low} "
                    f"reps. Drop ~10% to {_fmt(back)}, rebuild quality reps, and "
                    "we will climb back past this weight within a few sessions."
                ),
                "status": "deload",
                "previous_weight": working,
            }

        # 4. Plateau: load is static and estimated strength is flat.
        e1rms = [r["best_e1rm"] for r in hist[-(PLATEAU_SESSIONS + 1):]]
        flat = len(e1rms) > PLATEAU_SESSIONS and (
            max(e1rms) - min(e1rms)
        ) < max(0.5, 0.01 * max(e1rms))
        if flat and working > 0:
            return {
                "exercise": ex.id,
                "name": ex.name,
                "action": "change_stimulus",
                "weight": working,
                "target_reps_low": max(3, rep_low - 3),
                "target_reps_high": max(5, rep_low - 1),
                "sets": sets + 1,
                "reason": (
                    f"Estimated 1RM has not moved across {PLATEAU_SESSIONS} "
                    "sessions. Rather than grinding the same numbers, drop the "
                    "rep range and add a set for a few weeks \u2014 a different "
                    "stimulus, same lift."
                ),
                "status": "plateau",
                "previous_weight": working,
            }

        # 5. Default: repeat and chase reps.
        target_low = min(rep_high, max(rep_low, int(last["top_reps"]) + 1))
        return {
            "exercise": ex.id,
            "name": ex.name,
            "action": "repeat",
            "weight": working,
            "target_reps_low": target_low,
            "target_reps_high": rep_high,
            "sets": sets,
            "reason": (
                f"Hold {_fmt(working)} and beat your last session "
                f"({_fmt(last['top_weight'])}x{last['top_reps']}). "
                # The two situations need different words: naming a shortfall
                # when every set cleared the bottom of the range reads as a
                # contradiction ("0 set(s) fell under 8 \u2014 close that gap").
                + (
                    f"{low_hits} of {len(last_sets)} set(s) fell under {rep_low} "
                    "last time \u2014 closing that gap is the next win. "
                    if low_hits
                    else f"Every set cleared {rep_low} reps but none reached "
                    f"{rep_high} yet. "
                )
                + "Hit top of range on every set and the weight goes up."
            ),
            "status": "building",
            "previous_weight": working,
        }

    def starting_weight(self, exercise: str) -> float:
        """A conservative first working weight from bodyweight and experience.

        Beginners start at 60% of the reference ratio, intermediates at 80%,
        advanced at 100%. Anything without bodyweight on file falls back to a
        nominal 75 kg so the suggestion is still usable.
        """
        ex = get_exercise(exercise)
        if ex.is_bodyweight:
            return 0.0
        bw = self.store.bodyweight_kg or 75.0
        factor = {"beginner": 0.6, "intermediate": 0.8, "advanced": 1.0}.get(
            self.store.experience, 0.6
        )
        raw = bw * ex.strength_ratio * factor
        if raw <= 0:
            return ex.increment
        snapped = round_to_increment(raw, ex.increment)
        return snapped if snapped > 0 else ex.increment

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def personal_records(self, exercise: Optional[str] = None) -> List[Dict[str, Any]]:
        """All-time bests, per lift or across the whole library."""
        ids = [get_exercise(exercise).id] if exercise else sorted(
            {e.exercise_id for w in self.store.workouts for e in w.entries}
        )
        out = []
        for eid in ids:
            hist = self.store.exercise_history(eid)
            if not hist:
                continue
            best = max(hist, key=lambda r: r["best_e1rm"])
            heaviest = max(hist, key=lambda r: r["top_weight"])
            out.append(
                {
                    "exercise": eid,
                    "name": EXERCISES[eid].name,
                    "sessions": len(hist),
                    "first_date": hist[0]["date"],
                    "last_date": hist[-1]["date"],
                    "best_e1rm": best["best_e1rm"],
                    "best_e1rm_date": best["date"],
                    "heaviest_weight": heaviest["top_weight"],
                    "heaviest_reps": heaviest["top_reps"],
                    "heaviest_date": heaviest["date"],
                    "best_volume": max(r["volume"] for r in hist),
                    "trend": self._trend(hist),
                }
            )
        return sorted(out, key=lambda r: r["name"])

    def _trend(self, hist: List[Dict[str, Any]]) -> Dict[str, Any]:
        """First vs last estimated 1RM: improving, flat or declining."""
        if len(hist) < 2:
            return {"direction": "new", "delta": 0.0, "pct": 0.0}
        first, last = hist[0]["best_e1rm"], hist[-1]["best_e1rm"]
        delta = round(last - first, 1)
        pct = round((delta / first * 100.0), 1) if first else 0.0
        if delta > 0.2:
            direction = "improving"
        elif delta < -0.2:
            direction = "declining"
        else:
            direction = "flat"
        return {"direction": direction, "delta": delta, "pct": pct}

    def progress(self, exercise: str) -> Dict[str, Any]:
        """A per-lift progress timeline with the deltas that matter."""
        ex = get_exercise(exercise)
        hist = self.store.exercise_history(ex.id)
        if not hist:
            raise KeyError(f"no logged sessions for {ex.name} yet")
        first, last = hist[0], hist[-1]
        return {
            "exercise": ex.id,
            "name": ex.name,
            "sessions": len(hist),
            "first": {
                "date": first["date"],
                "weight": first["top_weight"],
                "reps": first["top_reps"],
                "e1rm": first["best_e1rm"],
            },
            "latest": {
                "date": last["date"],
                "weight": last["top_weight"],
                "reps": last["top_reps"],
                "e1rm": last["best_e1rm"],
            },
            "weight_gain": round(last["top_weight"] - first["top_weight"], 1),
            "e1rm_gain": round(last["best_e1rm"] - first["best_e1rm"], 1),
            "trend": self._trend(hist),
            "timeline": hist,
            "next": self.next_prescription(ex.id),
        }

    # ------------------------------------------------------------------
    # Today
    # ------------------------------------------------------------------

    def today(self, day: Optional[Any] = None) -> Dict[str, Any]:
        """The plan for today plus what the progression rules now advise."""
        target = parse_date(day or date.today())
        weekday = target.strftime("%a").lower()
        program = self.store.get_program()
        already = [w for w in self.store.workouts if w.date == target.isoformat()]

        if program is None:
            return {
                "date": target.isoformat(),
                "weekday": weekday,
                "rest": False,
                "program": None,
                "message": "No program yet \u2014 build one with `forge program`.",
                "logged": [w.to_dict() for w in already],
            }

        session = next((s for s in program.sessions if s.day == weekday), None)
        if session is None:
            return {
                "date": target.isoformat(),
                "weekday": weekday,
                "rest": True,
                "program": program.name,
                "message": f"Rest day \u2014 no {program.name} session scheduled.",
                "logged": [w.to_dict() for w in already],
            }

        prescribed = []
        for pe in session.exercises:
            advice = self.next_prescription(
                pe.exercise_id, pe.rep_low, pe.rep_high, pe.sets
            )
            prescribed.append(
                {
                    "exercise": pe.exercise_id,
                    "name": EXERCISES[pe.exercise_id].name,
                    "sets": pe.sets,
                    "rep_low": pe.rep_low,
                    "rep_high": pe.rep_high,
                    "rest_sec": pe.rest_sec,
                    "advice": advice,
                }
            )

        return {
            "date": target.isoformat(),
            "weekday": weekday,
            "rest": False,
            "program": program.name,
            "session": session.title,
            "focus": session.focus,
            "exercises": prescribed,
            "logged": [w.to_dict() for w in already],
        }

    def log_session(
        self, day: Optional[Any] = None, session: str = "", entries: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Log a whole session in one call: {exercise_id_or_name: set_spec}."""
        results = []
        for key, spec in (entries or {}).items():
            results.append(self.log(key, spec, day=day, session=session))
        return {
            "date": parse_date(day or date.today()).isoformat(),
            "session": session,
            "exercises": results,
            "total_volume": round(sum(r["volume"] for r in results), 1),
            "prs": [pr for r in results for pr in r["prs"]],
        }


def _fmt(value: float) -> str:
    """Render a LOAD for prose.

    0 means bodyweight work, so it is named rather than printed. "Hold 0kg" is
    nonsense to read, and for a pull-up the load genuinely is the athlete.
    """
    if not value:
        return "bodyweight"
    return _fmt_kg(value)


def _fmt_kg(value: float) -> str:
    """Render a weight as a number of kilos.

    Used for increments, where 0 has no special meaning worth phrasing.
    """
    if value == int(value):
        return f"{int(value)}kg"
    return f"{value:g}kg"

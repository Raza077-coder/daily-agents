"""Program builder for FORGE.

Turns four inputs \u2014 days available, experience, goal and equipment \u2014 into a
concrete weekly split with real exercises, sets, rep ranges and starting loads
that respect what the athlete actually has access to.

Split selection is prescriptive on purpose. Most people get better results from
a well-reasoned standard template than from a bespoke plan, and a deterministic
builder can guarantee the things that matter: every major muscle trained at
least twice a week, every movement pattern represented, and no exercise
scheduled on back-to-back days.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .exercises import EXERCISES, MAJOR_MUSCLES, filter_exercises
from .models import PlannedExercise, Program, SessionPlan

# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

SPLITS: Dict[str, Dict[str, Any]] = {
    "full_body_2": {
        "days": 2,
        "split": "Full Body (2 days)",
        "sessions": [
            {"day": "mon", "title": "Full Body A", "kind": "full"},
            {"day": "thu", "title": "Full Body B", "kind": "full"},
        ],
    },
    "full_body_3": {
        "days": 3,
        "split": "Full Body (3 days)",
        "sessions": [
            {"day": "mon", "title": "Full Body A", "kind": "full"},
            {"day": "wed", "title": "Full Body B", "kind": "full"},
            {"day": "fri", "title": "Full Body C", "kind": "full"},
        ],
    },
    "upper_lower_4": {
        "days": 4,
        "split": "Upper / Lower (4 days)",
        "sessions": [
            {"day": "mon", "title": "Upper A", "kind": "upper"},
            {"day": "tue", "title": "Lower A", "kind": "lower"},
            {"day": "thu", "title": "Upper B", "kind": "upper"},
            {"day": "fri", "title": "Lower B", "kind": "lower"},
        ],
    },
    "ppl_5": {
        "days": 5,
        "split": "Push / Pull / Legs (5 days)",
        "sessions": [
            {"day": "mon", "title": "Push", "kind": "push"},
            {"day": "tue", "title": "Pull", "kind": "pull"},
            {"day": "wed", "title": "Legs", "kind": "legs"},
            {"day": "thu", "title": "Upper", "kind": "upper"},
            {"day": "sat", "title": "Lower", "kind": "lower"},
        ],
    },
    "ppl_6": {
        "days": 6,
        "split": "Push / Pull / Legs (6 days)",
        "sessions": [
            {"day": "mon", "title": "Push A", "kind": "push"},
            {"day": "tue", "title": "Pull A", "kind": "pull"},
            {"day": "wed", "title": "Legs A", "kind": "legs"},
            {"day": "thu", "title": "Push B", "kind": "push"},
            {"day": "fri", "title": "Pull B", "kind": "pull"},
            {"day": "sat", "title": "Legs B", "kind": "legs"},
        ],
    },
}

# Which movement patterns and muscles each session kind should cover.
SESSION_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "full": [
        {"pattern": "squat", "slots": 1, "focus": ["quads"]},
        {"pattern": "horizontal_push", "slots": 1, "focus": ["chest"]},
        {"pattern": "horizontal_pull", "slots": 1, "focus": ["back"]},
        {"pattern": "hinge", "slots": 1, "focus": ["hamstrings"]},
        {"pattern": "vertical_push", "slots": 1, "focus": ["front_delts"]},
        {"pattern": "vertical_pull", "slots": 1, "focus": ["lats"]},
        {"pattern": "isolation", "slots": 1, "focus": ["biceps", "triceps"]},
        {"pattern": "core", "slots": 1, "focus": ["core"]},
    ],
    "upper": [
        {"pattern": "horizontal_push", "slots": 1, "focus": ["chest"]},
        {"pattern": "horizontal_pull", "slots": 1, "focus": ["back"]},
        {"pattern": "vertical_push", "slots": 1, "focus": ["front_delts"]},
        {"pattern": "vertical_pull", "slots": 1, "focus": ["lats"]},
        {"pattern": "isolation", "slots": 2, "focus": ["side_delts", "biceps", "triceps"]},
        {"pattern": "core", "slots": 1, "focus": ["core"]},
    ],
    "lower": [
        {"pattern": "squat", "slots": 1, "focus": ["quads"]},
        {"pattern": "hinge", "slots": 1, "focus": ["hamstrings"]},
        {"pattern": "lunge", "slots": 1, "focus": ["glutes"]},
        {"pattern": "isolation", "slots": 2, "focus": ["quads", "hamstrings", "calves"]},
        {"pattern": "core", "slots": 1, "focus": ["core"]},
    ],
    "push": [
        {"pattern": "horizontal_push", "slots": 1, "focus": ["chest"]},
        {"pattern": "vertical_push", "slots": 1, "focus": ["front_delts"]},
        {"pattern": "isolation", "slots": 3, "focus": ["chest", "side_delts", "triceps"]},
        {"pattern": "core", "slots": 1, "focus": ["core"]},
    ],
    "pull": [
        {"pattern": "vertical_pull", "slots": 1, "focus": ["lats"]},
        {"pattern": "horizontal_pull", "slots": 1, "focus": ["back"]},
        {"pattern": "isolation", "slots": 3, "focus": ["rear_delts", "biceps", "forearms"]},
    ],
    "legs": [
        {"pattern": "squat", "slots": 1, "focus": ["quads"]},
        {"pattern": "hinge", "slots": 1, "focus": ["hamstrings"]},
        {"pattern": "lunge", "slots": 1, "focus": ["glutes"]},
        {"pattern": "isolation", "slots": 2, "focus": ["hamstrings", "calves"]},
        {"pattern": "core", "slots": 1, "focus": ["core"]},
    ],
}

# Rep ranges and set counts by goal. Strength work sits low and heavy;
# hypertrophy work sits in the classic 8-12 band it actually responds to.
GOAL_SCHEMES: Dict[str, Dict[str, Any]] = {
    "strength": {"compound": (4, 3, 6), "isolation": (3, 8, 12), "rest": 180},
    "hypertrophy": {"compound": (4, 6, 10), "isolation": (3, 10, 15), "rest": 90},
    "fat_loss": {"compound": (3, 8, 12), "isolation": (3, 12, 15), "rest": 60},
    "general": {"compound": (3, 6, 10), "isolation": (3, 10, 15), "rest": 90},
}

EXPERIENCE_WEEKS = {"beginner": 8, "intermediate": 8, "advanced": 6}
# Weekly set caps per muscle, scaled by experience \u2014 the guard rail against
# builder-generated programmes that are absurdly long.
WEEKLY_SET_CAP = {"beginner": 12, "intermediate": 18, "advanced": 24}

# Smallest prescription worth scheduling. A 2-set slot is not a real training
# dose; a muscle is better served by fewer, properly dosed exercises. Slots
# that cannot be funded this way are skipped rather than padded.
MIN_SETS_PER_SLOT = 3


def choose_split(days_per_week: int) -> str:
    """Pick the standard split that matches the days available."""
    if days_per_week <= 2:
        return "full_body_2"
    if days_per_week == 3:
        return "full_body_3"
    if days_per_week == 4:
        return "upper_lower_4"
    if days_per_week == 5:
        return "ppl_5"
    return "ppl_6"


def build_program(
    days_per_week: int = 3,
    experience: str = "beginner",
    goal: str = "strength",
    bodyweight_kg: float = 75.0,
    equipment: Optional[Sequence[str]] = None,
    name: Optional[str] = None,
    weeks: Optional[int] = None,
    split: Optional[str] = None,
) -> Program:
    """Build a complete weekly programme.

    Deterministic: the same arguments always produce the same programme, so it
    can be diffed, version-controlled and unit-tested.
    """
    days_per_week = max(2, min(6, int(days_per_week)))
    experience = experience if experience in EXPERIENCE_WEEKS else "beginner"
    goal = goal if goal in GOAL_SCHEMES else "strength"

    key = split or choose_split(days_per_week)
    if key not in SPLITS:
        raise KeyError(f"unknown split {key!r} \u2014 try one of {', '.join(SPLITS)}")
    template = SPLITS[key]
    scheme = GOAL_SCHEMES[goal]

    equip_filter = _equipment_filter(equipment)
    used: set = set()
    sessions: List[SessionPlan] = []
    weekly_muscle_sets: Dict[str, int] = {}

    for idx, sess in enumerate(template["sessions"]):
        plans = _build_session(
            kind=sess["kind"],
            title=sess["title"],
            day=sess["day"],
            scheme=scheme,
            experience=experience,
            equip_filter=equip_filter,
            used=used,
            bodyweight_kg=bodyweight_kg,
            weekly_muscle_sets=weekly_muscle_sets,
            variant=idx,
        )
        # Rotate which session gets the alternative exercise variant so the
        # same lift does not land on the same day every week.
        sessions.append(plans)

    prog = Program(
        name=name or f"{template['split']} \u00b7 {goal.title()}",
        goal=goal,
        experience=experience,
        days_per_week=len(sessions),
        weeks=weeks or EXPERIENCE_WEEKS[experience],
        split=template["split"],
        bodyweight_kg=float(bodyweight_kg or 75.0),
        sessions=sessions,
        notes=_program_notes(goal, experience, equip_filter, weekly_muscle_sets),
    )
    return prog


def _equipment_filter(equipment: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Always fold bodyweight in; an empty list means "no restriction"."""
    if not equipment:
        return None
    equip = {e.lower() for e in equipment}
    equip.add("bodyweight")
    return sorted(equip)


def _build_session(
    kind: str,
    title: str,
    day: str,
    scheme: Dict[str, Any],
    experience: str,
    equip_filter: Optional[List[str]],
    used: set,
    bodyweight_kg: float,
    weekly_muscle_sets: Dict[str, int],
    variant: int,
) -> SessionPlan:
    """Fill one session's slots with the best available exercises."""
    slots = SESSION_TEMPLATES[kind]
    max_level = {"beginner": "beginner", "intermediate": "intermediate"}.get(
        experience, "advanced"
    )
    cap = WEEKLY_SET_CAP.get(experience, 12)
    chosen: List[PlannedExercise] = []
    focus: List[str] = []

    for slot in slots:
        for i in range(slot["slots"]):
            # Offset the focus list by the session index so that repeated
            # sessions (Push A / Push B) cover different muscles, while every
            # session still targets ONLY the muscles its kind is meant to train.
            # Falling back to `None` here would let an isolation slot grab a
            # leg movement on a pull day \u2014 the exact bug this guards against.
            wanted = (
                slot["focus"][(i + variant) % len(slot["focus"])]
                if slot["focus"]
                else None
            )
            pick = _pick(
                pattern=slot["pattern"],
                muscle=wanted,
                equip_filter=equip_filter,
                max_level=max_level,
                used=used,
                skip=0,
            )
            if pick is None:
                # Nothing fresh left for this slot. Reusing a movement already
                # in the week is the lesser evil \u2014 but only for THIS session,
                # so the rest of the week still searches against `used` and the
                # result is one repeat rather than a cascade of them.
                pick = _pick(
                    pattern=slot["pattern"],
                    muscle=wanted,
                    equip_filter=equip_filter,
                    max_level=max_level,
                    used=used,
                    skip=0,
                    allow_reuse=True,
                )
            if pick is None:
                continue

            sets, rep_low, rep_high = _dose(pick.kind, scheme)
            # Honour the weekly volume cap for the muscle this lift is for.
            #
            # If the cap cannot fund at least MIN_SETS_PER_SLOT working sets,
            # SKIP the slot instead of padding it to the minimum. A "2-set"
            # prescription is not a real dose, and forcing it also breached the
            # cap the builder is supposed to enforce: on a 4-day beginner
            # hypertrophy plan the quad slots were padded to 2 sets each and
            # ended up at 16 weekly sets against a cap of 12.
            muscle = pick.primary
            remaining = cap - weekly_muscle_sets.get(muscle, 0)
            if remaining < MIN_SETS_PER_SLOT:
                continue
            sets = min(sets, remaining)
            weekly_muscle_sets[muscle] = weekly_muscle_sets.get(muscle, 0) + sets

            start = _start_weight(pick.id, bodyweight_kg, experience)
            chosen.append(
                PlannedExercise(
                    exercise_id=pick.id,
                    sets=sets,
                    rep_low=rep_low,
                    rep_high=rep_high,
                    start_weight=start,
                    increment=pick.increment,
                    rest_sec=scheme["rest"] if pick.kind == "compound" else max(45, scheme["rest"] // 2),
                )
            )
            used.add(pick.id)
            for m in [pick.primary] + list(pick.secondary):
                if m not in focus:
                    focus.append(m)

    return SessionPlan(day=day, title=title, focus=focus, exercises=chosen)


def _pick(
    pattern: str,
    muscle: Optional[str],
    equip_filter: Optional[List[str]],
    max_level: str,
    used: set,
    skip: int = 0,
    allow_reuse: bool = False,
):
    """Choose an exercise for a slot, relaxing constraints in a strict order.

    The ordering matters. A slot that names a muscle (e.g. "rear delts on a pull
    day") must never be filled by *any* isolation movement \u2014 that is how a leg
    extension ends up on a pull day. So the muscle constraint is held longest
    and the movement pattern is allowed to relax first:

        1. exact pattern + muscle, not already programmed this week
        2. exact pattern + muscle, even if reused
        3. muscle only, any pattern, fresh
        4. muscle only, any pattern, reused
        5. pattern only, fresh  \u2014 last resort when the muscle has no movement
                                  matching the required equipment
        6. pattern only, reused
    """
    has_muscle = bool(muscle)

    def find(pat: Optional[str], mus: Optional[str], avoid_used: bool):
        return filter_exercises(
            equipment=equip_filter,
            muscle=mus,
            pattern=pat,
            max_level=max_level,
            exclude=used if (avoid_used and not allow_reuse) else None,
        )

    ladder = []
    if has_muscle:
        ladder += [
            (pattern, muscle, True),
            (pattern, muscle, False),
            (None, muscle, True),
            (None, muscle, False),
            (pattern, None, True),
            (pattern, None, False),
        ]
    else:
        ladder += [
            (pattern, None, True),
            (pattern, None, False),
        ]

    for pat, mus, avoid in ladder:
        cands = find(pat, mus, avoid)
        if len(cands) > skip:
            return cands[skip]
        if cands:
            return cands[0]
    return None


def _dose(kind: str, scheme: Dict[str, Any]):
    sets, low, high = scheme[kind]
    return sets, low, high


def _start_weight(exercise_id: str, bodyweight_kg: float, experience: str) -> float:
    from .engine import round_to_increment

    ex = EXERCISES[exercise_id]
    if ex.is_bodyweight:
        return 0.0
    factor = {"beginner": 0.6, "intermediate": 0.8, "advanced": 1.0}.get(experience, 0.6)
    bw = float(bodyweight_kg or 75.0)
    raw = bw * ex.strength_ratio * factor
    snapped = round_to_increment(raw, ex.increment)
    return snapped if snapped > 0 else ex.increment


def _program_notes(
    goal: str,
    experience: str,
    equip_filter: Optional[List[str]],
    weekly_muscle_sets: Dict[str, int],
) -> List[str]:
    notes = [
        f"Built for a {experience} lifter training for {goal.replace('_', ' ')}.",
        "Progression: double progression \u2014 add reps within the range, then add "
        "the smallest increment and reset to the bottom of the range.",
    ]
    if equip_filter:
        notes.append(
            "Filtered to your equipment: " + ", ".join(sorted(equip_filter)) + ". "
            "Bodyweight movements are always available as a fallback."
        )
    else:
        notes.append("No equipment restriction given \u2014 the builder assumed a full gym.")

    covered = [m for m in MAJOR_MUSCLES if weekly_muscle_sets.get(m)]
    missing = [m for m in MAJOR_MUSCLES if not weekly_muscle_sets.get(m)]
    notes.append(f"Weekly coverage: {len(covered)}/{len(MAJOR_MUSCLES)} major muscles.")
    if missing:
        notes.append("Not directly trained this split: " + ", ".join(missing) + ".")
    return notes


def project_overload(program: Program, weeks: int = 4) -> List[Dict[str, Any]]:
    """Project the first `weeks` weeks of the programme.

    Each week applies the double-progression rule to every big compound lift:
    hit the top of the rep range, add one increment. This makes the intended
    trajectory concrete instead of aspirational, and it is what lets the coach
    answer "where will this put me in a month?".
    """
    from .engine import round_to_increment

    out: List[Dict[str, Any]] = []
    for week in range(1, weeks + 1):
        rows = []
        for sess in program.sessions:
            lifts = []
            for pe in sess.exercises:
                ex = EXERCISES[pe.exercise_id]
                if ex.kind != "compound" or ex.increment <= 0:
                    continue
                # Double progression over time: reps climb one at a time from
                # the bottom of the range to the top, THEN the load steps up by
                # one increment and the reps reset. The cycle therefore runs
                # `span + 1` weeks (e.g. 6-10 reps = 5 weeks per increment).
                span = max(1, pe.rep_high - pe.rep_low)
                cycles, offset = divmod(week - 1, span + 1)
                weight = round_to_increment(
                    pe.start_weight + cycles * ex.increment, ex.increment
                )
                reps = pe.rep_low + offset
                lifts.append(
                    {
                        "exercise": pe.exercise_id,
                        "name": ex.name,
                        "sets": pe.sets,
                        "weight": weight,
                        "reps": reps,
                        "e1rm": round(
                            weight * (1 + reps / 30.0), 1
                        ) if weight > 0 else 0.0,
                    }
                )
            rows.append({"session": sess.title, "day": sess.day, "lifts": lifts})
        out.append({"week": week, "sessions": rows})
    return out

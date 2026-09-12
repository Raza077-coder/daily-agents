"""The FORGE exercise library.

A deliberately curated set of common barbell / dumbbell / machine / cable /
bodyweight movements. Each entry carries the metadata the rest of the engine
needs: which muscles it trains, what equipment it needs, whether it is a
compound or isolation, and a conservative starting-load ratio.

`strength_ratio` = approximate load an intermediate lifter moves for 8-10 reps
as a fraction of bodyweight. Used ONLY to choose a first working weight.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .models import Exercise

# Default smallest sensible jump per equipment type (kg).
DEFAULT_INCREMENTS: Dict[str, float] = {
    "barbell": 2.5,
    "dumbbell": 2.0,
    "machine": 5.0,
    "cable": 2.5,
    "kettlebell": 4.0,
    "band": 0.0,
    "bodyweight": 0.0,
}

MUSCLE_GROUPS = [
    "chest",
    "back",
    "lats",
    "front_delts",
    "side_delts",
    "rear_delts",
    "biceps",
    "triceps",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "core",
    "forearms",
    "traps",
]

PATTERNS = [
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "squat",
    "hinge",
    "lunge",
    "isolation",
    "core",
    "carry",
]

# id, name, primary, secondary, equipment, pattern, kind, level, ratio, unilateral
_CATALOG = [
    # ---- Horizontal push -------------------------------------------------
    ("barbell_bench_press", "Barbell Bench Press", "chest", ["triceps", "front_delts"], "barbell", "horizontal_push", "compound", "beginner", 0.65, False),
    ("incline_barbell_press", "Incline Barbell Press", "chest", ["front_delts", "triceps"], "barbell", "horizontal_push", "compound", "intermediate", 0.55, False),
    ("close_grip_bench", "Close-Grip Bench Press", "triceps", ["chest", "front_delts"], "barbell", "horizontal_push", "compound", "intermediate", 0.55, False),
    ("db_bench_press", "Dumbbell Bench Press", "chest", ["triceps", "front_delts"], "dumbbell", "horizontal_push", "compound", "beginner", 0.28, False),
    ("incline_db_press", "Incline Dumbbell Press", "chest", ["front_delts", "triceps"], "dumbbell", "horizontal_push", "compound", "beginner", 0.24, False),
    ("machine_chest_press", "Machine Chest Press", "chest", ["triceps", "front_delts"], "machine", "horizontal_push", "compound", "beginner", 0.5, False),
    ("cable_fly", "Cable Fly", "chest", ["front_delts"], "cable", "isolation", "isolation", "beginner", 0.12, False),
    ("pec_deck", "Pec Deck", "chest", ["front_delts"], "machine", "isolation", "isolation", "beginner", 0.22, False),
    ("pushup", "Push-Up", "chest", ["triceps", "front_delts", "core"], "bodyweight", "horizontal_push", "compound", "beginner", 0.0, False),
    ("dip", "Dip", "chest", ["triceps", "front_delts"], "bodyweight", "horizontal_push", "compound", "intermediate", 0.0, False),
    # ---- Vertical push ---------------------------------------------------
    ("overhead_press", "Overhead Press", "front_delts", ["triceps", "side_delts", "core"], "barbell", "vertical_push", "compound", "beginner", 0.42, False),
    ("db_shoulder_press", "Dumbbell Shoulder Press", "front_delts", ["triceps", "side_delts"], "dumbbell", "vertical_push", "compound", "beginner", 0.20, False),
    ("arnold_press", "Arnold Press", "front_delts", ["side_delts", "triceps"], "dumbbell", "vertical_push", "compound", "intermediate", 0.17, False),
    ("machine_shoulder_press", "Machine Shoulder Press", "front_delts", ["triceps"], "machine", "vertical_push", "compound", "beginner", 0.35, False),
    ("pike_pushup", "Pike Push-Up", "front_delts", ["triceps", "core"], "bodyweight", "vertical_push", "compound", "beginner", 0.0, False),
    # ---- Horizontal pull -------------------------------------------------
    ("barbell_row", "Barbell Row", "back", ["lats", "biceps", "rear_delts", "traps"], "barbell", "horizontal_pull", "compound", "intermediate", 0.60, False),
    ("pendlay_row", "Pendlay Row", "back", ["lats", "biceps", "traps"], "barbell", "horizontal_pull", "compound", "advanced", 0.65, False),
    ("db_row", "One-Arm Dumbbell Row", "back", ["lats", "biceps", "rear_delts"], "dumbbell", "horizontal_pull", "compound", "beginner", 0.28, True),
    ("cable_row", "Seated Cable Row", "back", ["lats", "biceps", "rear_delts"], "cable", "horizontal_pull", "compound", "beginner", 0.55, False),
    ("machine_row", "Machine Row", "back", ["lats", "biceps", "rear_delts"], "machine", "horizontal_pull", "compound", "beginner", 0.5, False),
    ("t_bar_row", "T-Bar Row", "back", ["lats", "biceps", "traps"], "barbell", "horizontal_pull", "compound", "intermediate", 0.55, False),
    ("inverted_row", "Inverted Row", "back", ["lats", "biceps", "core"], "bodyweight", "horizontal_pull", "compound", "beginner", 0.0, False),
    ("face_pull", "Face Pull", "rear_delts", ["traps", "back"], "cable", "horizontal_pull", "isolation", "beginner", 0.15, False),
    # ---- Vertical pull ---------------------------------------------------
    ("pullup", "Pull-Up", "lats", ["back", "biceps", "forearms"], "bodyweight", "vertical_pull", "compound", "intermediate", 0.0, False),
    ("chinup", "Chin-Up", "lats", ["biceps", "back"], "bodyweight", "vertical_pull", "compound", "intermediate", 0.0, False),
    ("lat_pulldown", "Lat Pulldown", "lats", ["back", "biceps"], "cable", "vertical_pull", "compound", "beginner", 0.55, False),
    ("straight_arm_pulldown", "Straight-Arm Pulldown", "lats", ["core"], "cable", "isolation", "isolation", "beginner", 0.20, False),
    # ---- Squat / lunge ---------------------------------------------------
    ("back_squat", "Back Squat", "quads", ["glutes", "hamstrings", "core"], "barbell", "squat", "compound", "beginner", 0.90, False),
    ("front_squat", "Front Squat", "quads", ["glutes", "core"], "barbell", "squat", "compound", "intermediate", 0.70, False),
    ("leg_press", "Leg Press", "quads", ["glutes", "hamstrings"], "machine", "squat", "compound", "beginner", 1.80, False),
    ("hack_squat", "Hack Squat", "quads", ["glutes"], "machine", "squat", "compound", "intermediate", 1.20, False),
    ("goblet_squat", "Goblet Squat", "quads", ["glutes", "core"], "dumbbell", "squat", "compound", "beginner", 0.22, False),
    ("bulgarian_split_squat", "Bulgarian Split Squat", "quads", ["glutes", "hamstrings"], "dumbbell", "lunge", "compound", "intermediate", 0.22, True),
    ("walking_lunge", "Walking Lunge", "quads", ["glutes", "hamstrings"], "dumbbell", "lunge", "compound", "beginner", 0.20, True),
    ("step_up", "Step-Up", "quads", ["glutes"], "dumbbell", "lunge", "compound", "beginner", 0.18, True),
    ("leg_extension", "Leg Extension", "quads", [], "machine", "isolation", "isolation", "beginner", 0.35, False),
    # ---- Hinge -----------------------------------------------------------
    ("deadlift", "Conventional Deadlift", "hamstrings", ["glutes", "back", "traps", "forearms"], "barbell", "hinge", "compound", "intermediate", 1.15, False),
    ("sumo_deadlift", "Sumo Deadlift", "glutes", ["hamstrings", "quads", "back"], "barbell", "hinge", "compound", "intermediate", 1.20, False),
    ("romanian_deadlift", "Romanian Deadlift", "hamstrings", ["glutes", "back"], "barbell", "hinge", "compound", "intermediate", 0.85, False),
    ("hip_thrust", "Barbell Hip Thrust", "glutes", ["hamstrings"], "barbell", "hinge", "compound", "beginner", 1.00, False),
    ("glute_bridge", "Glute Bridge", "glutes", ["hamstrings", "core"], "bodyweight", "hinge", "compound", "beginner", 0.0, False),
    ("back_extension", "Back Extension", "glutes", ["hamstrings", "core"], "bodyweight", "hinge", "compound", "beginner", 0.0, False),
    ("good_morning", "Good Morning", "hamstrings", ["glutes", "back"], "barbell", "hinge", "compound", "advanced", 0.40, False),
    ("leg_curl", "Lying Leg Curl", "hamstrings", [], "machine", "isolation", "isolation", "beginner", 0.30, False),
    ("seated_leg_curl", "Seated Leg Curl", "hamstrings", [], "machine", "isolation", "isolation", "beginner", 0.32, False),
    ("nordic_curl", "Nordic Hamstring Curl", "hamstrings", ["glutes"], "bodyweight", "isolation", "isolation", "advanced", 0.0, False),
    ("kettlebell_swing", "Kettlebell Swing", "glutes", ["hamstrings", "core", "back"], "kettlebell", "hinge", "compound", "beginner", 0.24, False),
    # ---- Shoulders / arms ------------------------------------------------
    ("lateral_raise", "Dumbbell Lateral Raise", "side_delts", [], "dumbbell", "isolation", "isolation", "beginner", 0.08, False),
    ("cable_lateral_raise", "Cable Lateral Raise", "side_delts", [], "cable", "isolation", "isolation", "beginner", 0.06, False),
    ("front_raise", "Dumbbell Front Raise", "front_delts", [], "dumbbell", "isolation", "isolation", "beginner", 0.08, False),
    ("reverse_fly", "Dumbbell Reverse Fly", "rear_delts", ["traps"], "dumbbell", "isolation", "isolation", "beginner", 0.08, False),
    ("shrug", "Barbell Shrug", "traps", ["forearms"], "barbell", "isolation", "isolation", "beginner", 0.50, False),
    ("barbell_curl", "Barbell Curl", "biceps", ["forearms"], "barbell", "isolation", "isolation", "beginner", 0.25, False),
    ("db_curl", "Dumbbell Curl", "biceps", ["forearms"], "dumbbell", "isolation", "isolation", "beginner", 0.12, False),
    ("hammer_curl", "Hammer Curl", "biceps", ["forearms"], "dumbbell", "isolation", "isolation", "beginner", 0.13, False),
    ("preacher_curl", "Preacher Curl", "biceps", [], "barbell", "isolation", "isolation", "beginner", 0.20, False),
    ("cable_curl", "Cable Curl", "biceps", ["forearms"], "cable", "isolation", "isolation", "beginner", 0.20, False),
    ("triceps_pushdown", "Triceps Pushdown", "triceps", [], "cable", "isolation", "isolation", "beginner", 0.25, False),
    ("skullcrusher", "Skullcrusher", "triceps", [], "barbell", "isolation", "isolation", "beginner", 0.20, False),
    ("overhead_triceps_ext", "Overhead Triceps Extension", "triceps", [], "dumbbell", "isolation", "isolation", "beginner", 0.20, False),
    ("bench_dip", "Bench Dip", "triceps", ["chest", "front_delts"], "bodyweight", "isolation", "isolation", "beginner", 0.0, False),
    # ---- Core / carry ----------------------------------------------------
    ("plank", "Plank", "core", [], "bodyweight", "core", "isolation", "beginner", 0.0, False),
    ("hanging_leg_raise", "Hanging Leg Raise", "core", ["forearms"], "bodyweight", "core", "isolation", "intermediate", 0.0, False),
    ("ab_wheel", "Ab Wheel Rollout", "core", ["lats"], "bodyweight", "core", "isolation", "intermediate", 0.0, False),
    ("cable_crunch", "Cable Crunch", "core", [], "cable", "core", "isolation", "beginner", 0.30, False),
    ("russian_twist", "Russian Twist", "core", [], "dumbbell", "core", "isolation", "beginner", 0.06, False),
    ("farmers_carry", "Farmer's Carry", "forearms", ["traps", "core"], "dumbbell", "carry", "compound", "beginner", 0.40, True),
    ("calf_raise", "Standing Calf Raise", "calves", [], "machine", "isolation", "isolation", "beginner", 0.60, False),
    ("seated_calf_raise", "Seated Calf Raise", "calves", [], "machine", "isolation", "isolation", "beginner", 0.50, False),
]


def _build() -> Dict[str, Exercise]:
    out: Dict[str, Exercise] = {}
    for (eid, name, primary, secondary, equip, pattern, kind, level, ratio, uni) in _CATALOG:
        inc = DEFAULT_INCREMENTS.get(equip, 2.5)
        # Lower-body compounds tolerate (and need) bigger jumps.
        if equip == "barbell" and pattern in ("squat", "hinge", "lunge"):
            inc = 5.0
        if equip == "dumbbell" and uni:
            inc = 2.0
        out[eid] = Exercise(
            id=eid,
            name=name,
            primary=primary,
            secondary=list(secondary),
            equipment=equip,
            pattern=pattern,
            kind=kind,
            level=level,
            strength_ratio=ratio,
            increment=inc,
            unilateral=uni,
        )
    return out


EXERCISES: Dict[str, Exercise] = _build()

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


# Common shorthand people actually type. Without these, `forge log bench 60x8`
# is genuinely ambiguous (barbell_bench_press / bench_dip / db_bench_press /
# close_grip_bench all contain "bench") and the CLI refuses to guess.
ALIASES: Dict[str, str] = {
    "bench": "barbell_bench_press",
    "benchpress": "barbell_bench_press",
    "incline": "incline_barbell_press",
    "dbpress": "db_bench_press",
    "squat": "back_squat",
    "backsquat": "back_squat",
    "frontsquat": "front_squat",
    "deadlift": "deadlift",
    "dl": "deadlift",
    "rdl": "romanian_deadlift",
    "romanian": "romanian_deadlift",
    "ohp": "overhead_press",
    "press": "overhead_press",
    "milpress": "overhead_press",
    "row": "barbell_row",
    "barbellrow": "barbell_row",
    "dbrow": "db_row",
    "pullup": "pullup",
    "pullups": "pullup",
    "chinup": "chinup",
    "pulldown": "lat_pulldown",
    "latpull": "lat_pulldown",
    "dip": "dip",
    "dips": "dip",
    "curl": "barbell_curl",
    "bicepcurl": "barbell_curl",
    "hammer": "hammer_curl",
    "pushdown": "triceps_pushdown",
    "skull": "skullcrusher",
    "lateral": "lateral_raise",
    "latraise": "lateral_raise",
    "side raise": "lateral_raise",
    "facepull": "face_pull",
    "reversefly": "reverse_fly",
    "legpress": "leg_press",
    "legcurl": "leg_curl",
    "legext": "leg_extension",
    "hipthrust": "hip_thrust",
    "calf": "calf_raise",
    "calves": "calf_raise",
    "plank": "plank",
    "pushup": "pushup",
    "pushups": "pushup",
    "swing": "kettlebell_swing",
    "shrug": "shrug",
    "split_squat": "bulgarian_split_squat",
    "bulgarian": "bulgarian_split_squat",
    "lunge": "walking_lunge",
}


def get_exercise(exercise_id: str) -> Exercise:
    """Look up an exercise by id, alias, or fuzzy name.

    Resolution order: exact id -> alias -> normalized id -> unique search hit.
    Raises `KeyError` with a helpful message rather than returning None, so a
    typo in the CLI surfaces immediately instead of silently logging nothing.
    """
    if exercise_id in EXERCISES:
        return EXERCISES[exercise_id]
    key = (exercise_id or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in EXERCISES:
        return EXERCISES[key]
    if key in ALIASES:
        return EXERCISES[ALIASES[key]]
    spaced = (exercise_id or "").strip().lower()
    if spaced in ALIASES:
        return EXERCISES[ALIASES[spaced]]
    hits = search_exercises(exercise_id)
    if len(hits) == 1:
        return hits[0]
    if hits:
        names = ", ".join(e.id for e in hits[:5])
        raise KeyError(
            f"ambiguous exercise {exercise_id!r} — did you mean: {names}? "
            "Or use a full id, e.g. barbell_bench_press."
        )
    raise KeyError(f"unknown exercise {exercise_id!r}")


def search_exercises(query: str) -> List[Exercise]:
    """Substring search across id, name, muscle groups and equipment."""
    q = (query or "").strip().lower()
    if not q:
        return []

    def score(e: Exercise) -> int:
        if e.id == q:
            return 0
        if e.name.lower() == q:
            return 1
        if q in e.id:
            return 2
        if q in e.name.lower():
            return 3
        if any(q in m for m in e.focus_muscles):
            return 4
        if q in e.equipment:
            return 5
        return 9

    return sorted(
        (e for e in EXERCISES.values() if score(e) < 9), key=lambda e: (score(e), e.name)
    )


def filter_exercises(
    equipment: Optional[Iterable[str]] = None,
    muscle: Optional[str] = None,
    pattern: Optional[str] = None,
    kind: Optional[str] = None,
    max_level: str = "advanced",
    exclude: Optional[Iterable[str]] = None,
) -> List[Exercise]:
    """Filter the library. `exclude` removes ids already used in a session."""
    banned = set(exclude or ())
    equip_set = {e.lower() for e in equipment} if equipment else None
    ceiling = _LEVEL_ORDER.get(max_level, 2)
    out = []
    for e in EXERCISES.values():
        if e.id in banned:
            continue
        if equip_set is not None and e.equipment not in equip_set:
            continue
        if muscle and muscle not in e.focus_muscles:
            continue
        if pattern and e.pattern != pattern:
            continue
        if kind and e.kind != kind:
            continue
        if _LEVEL_ORDER.get(e.level, 0) > ceiling:
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e.kind != "compound", e.name))


def _focus(self: Exercise) -> List[str]:
    """Primary muscle first, then accessories. Convenient for filtering."""
    return [self.primary] + list(self.secondary)


# Convenience property so `filter_exercises(muscle=...)` can match any muscle
# an exercise touches, not just its primary.
Exercise.focus_muscles = property(_focus)  # type: ignore[attr-defined]


ALL_EQUIPMENT = sorted({e.equipment for e in EXERCISES.values()})
MAJOR_MUSCLES = [
    "chest",
    "back",
    "lats",
    "front_delts",
    "side_delts",
    "rear_delts",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "biceps",
    "triceps",
    "core",
]

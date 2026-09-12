"""FORGE — Workout Coach Agent.

A deterministic, offline strength-training engine: exercise library, workout
logging, double-progression programming, PR detection and volume analytics.

No ML, no network calls, no accounts. Same input always produces the same plan.
"""

from .models import (
    Exercise,
    SetEntry,
    LoggedExercise,
    Workout,
    Program,
    SessionPlan,
    epley_e1rm,
)
from .exercises import EXERCISES, get_exercise, search_exercises, filter_exercises
from .storage import Store
from .engine import ForgeEngine
from .program import build_program, SPLITS, project_overload, choose_split
from .analytics import (
    volume_by_muscle,
    weekly_tonnage,
    balance_report,
    week_streak,
    muscle_coverage,
    summary,
)
from .coach import Coach

__version__ = "1.0.0"

__all__ = [
    "Exercise",
    "SetEntry",
    "LoggedExercise",
    "Workout",
    "Program",
    "SessionPlan",
    "epley_e1rm",
    "EXERCISES",
    "get_exercise",
    "search_exercises",
    "filter_exercises",
    "Store",
    "ForgeEngine",
    "build_program",
    "SPLITS",
    "project_overload",
    "choose_split",
    "volume_by_muscle",
    "weekly_tonnage",
    "balance_report",
    "week_streak",
    "muscle_coverage",
    "summary",
    "Coach",
    "__version__",
]

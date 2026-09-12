"""FORGE command line interface.

    forge init --bodyweight 80 --experience intermediate
    forge program --days 4 --goal hypertrophy
    forge today
    forge log bench 60x8x3
    forge progress bench
    forge pr
    forge report
    forge library --muscle chest

Exit codes: 0 success, 1 user error (bad input, nothing logged), 2 unexpected
error. That makes `forge` safe to chain in scripts and CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import __version__
from .analytics import summary
from .coach import Coach
from .engine import ForgeEngine
from .exercises import ALL_EQUIPMENT, EXERCISES, filter_exercises, search_exercises
from .models import parse_date
from .program import SPLITS, build_program, choose_split, project_overload
from .storage import Store, default_store_path

EXIT_OK = 0
EXIT_USER = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="forge",
        description="FORGE — deterministic strength & workout coach. Offline, no accounts.",
    )
    p.add_argument("--version", action="version", version=f"FORGE {__version__}")
    p.add_argument(
        "--data",
        metavar="PATH",
        help=f"training file (default: {default_store_path()})",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")

    sub = p.add_subparsers(dest="command", metavar="<command>")

    # -- init ---------------------------------------------------------------
    si = sub.add_parser("init", help="create or update your athlete profile")
    si.add_argument("--bodyweight", type=float, default=0.0, help="bodyweight in kg")
    si.add_argument(
        "--experience",
        choices=["beginner", "intermediate", "advanced"],
        default="beginner",
    )
    si.add_argument(
        "--goal",
        choices=["strength", "hypertrophy", "fat_loss", "general"],
        default="strength",
    )
    si.add_argument(
        "--equipment",
        default="",
        help="comma-separated: " + ", ".join(ALL_EQUIPMENT) + " (blank = full gym)",
    )
    si.add_argument("--name", default="", help="your name (optional)")
    si.add_argument("--force", action="store_true", help="wipe existing history")

    # -- program ------------------------------------------------------------
    sp = sub.add_parser("program", help="build and save a weekly programme")
    sp.add_argument("--days", type=int, default=0, help="training days per week (2-6)")
    sp.add_argument(
        "--experience", choices=["beginner", "intermediate", "advanced"], default=None
    )
    sp.add_argument(
        "--goal",
        choices=["strength", "hypertrophy", "fat_loss", "general"],
        default=None,
    )
    sp.add_argument("--bodyweight", type=float, default=None)
    sp.add_argument("--weeks", type=int, default=None, help="block length")
    sp.add_argument("--split", choices=sorted(SPLITS), default=None)
    sp.add_argument("--show", action="store_true", help="print the saved programme")
    sp.add_argument(
        "--preview",
        action="store_true",
        help="build without saving (useful for comparing splits)",
    )
    sp.add_argument(
        "--project",
        type=int,
        default=0,
        metavar="N",
        help="also project the first N weeks of overload",
    )

    # -- today --------------------------------------------------------------
    st = sub.add_parser("today", help="today's session with per-lift advice")
    st.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")

    # -- log ----------------------------------------------------------------
    sl = sub.add_parser("log", help="log an exercise")
    sl.add_argument("exercise", help="exercise id or name, e.g. bench, back_squat")
    sl.add_argument(
        "sets",
        help="set spec: 60x8, 60x8x3, 60x8,60x8,60x6, bw x10, bw+10 x5, 60x8@8",
    )
    sl.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    sl.add_argument("--session", default="", help="session label, e.g. 'Upper A'")
    sl.add_argument("--duration", type=int, default=None, help="minutes")
    sl.add_argument("--notes", default="", help="free-text note")

    # -- session (bulk log) -------------------------------------------------
    ss = sub.add_parser("session", help="log a whole session at once")
    ss.add_argument(
        "entries",
        nargs="+",
        metavar="EXERCISE=SETS",
        help="e.g. bench=60x8x3 row=50x10x3 squat=80x5x5",
    )
    ss.add_argument("--date", default=None)
    ss.add_argument("--label", default="", help="session name")
    ss.add_argument("--duration", type=int, default=None)

    # -- progress -----------------------------------------------------------
    sg = sub.add_parser("progress", help="per-lift progress timeline")
    sg.add_argument("exercise")
    sg.add_argument("--limit", type=int, default=0, help="only the last N sessions")

    # -- pr -----------------------------------------------------------------
    spr = sub.add_parser("pr", help="personal records")
    spr.add_argument("exercise", nargs="?", default=None, help="optional single lift")

    # -- report -------------------------------------------------------------
    sr = sub.add_parser("report", help="training report: volume, balance, streak")
    sr.add_argument("--days", type=int, default=28, help="window in days")
    sr.add_argument("--date", default=None)

    # -- library ------------------------------------------------------------
    slib = sub.add_parser("library", help="search the exercise library")
    slib.add_argument("query", nargs="?", default="", help="search term")
    slib.add_argument("--muscle", default=None)
    slib.add_argument("--equipment", default=None)
    slib.add_argument("--pattern", default=None)
    slib.add_argument("--limit", type=int, default=0)

    # -- history ------------------------------------------------------------
    sh = sub.add_parser("history", help="list logged workouts")
    sh.add_argument("--limit", type=int, default=20)

    # -- undo ---------------------------------------------------------------
    su = sub.add_parser("undo", help="delete every workout on a date")
    su.add_argument("date", nargs="?", default="today", help="YYYY-MM-DD or 'today'")
    su.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    # -- demo ---------------------------------------------------------------
    sd = sub.add_parser("demo", help="seed a realistic 3-week history and report")
    sd.add_argument("--force", action="store_true", help="overwrite existing history")

    # Let `--json` work AFTER the subcommand too (`forge report --json`), not
    # only before it. Must run here, at the END of parser construction, once
    # every subparser exists. `default=SUPPRESS` keeps a top-level
    # `forge --json report` from being clobbered back to False.
    for _sub in sub.choices.values():
        _sub.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="emit JSON instead of text",
        )

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK

    store = Store(args.data)
    engine = ForgeEngine(store)
    coach = Coach()
    as_json = bool(getattr(args, "json", False))

    try:
        handler = globals().get(f"cmd_{args.command}")
        if handler is None:
            parser.print_help()
            return EXIT_USER
        return handler(args, engine, coach, as_json)
    except KeyError as exc:
        # KeyError's str() adds its own quotes around the message — unwrap it so
        # `forge log bogus 60x8` prints `forge: unknown exercise 'bogus'`
        # instead of the doubled-up `forge: "unknown exercise 'bogus'"`.
        _fail(_unquote(exc), as_json)
        return EXIT_USER
    except ValueError as exc:
        _fail(str(exc), as_json)
        return EXIT_USER
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the CLI
        _fail(f"unexpected error: {exc.__class__.__name__}: {exc}", as_json)
        return EXIT_ERROR


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------


def cmd_init(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    equipment = [e.strip() for e in (args.equipment or "").split(",") if e.strip()]
    bad = [e for e in equipment if e not in ALL_EQUIPMENT]
    if bad:
        raise ValueError(
            f"unknown equipment {', '.join(bad)} — valid options: {', '.join(ALL_EQUIPMENT)}"
        )
    profile = engine.init_profile(
        bodyweight_kg=args.bodyweight,
        experience=args.experience,
        goal=args.goal,
        equipment=equipment,
        name=args.name,
        reset=args.force,
    )
    if as_json:
        _emit({"profile": profile, "data_file": str(engine.store.path)}, as_json)
        return EXIT_OK

    print(coach.greet())
    print()
    print(f"Profile saved to {engine.store.path}")
    if profile.get("bodyweight_kg"):
        print(f"  Bodyweight: {profile['bodyweight_kg']:g} kg")
    print(f"  Experience: {profile.get('experience')}")
    print(f"  Goal:       {str(profile.get('goal')).replace('_', ' ')}")
    print(
        "  Equipment:  "
        + (", ".join(profile.get("equipment") or []) or "full gym (no restriction)")
    )
    print()
    print(coach.msg("init") or "Profile saved.")
    return EXIT_OK


def cmd_program(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    profile = engine.profile
    bodyweight = args.bodyweight if args.bodyweight is not None else (
        profile.get("bodyweight_kg") or 75.0
    )
    experience = args.experience or profile.get("experience") or "beginner"
    goal = args.goal or profile.get("goal") or "strength"
    days = args.days or 0
    if not days:
        try:
            days = int(profile.get("days_per_week") or 0)
        except (TypeError, ValueError):
            days = 0
    if not days:
        days = 3

    program = build_program(
        days_per_week=days,
        experience=experience,
        goal=goal,
        bodyweight_kg=float(bodyweight or 75.0),
        equipment=engine.available_equipment() or None,
        weeks=args.weeks,
        split=args.split,
    )

    if not args.preview:
        engine.store.set_program(program)

    if as_json:
        payload: Dict[str, Any] = {"program": program.to_dict()}
        if args.project:
            payload["projection"] = project_overload(program, args.project)
        _emit(payload, as_json)
        return EXIT_OK

    print(coach.describe_program(program))
    if args.project:
        print()
        print(coach.describe_projection(project_overload(program, args.project)))
    if args.preview:
        print()
        print("(preview only — nothing saved. Drop --preview to save it.)")
    else:
        print()
        print("Programme saved. Run `forge today` for your next session.")
    return EXIT_OK


def cmd_today(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    day = parse_date(args.date) if args.date else date.today()
    data = engine.today(day)
    if as_json:
        _emit(data, as_json)
        return EXIT_OK
    print(coach.describe_today(data))
    return EXIT_OK


def cmd_log(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    result = engine.log(
        args.exercise,
        args.sets,
        day=args.date,
        session=args.session,
        notes=args.notes,
        duration_min=args.duration,
    )
    if as_json:
        _emit(result, as_json)
        return EXIT_OK

    print(
        f"Logged {result['name']} on {result['date']} — "
        f"{len(result['sets'])} sets, volume {result['volume']:g}kg"
    )
    pr_line = coach.describe_prs(result["prs"])
    if pr_line:
        print()
        print(pr_line)

    advice = engine.next_prescription(result["exercise"])
    print()
    print("Next session:")
    print("  " + coach.describe_prescription(advice))
    return EXIT_OK


def cmd_session(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    entries: Dict[str, str] = {}
    for item in args.entries:
        if "=" not in item:
            raise ValueError(f"expected EXERCISE=SETS, got {item!r}")
        key, _, spec = item.partition("=")
        entries[key.strip()] = spec.strip()
    if not entries:
        raise ValueError("no exercises given")

    result = engine.log_session(day=args.date, session=args.label, entries=entries)
    if as_json:
        _emit(result, as_json)
        return EXIT_OK
    print(coach.describe_session(result))
    return EXIT_OK


def cmd_progress(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    data = engine.progress(args.exercise)
    if args.limit:
        data["timeline"] = data["timeline"][-args.limit:]
    if as_json:
        _emit(data, as_json)
        return EXIT_OK
    print(coach.describe_progress(data))
    if data.get("timeline"):
        print()
        print("Timeline")
        for row in data["timeline"][-8:]:
            print(
                f"  {row['date']}  {len(row['sets'])} sets  "
                f"top {row['top_weight']:g}kg x {row['top_reps']}  "
                f"e1RM {row['best_e1rm']:g}kg  vol {row['volume']:g}kg"
            )
    return EXIT_OK


def cmd_pr(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    rows = engine.personal_records(args.exercise)
    if not rows:
        if as_json:
            _emit({"records": []}, as_json)
            return EXIT_OK
        print("No personal records yet — log a session first.")
        return EXIT_OK
    if as_json:
        _emit({"records": rows}, as_json)
        return EXIT_OK
    print("Personal records")
    print("=" * 16)
    for r in rows:
        trend = r["trend"]["direction"]
        arrow = {"improving": "up", "flat": "flat", "declining": "down", "new": "new"}[trend]
        print(
            f"  {r['name']:<28} e1RM {r['best_e1rm']:>6g}kg   "
            f"heaviest {r['heaviest_weight']:g}kg x {r['heaviest_reps']}   "
            f"{r['sessions']} sessions   {arrow}"
        )
    return EXIT_OK


def cmd_report(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    day = parse_date(args.date) if args.date else date.today()
    data = summary(engine.store.workouts, day, args.days)
    data["profile"] = engine.profile
    data["program"] = (
        engine.store.get_program().name if engine.store.get_program() else None
    )
    if as_json:
        _emit(data, as_json)
        return EXIT_OK
    print(coach.describe_summary(data))
    return EXIT_OK


def cmd_library(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    if args.query:
        rows = search_exercises(args.query)
    else:
        rows = filter_exercises(
            equipment=[args.equipment] if args.equipment else None,
            muscle=args.muscle,
            pattern=args.pattern,
        )
    if args.equipment and args.query:
        rows = [e for e in rows if e.equipment == args.equipment]
    if args.muscle and args.query:
        rows = [e for e in rows if args.muscle in e.focus_muscles]
    if args.pattern and args.query:
        rows = [e for e in rows if e.pattern == args.pattern]
    if args.limit:
        rows = rows[: args.limit]

    if as_json:
        _emit({"count": len(rows), "exercises": [e.to_dict() for e in rows]}, as_json)
        return EXIT_OK
    if not rows:
        print("No matching exercises.")
        return EXIT_OK
    print(f"{len(rows)} exercise(s)")
    print("=" * 18)
    for e in rows[:60]:
        muscles = ", ".join(e.focus_muscles)
        print(
            f"  {e.id:<26} {e.name:<30} {e.kind:<9} {e.equipment:<10} "
            f"inc {e.increment:g}kg  [{muscles}]"
        )
    return EXIT_OK


def cmd_history(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    workouts = engine.store.workouts
    rows = workouts[-args.limit:] if args.limit else workouts
    if as_json:
        _emit({"count": len(rows), "workouts": [w.to_dict() for w in rows]}, as_json)
        return EXIT_OK
    if not rows:
        print("No workouts logged yet.")
        return EXIT_OK
    print(f"{len(workouts)} workout(s) — showing {len(rows)}")
    print("=" * 34)
    for w in rows:
        names = ", ".join(
            EXERCISES[e.exercise_id].name if e.exercise_id in EXERCISES else e.exercise_id
            for e in w.entries
        )
        label = w.session or "session"
        print(
            f"  {w.date}  {label:<14} {w.total_sets:>2} sets  "
            f"{w.volume:>9g}kg  {names}"
        )
    return EXIT_OK


def cmd_undo(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    raw = (args.date or "today").strip().lower()
    day = date.today() if raw == "today" else parse_date(raw)
    existing = [w for w in engine.store.workouts if w.date == day.isoformat()]
    if not existing:
        print(f"Nothing logged on {day.isoformat()}.")
        return EXIT_OK
    if not args.yes:
        print(
            f"About to delete {len(existing)} session(s) on {day.isoformat()}. "
            "Re-run with --yes to confirm."
        )
        return EXIT_USER
    removed = engine.remove_day(day)
    if as_json:
        _emit({"date": day.isoformat(), "removed": removed}, as_json)
        return EXIT_OK
    print(f"Deleted sessions on {day.isoformat()}." if removed else "Nothing to delete.")
    return EXIT_OK


DEMO_PLAN = [
    # (days ago, session, {exercise: spec})
    (
        20, "Upper A",
        {"barbell_bench_press": "60x8x3", "barbell_row": "50x10x3",
         "overhead_press": "35x8x3", "lat_pulldown": "50x10x3"},
    ),
    (
        18, "Lower A",
        {"back_squat": "70x8x3", "romanian_deadlift": "60x8x3",
         "leg_curl": "30x12x3", "calf_raise": "50x15x3"},
    ),
    (
        15, "Upper A",
        {"barbell_bench_press": "62.5x8x3", "barbell_row": "52.5x10x3",
         "overhead_press": "37.5x8x3", "lat_pulldown": "52.5x10x3"},
    ),
    (
        13, "Lower A",
        {"back_squat": "75x8x3", "romanian_deadlift": "62.5x8x3",
         "leg_curl": "32.5x12x3", "calf_raise": "52.5x15x3"},
    ),
    (
        8, "Upper A",
        {"barbell_bench_press": "65x10x3", "barbell_row": "55x10x3",
         "overhead_press": "40x8x3", "lat_pulldown": "55x10x3"},
    ),
    (
        6, "Lower A",
        {"back_squat": "80x8x3", "romanian_deadlift": "65x8x3",
         "leg_curl": "35x12x3", "calf_raise": "55x15x3"},
    ),
    (
        3, "Upper A",
        {"barbell_bench_press": "67.5x8x3", "barbell_row": "57.5x10x3",
         "overhead_press": "40x10x3", "lat_pulldown": "57.5x10x3"},
    ),
    (
        1, "Lower A",
        {"back_squat": "82.5x8x3", "romanian_deadlift": "67.5x8x3",
         "leg_curl": "37.5x12x3", "calf_raise": "57.5x15x3"},
    ),
]


def cmd_demo(args, engine: ForgeEngine, coach: Coach, as_json: bool) -> int:
    """Seed a realistic history so the report has something to say."""
    if engine.store.workouts and not args.force:
        raise ValueError(
            f"{engine.store.path} already has workouts — pass --force to overwrite"
        )
    if args.force:
        engine.store.reset()

    engine.init_profile(
        bodyweight_kg=80.0,
        experience="intermediate",
        goal="strength",
        equipment=[],
        reset=False,
    )
    engine.store.set_program(
        build_program(
            days_per_week=4,
            experience="intermediate",
            goal="strength",
            bodyweight_kg=80.0,
        )
    )

    today = date.today()
    logged = 0
    for days_ago, session, entries in DEMO_PLAN:
        day = today - timedelta(days=days_ago)
        engine.log_session(day=day, session=session, entries=entries)
        logged += len(entries)

    data = summary(engine.store.workouts, today, 28)
    if as_json:
        _emit({"seeded": logged, "summary": data}, as_json)
        return EXIT_OK

    print(coach.greet())
    print()
    print(f"Seeded {logged} exercises across {len(DEMO_PLAN)} sessions into {engine.store.path}")
    print()
    print(coach.describe_summary(data))
    return EXIT_OK


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


def _unquote(exc: BaseException) -> str:
    """KeyError stringifies with surrounding quotes; strip them for display."""
    text = str(exc)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _emit(payload: Any, as_json: bool) -> None:
    """Print JSON, converting sets to plain lists so it always serialises."""
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=_default))
    else:
        print(payload)


def _default(obj: Any) -> Any:
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return str(obj)


def _fail(message: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, indent=2))
    else:
        print(f"forge: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

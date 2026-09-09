"""HABITOS command-line interface.

Commands: add, list, log, unlog, status, detail, report, trend, delete,
archive, demo. All deterministic and offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .engine import HabitEngine


def _fmt_habit(h) -> str:
    bar_len = 14
    filled = int(round(h.completion_pct / 100 * bar_len)) if h.week_possible else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    status_icon = {"on_track": "🟢", "at_risk": "🟡", "off_track": "🔴"}.get(h.status, "⚪")
    return (f"{status_icon} {h.name:<22} {bar} {h.completion_pct:5.0f}%  "
            f"week {h.week_done}/{h.target_per_week}  streak {h.current_streak}d  best {h.longest_streak}d")


def cmd_add(eng: HabitEngine, args) -> int:
    h = eng.add_habit(args.name, category=args.category,
                      target_per_week=args.target, frequency=args.frequency,
                      color=args.color)
    print(f"Added habit  '{h.name}'  [{h.habit_id}]  target {h.target_per_week}/week")
    return 0


def cmd_list(eng: HabitEngine, args) -> int:
    habits = eng.list_habits(include_archived=args.all)
    if not habits:
        print("No habits yet. Add one:  habitos add \"Drink water\" --frequency daily")
        return 0
    print(f"{'HABIT':<46} PROGRESS  WEEKLY  STREAK")
    for h in habits:
        if h.archived and not args.all:
            continue
        print(_fmt_habit(h))
    s = eng.summary()
    print(f"\n{s['active_habits']} active · {s['on_track']} on track · "
          f"{s['at_risk']} at risk · {s['off_track']} off track · "
          f"{s['total_logs']} total check-ins")
    return 0


def cmd_log(eng: HabitEngine, args) -> int:
    try:
        e = eng.log(args.habit, date=args.date, note=args.note)
    except (KeyError, ValueError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    h = eng.get_habit(e.habit_id)
    eng.refresh_metrics([h])
    print(f"Logged ✓  '{h.name}'  on {e.date}  (streak now {h.current_streak}d)")
    return 0


def cmd_unlog(eng: HabitEngine, args) -> int:
    if eng.unlog(args.habit, date=args.date):
        print(f"Removed check-in for '{args.habit}' on {args.date or eng.today.isoformat()}")
        return 0
    print(f"No check-in found for '{args.habit}' on {args.date or eng.today.isoformat()}")
    return 1


def cmd_status(eng: HabitEngine, args) -> int:
    habits = eng.list_habits(include_archived=False)
    if not habits:
        print("No habits yet.")
        return 0
    monday = None
    for h in habits:
        d = eng.habit_detail(h.habit_id)
        if monday is None:
            monday = d["week_monday"]
        row = d["last_7_days"]
        cells = []
        for day in row:
            lbl = "Today" if day["date"] == eng.today.isoformat() else day["date"][5:]
            cells.append(f"{lbl}:{'✓' if day['done'] else '–'}")
        print(f"{d['name']}  [{d['status'].upper()}]  streak {d['current_streak']}d")
        print("   " + "  ".join(cells))
    print(f"\nWeek started {monday}")
    return 0


def cmd_report(eng: HabitEngine, args) -> int:
    if args.json:
        print(json.dumps(eng.weekly_report(format="json"), indent=2, default=str))
    else:
        print(eng.weekly_report(format="text"))
    return 0


def cmd_detail(eng: HabitEngine, args) -> int:
    d = eng.habit_detail(args.habit)
    if d is None:
        print(f"Unknown habit '{args.habit}'", file=sys.stderr)
        return 1
    print(json.dumps(d, indent=2, default=str))
    return 0


def cmd_trend(eng: HabitEngine, args) -> int:
    t = eng.habit_trend(args.habit, weeks=args.weeks)
    if t is None:
        print(f"Unknown habit '{args.habit}'", file=sys.stderr)
        return 1
    maxc = max([b["count"] for b in t["buckets"]] + [1])
    print(f"Trend for '{t['habit']['name']}' — last {len(t['buckets'])} weeks")
    for b in t["buckets"]:
        bar = "█" * b["count"] + "░" * (maxc - b["count"])
        label = b["week_start"][5:]
        print(f"  {label}  {bar} {b['count']}x")
    return 0


def cmd_delete(eng: HabitEngine, args) -> int:
    if eng.delete_habit(args.habit):
        print(f"Deleted habit '{args.habit}' and its history")
        return 0
    print(f"Unknown habit '{args.habit}'", file=sys.stderr)
    return 1


def cmd_archive(eng: HabitEngine, args) -> int:
    h = eng.archive_habit(args.habit, archived=not args.unarchive)
    if h is None:
        print(f"Unknown habit '{args.habit}'", file=sys.stderr)
        return 1
    print(f"{'Unarchived' if args.unarchive else 'Archived'} habit '{h.name}'")
    return 0


def cmd_demo(eng: HabitEngine, args) -> int:
    """Seed a realistic 3-week demo dataset."""
    import datetime as dt
    import random
    eng.delete_habit("meditate")
    eng.delete_habit("read")
    eng.delete_habit("exercise")
    eng.delete_habit("code")
    habits = [
        ("Meditate", "mindfulness", 7),
        ("Read 20 pages", "learning", 5),
        ("Workout", "fitness", 4),
        ("Ship code", "productivity", 3),
    ]
    created = []
    for name, cat, target in habits:
        h = eng.add_habit(name, category=cat, target_per_week=target)
        created.append(h.habit_id)
    today = eng.today
    rng = random.Random(42)
    for hid, cat, target in zip(created, ["mindfulness", "learning", "fitness", "productivity"], [7, 5, 4, 3]):
        for back in range(20, -1, -1):
            d = today - dt.timedelta(days=back)
            weekday_ok = True
            if cat == "fitness" and d.weekday() >= 5 and rng.random() < 0.7:
                weekday_ok = False
            if rng.random() < (target / 7.0) * 0.9 and weekday_ok:
                eng.log(hid, date=d.isoformat(), note="demo")
    print("Demo dataset seeded (3 weeks of history, 4 habits).")
    print(eng.weekly_report(format="text"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="habitos", description="HABITOS — Habit Tracker Agent")
    p.add_argument("--data", default=None, help="path to JSON data file (default: temp)")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="add a new habit")
    a.add_argument("name")
    a.add_argument("--category", default="general",
                   choices=["health", "productivity", "learning", "fitness", "mindfulness", "general"])
    a.add_argument("--target", type=int, default=None, help="target days per week (1-7)")
    a.add_argument("--frequency", default="", choices=["", "daily", "weekdays", "weekly", "weekends"])
    a.add_argument("--color", default="")

    sub.add_parser("list", help="list habits").add_argument("--all", action="store_true")

    a = sub.add_parser("log", help="mark habit done")
    a.add_argument("habit")
    a.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    a.add_argument("--note", default="")

    a = sub.add_parser("unlog", help="remove a check-in")
    a.add_argument("habit")
    a.add_argument("--date", default=None)

    sub.add_parser("status", help="show week status board")
    sub.add_parser("report", help="weekly report").add_argument("--json", action="store_true")

    a = sub.add_parser("detail", help="habit detail as JSON")
    a.add_argument("habit")
    a = sub.add_parser("trend", help="weekly trend chart")
    a.add_argument("habit")
    a.add_argument("--weeks", type=int, default=6)
    a = sub.add_parser("delete", help="delete habit + history")
    a.add_argument("habit")
    a = sub.add_parser("archive", help="archive/unarchive habit")
    a.add_argument("habit")
    a.add_argument("--unarchive", action="store_true")
    sub.add_parser("demo", help="seed demo dataset")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    eng = HabitEngine(data_path=args.data)
    handler = {
        "add": cmd_add, "list": cmd_list, "log": cmd_log, "unlog": cmd_unlog,
        "status": cmd_status, "report": cmd_report, "detail": cmd_detail,
        "trend": cmd_trend, "delete": cmd_delete, "archive": cmd_archive,
        "demo": cmd_demo,
    }[args.command]
    return handler(eng, args)


if __name__ == "__main__":
    raise SystemExit(main())

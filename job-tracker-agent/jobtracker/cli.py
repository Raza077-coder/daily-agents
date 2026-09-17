"""Command-line interface for JOBFLOW.

Exit codes are part of the contract, so scripts can rely on them:

====  ==========================================================
  0   success
  1   a deliberate JobFlow error (bad input, unknown id, etc.)
  2   an unexpected internal error (a bug — please report it)
====  ==========================================================
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import TAGLINE, __version__, report
from .engine import JobFlowEngine
from .models import (
    INTERVIEW_KINDS,
    SOURCES,
    SOURCE_LABELS,
    STAGES,
    STATUS_LABELS,
    STATUSES,
    WORK_MODES,
    JobFlowError,
    ValidationError,
    format_range,
)
from .store import default_vault_path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BUG = 2


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobflow",
        description=TAGLINE,
        epilog=(
            "Every command accepts --today YYYY-MM-DD so runs are reproducible, "
            "and --vault PATH to point at a specific vault file."
        ),
    )
    parser.add_argument("--version", action="version", version=f"jobflow {__version__}")
    parser.add_argument(
        "--vault",
        default=None,
        help=f"vault file (default: {default_vault_path()})",
    )
    parser.add_argument(
        "--today",
        default=None,
        help="treat this date as today (YYYY-MM-DD); defaults to the real date",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON instead of text"
    )

    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    # -- add ---------------------------------------------------------------
    add = subs.add_parser("add", help="add an application (or a wishlist item)")
    add.add_argument("company")
    add.add_argument("role")
    add.add_argument("--status", default="wishlist", choices=list(STATUSES))
    add.add_argument("--applied-on", default=None, help="date you submitted it (YYYY-MM-DD)")
    add.add_argument("--location", default="")
    add.add_argument("--work-mode", default="unspecified", choices=list(WORK_MODES))
    add.add_argument("--source", default="other", choices=list(SOURCES))
    add.add_argument("--url", default="")
    add.add_argument("--currency", default="USD")
    add.add_argument("--salary-min", default=None)
    add.add_argument("--salary-max", default=None)
    add.add_argument("--priority", default=None, help="1-5 enthusiasm score (default 3)")
    add.add_argument("--deadline", default=None, help="application deadline (YYYY-MM-DD)")
    add.add_argument("--contact", default="")
    add.add_argument("--tags", default="", help="comma-separated tags")
    add.add_argument("--id", default=None, help="override the generated id")

    # -- log-apply ---------------------------------------------------------
    apply_cmd = subs.add_parser("log-apply", help="record that you submitted an application")
    apply_cmd.add_argument("app_id")
    apply_cmd.add_argument("--on", default=None, help="submission date (defaults to today)")
    apply_cmd.add_argument("--note", default="")

    # -- move --------------------------------------------------------------
    move = subs.add_parser("move", help="change status (refuses to skip stages)")
    move.add_argument("app_id")
    move.add_argument("status", choices=list(STATUSES))
    move.add_argument("--note", default="")
    move.add_argument("--force", action="store_true", help="allow a stage-skipping move")

    # -- stage (backfill) --------------------------------------------------
    stage = subs.add_parser("stage", help="backfill a stage that already happened")
    stage.add_argument("app_id")
    stage.add_argument("stage", choices=[s for s in STAGES if s != "applied"])
    stage.add_argument("--on", default=None, help="when it happened (defaults to today)")
    stage.add_argument("--note", default="")

    # -- reopen / drop -----------------------------------------------------
    reopen = subs.add_parser("reopen", help="reopen a closed application")
    reopen.add_argument("app_id")
    reopen.add_argument("--note", default="")

    drop = subs.add_parser("drop", help="delete an application permanently")
    drop.add_argument("app_id")

    # -- interview ---------------------------------------------------------
    interview = subs.add_parser("interview", help="schedule or complete an interview")
    interview.add_argument("app_id")
    interview.add_argument("--on", default=None, help="interview date (defaults to today)")
    interview.add_argument("--kind", default="other", choices=list(INTERVIEW_KINDS))
    interview.add_argument("--note", default="")
    interview.add_argument("--done", action="store_true", help="mark it as completed")

    # -- note / followup / next -------------------------------------------
    note = subs.add_parser("note", help="attach a note")
    note.add_argument("app_id")
    note.add_argument("text")
    note.add_argument("--on", default=None)

    follow = subs.add_parser("followup", help="record that you chased the employer")
    follow.add_argument("app_id")
    follow.add_argument("--note", default="")
    follow.add_argument("--channel", default="", help="email, linkedin, phone…")

    nxt = subs.add_parser("next", help="set the next concrete action")
    nxt.add_argument("app_id")
    nxt.add_argument("action")
    nxt.add_argument("--on", default=None)

    # -- edit --------------------------------------------------------------
    edit = subs.add_parser("edit", help="update fields on an application")
    edit.add_argument("app_id")
    edit.add_argument("--company")
    edit.add_argument("--role")
    edit.add_argument("--location")
    edit.add_argument("--work-mode", choices=list(WORK_MODES))
    edit.add_argument("--source", choices=list(SOURCES))
    edit.add_argument("--url")
    edit.add_argument("--currency")
    edit.add_argument("--salary-min")
    edit.add_argument("--salary-max")
    edit.add_argument("--priority")
    edit.add_argument("--deadline")
    edit.add_argument("--contact")
    edit.add_argument("--next-action")
    edit.add_argument("--tags")

    # -- reads -------------------------------------------------------------
    listing = subs.add_parser("list", help="list applications")
    listing.add_argument("--status", action="append", choices=list(STATUSES))
    listing.add_argument("--active", action="store_true", help="only open applications")
    listing.add_argument("--tag")
    listing.add_argument("--company")

    subs.add_parser("board", help="grouped board view")

    show = subs.add_parser("show", help="full detail for one application")
    show.add_argument("app_id")

    subs.add_parser("status", help="daily digest: funnel, plan, throughput")
    subs.add_parser("plan", help="ranked to-do list with reasons")
    subs.add_parser("report", help="the full report")
    subs.add_parser("md", help="Markdown report for pasting elsewhere")
    subs.add_parser("export", help="machine-readable bundle (JSON)")
    subs.add_parser("vocab", help="statuses, stages, rules and thresholds")
    subs.add_parser("init", help="create an empty vault")

    up = subs.add_parser("upcoming", help="interviews in the next N days")
    up.add_argument("--days", type=int, default=14)

    why = subs.add_parser("why", help="explain why a rule fired")
    why.add_argument("rule")
    why.add_argument("--app", default=None)

    demo = subs.add_parser("demo", help="seed a realistic 90-day demo vault")
    demo.add_argument("--force", action="store_true", help="replace existing contents")

    # --json is declared on the top-level parser, so argparse only accepts it
    # *before* the subcommand (`jobflow --json list`).  Declaring it on every
    # subparser as well makes the natural `jobflow list --json` work, and
    # suppresses the attribute so it cannot clobber the global value with
    # its default.  Without this, `jobflow list --json` exited 2.
    for sub in subs.choices.values():
        sub.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="emit machine-readable JSON instead of text",
        )

    return parser


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def _emit(args: argparse.Namespace, text: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Print either text or JSON depending on ``--json``."""

    if getattr(args, "json", False):
        if payload is None:
            payload = {"ok": True, "output": text}
        print(report.to_json(payload), end="")
    else:
        print(text)


def _app_brief(app: Any, today: date) -> Dict[str, Any]:
    salary = format_range(app.salary_min, app.salary_max, app.currency, compact=True)
    return {
        "id": app.id,
        "label": app.label,
        "company": app.company,
        "role": app.role,
        "status": app.status,
        "status_label": STATUS_LABELS[app.status],
        "location": app.location,
        "work_mode": app.work_mode,
        "source": app.source,
        "priority": app.priority,
        "salary": salary,
        "age_days": app.age_days(today),
        "days_in_stage": app.days_in_stage(today),
        "days_since_activity": app.days_since_activity(today),
        "furthest_stage": app.furthest_stage(),
        "applied_on": app.applied_on,
        "next_action": app.next_action,
        "next_action_on": app.next_action_on,
        "followups_sent": app.followup_count(),
        "tags": list(app.tags),
    }


def _detail(app: Any, today: date) -> str:
    lines: List[str] = []
    lines.append(f"{app.label}")
    lines.append("─" * 68)
    lines.append(f"  id           {app.id}")
    lines.append(f"  status       {STATUS_LABELS[app.status]} ({app.status})")
    lines.append(f"  furthest     {app.furthest_stage() or '—'}")
    lines.append(f"  location     {app.location or '—'}  ·  {app.work_mode}")
    lines.append(f"  source       {SOURCE_LABELS.get(app.source, app.source)}")
    lines.append(
        f"  salary       {format_range(app.salary_min, app.salary_max, app.currency)}"
    )
    lines.append(f"  priority     {app.priority}/5")
    lines.append(f"  created      {app.created_on or '—'}")
    lines.append(f"  applied      {app.applied_on or '—'}")
    if app.closed_on:
        lines.append(f"  closed       {app.closed_on}")
    if app.deadline_on:
        lines.append(f"  deadline     {app.deadline_on}")
    lines.append(f"  contact      {app.contact or '—'}")
    if app.url:
        lines.append(f"  url          {app.url}")
    if app.tags:
        lines.append(f"  tags         {', '.join(app.tags)}")
    lines.append(f"  age          {app.age_days(today)}d  ·  " f"{app.days_in_stage(today)}d in stage")
    lines.append(
        f"  activity     {app.days_since_activity(today)}d ago"
        + (f" ({app.last_activity_on()})" if app.last_activity_on() else "")
    )
    if app.next_action:
        lines.append(
            f"  next action  {app.next_action}"
            + (f" (due {app.next_action_on})" if app.next_action_on else "")
        )

    if app.interviews:
        lines.append("")
        lines.append("INTERVIEWS")
        for i in app.interviews:
            mark = "✔" if i.done else "◷"
            extra = f" — {i.note}" if i.note else ""
            lines.append(f"  {mark} {i.on}  {i.kind}{extra}")

    if app.notes:
        lines.append("")
        lines.append("NOTES")
        for n in app.notes:
            lines.append(f"  {n.on}  {n.text}")

    if app.events:
        lines.append("")
        lines.append("TIMELINE")
        for e in app.events:
            arrow = ""
            if e.from_status and e.to_status:
                arrow = f"  {e.from_status} → {e.to_status}"
            elif e.to_status:
                arrow = f"  → {e.to_status}"
            extra = f"  · {e.note}" if e.note else ""
            lines.append(f"  {e.on}  {e.kind:<9}{arrow}{extra}")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Command dispatch
# --------------------------------------------------------------------------


def run(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help()
        return EXIT_OK

    engine = JobFlowEngine.open(args.vault)
    today = engine.resolve_today(args.today)
    command = args.command

    # ---- writes ----------------------------------------------------------
    if command == "add":
        tags = [t for t in (args.tags or "").replace(";", ",").split(",") if t.strip()]
        app = engine.add(
            args.company,
            args.role,
            today=today,
            status=args.status,
            applied_on=args.applied_on,
            location=args.location,
            work_mode=args.work_mode,
            source=args.source,
            url=args.url,
            currency=args.currency,
            salary_min=args.salary_min,
            salary_max=args.salary_max,
            priority=args.priority,
            deadline_on=args.deadline,
            contact=args.contact,
            tags=tags,
            id=args.id,
        )
        engine.save()
        _emit(
            args,
            f"Added {app.label}\n  id: {app.id}\n  status: {STATUS_LABELS[app.status]}",
            {"ok": True, "added": app.to_dict()},
        )

    elif command == "log-apply":
        app = engine.log_apply(args.app_id, today=today, on=args.on, note=args.note)
        engine.save()
        _emit(
            args,
            f"Logged the application to {app.company} on {app.applied_on}.",
            {"ok": True, "application": app.to_dict()},
        )

    elif command == "move":
        app = engine.move(args.app_id, args.status, today=today, note=args.note, force=args.force)
        engine.save()
        _emit(
            args,
            f"{app.label} is now {STATUS_LABELS[app.status]}.",
            {"ok": True, "application": app.to_dict()},
        )

    elif command == "stage":
        app = engine.log_stage(args.app_id, args.stage, today=today, on=args.on, note=args.note)
        engine.save()
        _emit(
            args,
            f"Recorded {args.stage} for {app.label} on {args.on or today.isoformat()}.",
            {"ok": True, "application": app.to_dict()},
        )

    elif command == "reopen":
        app = engine.reopen(args.app_id, today=today, note=args.note)
        engine.save()
        _emit(
            args,
            f"Reopened {app.label} as {STATUS_LABELS[app.status]}.",
            {"ok": True, "application": app.to_dict()},
        )

    elif command == "drop":
        app = engine.drop(args.app_id)
        engine.save()
        _emit(args, f"Deleted {app.label} ({app.id}).", {"ok": True, "deleted": app.id})

    elif command == "interview":
        if args.done:
            app = engine.complete_interview(
                args.app_id, today=today, on=args.on, note=args.note
            )
            engine.save()
            _emit(
                args,
                f"Marked the interview for {app.label} complete.",
                {"ok": True, "application": app.to_dict()},
            )
        else:
            interview_on = args.on or today.isoformat()
            interview = engine.schedule_interview(
                args.app_id, interview_on, kind=args.kind, note=args.note
            )
            engine.save()
            _emit(
                args,
                f"Scheduled a {args.kind.replace('_', ' ')} interview on {interview.on}.",
                {"ok": True, "interview": interview.to_dict()},
            )

    elif command == "note":
        app = engine.note(args.app_id, args.text, today=today, on=args.on)
        engine.save()
        _emit(args, f"Noted on {app.label}.", {"ok": True, "application": app.to_dict()})

    elif command == "followup":
        app = engine.followup(args.app_id, today=today, note=args.note, channel=args.channel)
        engine.save()
        _emit(
            args,
            f"Logged follow-up #{app.followup_count()} for {app.label}.",
            {"ok": True, "application": app.to_dict()},
        )

    elif command == "next":
        app = engine.set_next_action(args.app_id, args.action, on=args.on)
        engine.save()
        due = f" due {app.next_action_on}" if app.next_action_on else ""
        _emit(args, f"Next action for {app.label}: {app.next_action}{due}", {"ok": True})

    elif command == "edit":
        changes = {
            "company": args.company,
            "role": args.role,
            "location": args.location,
            "work_mode": args.work_mode,
            "source": args.source,
            "url": args.url,
            "currency": args.currency,
            "salary_min": args.salary_min,
            "salary_max": args.salary_max,
            "priority": args.priority,
            "deadline_on": args.deadline,
            "contact": args.contact,
            "next_action": args.next_action,
            "tags": args.tags,
        }
        changes = {k: v for k, v in changes.items() if v is not None}
        app = engine.edit(args.app_id, today=today, **changes)
        engine.save()
        _emit(
            args,
            f"Updated {app.label} ({', '.join(sorted(changes))}).",
            {"ok": True, "application": app.to_dict()},
        )

    elif command == "init":
        engine.vault.owner = engine.vault.owner or ""
        path = engine.save()
        _emit(args, f"Vault ready at {path}", {"ok": True, "path": str(path)})

    elif command == "demo":
        info = engine.seed_demo(today=today, force=args.force)
        path = engine.save()
        _emit(
            args,
            f"Seeded {info['seeded']} applications into {path}.\n"
            "Try: jobflow status   ·   jobflow plan   ·   jobflow board",
            {"ok": True, "seeded": info, "path": str(path)},
        )

    # ---- reads -----------------------------------------------------------
    elif command == "list":
        apps = engine.list(
            statuses=args.status, active_only=args.active, tag=args.tag, company=args.company
        )
        if args.json:
            _emit(
                args,
                "",
                {"ok": True, "count": len(apps), "applications": [_app_brief(a, today) for a in apps]},
            )
        else:
            if not apps:
                print("No applications match.")
            else:
                for app in apps:
                    brief = _app_brief(app, today)
                    print(
                        f"  {app.id:<34} {brief['status_label']:<15} "
                        f"{app.label[:34]:<34} {brief['age_days']:>3}d"
                    )
                print(f"\n{len(apps)} application(s).")

    elif command == "board":
        text = engine.render_board(today=today)
        _emit(args, text, {"ok": True, "board": text})

    elif command == "show":
        app = engine.get(args.app_id)
        _emit(args, _detail(app, today), {"ok": True, "application": app.to_dict()})

    elif command == "status":
        text = engine.render_status(today=today)
        _emit(args, text, {"ok": True, "status": text, "summary": engine.summary(today=today)})

    elif command == "plan":
        text = engine.render_plan(today=today)
        _emit(args, text, {"ok": True, "plan": engine.plan(today=today, limit=50)})

    elif command == "report":
        text = engine.render_report(today=today)
        _emit(args, text, {"ok": True, "report": text})

    elif command == "md":
        text = engine.render_markdown(today=today)
        _emit(args, text, {"ok": True, "markdown": text})

    elif command == "export":
        bundle = engine.export(today=today)
        print(report.to_json(bundle), end="")

    elif command == "vocab":
        vocab = engine.vocabulary()
        if args.json:
            print(report.to_json(vocab), end="")
        else:
            print(json.dumps(vocab, indent=2, sort_keys=True))

    elif command == "upcoming":
        rows = engine.upcoming(today=today, days=args.days)
        if args.json:
            _emit(args, "", {"ok": True, "count": len(rows), "interviews": rows})
        else:
            if not rows:
                print(f"No interviews in the next {args.days} day(s).")
            for row in rows:
                when = "today" if row["days_away"] == 0 else f"in {row['days_away']}d"
                print(
                    f"  {row['on']}  {row['kind']:<11} {row['label'][:38]:<38} {when}"
                )

    elif command == "why":
        result = engine.explain(args.rule, app_id=args.app, today=today)
        if args.json:
            print(report.to_json(result), end="")
        elif result["found"]:
            a = result["action"]
            print(f"{a['title']}  [{a['severity']}]  score {a['score']}")
            print(f"  {a['action']}")
            print(f"  why: {a['explain']}")
            print(f"  score terms: {a['terms']}")
        else:
            print(result["reason"])

    else:  # pragma: no cover - argparse rejects unknown commands first
        parser.print_help()
        return EXIT_ERROR

    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point that turns exceptions into exit codes and clean messages."""

    try:
        return run(argv)
    except JobFlowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

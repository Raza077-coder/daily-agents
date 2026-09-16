"""SplitKit command-line interface.

    splitkit init "Goa Trip" --members ali,sara,bilal --currency USD
    splitkit add "Beach hut" 180.00 --paid-by ali
    splitkit add "Dinner" 92.40 --paid-by sara --split shares --shares ali=2,sara=1,bilal=1
    splitkit add "Cab" 24.00 --paid-by bilal --split exact --amounts ali=12,sara=6,bilal=6
    splitkit add "Groceries" 58.30 --paid-by ali --split itemized --items "Milk:4.50@ali,sara" ...
    splitkit report
    splitkit settle
    splitkit whoowes
    splitkit settle-record ali sara 30.00

Every command exits 0 on success and 1 on a handled error, printing the reason
to stderr — so it composes in a shell script or a CI job.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .config import Config, DEFAULTS, load_config, load_persona
from .engine import SplitKit, currency_reference, describe_modes
from .errors import SplitKitError, ValidationError
from .models import CATEGORY_ICONS, KNOWN_CATEGORIES
from .money import CURRENCY_EXPONENTS, exponent_for, format_amount, parse_amount
from .splits import MODES
from . import settle as _settle
from . import storage as _storage

PROG = "splitkit"


# --------------------------------------------------------------------------- #
# arg helpers
# --------------------------------------------------------------------------- #


def _parse_kv(pairs: Optional[Sequence[str]], what: str) -> Dict[str, str]:
    """Parse ``ali=2,sara=1`` style mappings, or repeated ``--x ali=2``."""
    out: Dict[str, str] = {}
    if not pairs:
        return out
    tokens: List[str] = []
    for item in pairs:
        tokens.extend(str(item).split(","))
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValidationError(
                f"{what} entry {token!r} must look like member=value (e.g. ali=2)"
            )
        key, _, value = token.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not key or not value:
            raise ValidationError(f"{what} entry {token!r} is missing a member or a value")
        if key in out:
            raise ValidationError(f"{what} lists {key!r} twice")
        out[key] = value
    return out


def _parse_items(raw: Optional[Sequence[str]], currency: str) -> List[Dict[str, Any]]:
    """Parse ``--items "Milk:4.50@ali,sara" "Bread:3.20@all"``.

    ``@all`` (or omitting ``@``) assigns an item to every participant.
    """
    items: List[Dict[str, Any]] = []
    if not raw:
        return items
    exp = exponent_for(currency)
    for entry in raw:
        text = str(entry).strip()
        if not text:
            continue
        if ":" not in text:
            raise ValidationError(
                f"item {text!r} must look like \"Label:amount@who\" (e.g. \"Milk:4.50@ali,sara\")"
            )
        label_part, _, rest = text.partition(":")
        label = label_part.strip()
        if not label:
            raise ValidationError(f"item {text!r} has no label")
        amount_part, sep, who_part = rest.partition("@")
        amount_text = amount_part.strip()
        if not amount_text:
            raise ValidationError(f"item {text!r} has no amount")
        amount = parse_amount(amount_text, exp)
        participants: Optional[List[str]] = None
        if sep:
            who_text = who_part.strip()
            if who_text and who_text.lower() not in ("all", "*", "everyone"):
                participants = [w.strip().lower() for w in who_text.split(",") if w.strip()]
        items.append({"label": label, "amount": amount, "participants": participants})
    return items


def _build_split(args: argparse.Namespace, currency: str) -> Dict[str, Any]:
    """Turn the CLI split flags into a split spec dict."""
    mode = (args.split or "equal").lower()
    if mode not in MODES:
        raise ValidationError(f"--split must be one of {', '.join(MODES)} — got {mode!r}")

    spec: Dict[str, Any] = {"mode": mode}
    if getattr(args, "participants", None):
        spec["participants"] = [p.strip().lower() for p in args.participants.split(",") if p.strip()]

    if mode == "exact":
        amounts = _parse_kv(getattr(args, "amounts", None), "--amounts")
        if not amounts:
            raise ValidationError("--split exact needs --amounts (e.g. --amounts ali=12,sara=6)")
        spec["amounts"] = {k: parse_amount(v, exponent_for(currency)) for k, v in amounts.items()}
    elif mode == "shares":
        shares = _parse_kv(getattr(args, "shares", None), "--shares")
        if not shares:
            raise ValidationError("--split shares needs --shares (e.g. --shares ali=2,sara=1)")
        spec["shares"] = shares
    elif mode == "percent":
        percents = _parse_kv(getattr(args, "percents", None), "--percents")
        if not percents:
            raise ValidationError("--split percent needs --percents (e.g. --percents ali=60,sara=40)")
        spec["percents"] = percents
    elif mode == "itemized":
        items = _parse_items(getattr(args, "items", None), currency)
        if not items:
            raise ValidationError(
                "--split itemized needs --items (e.g. --items \"Milk:4.50@ali,sara\")"
            )
        spec["items"] = items
        if getattr(args, "tax", None):
            spec["tax"] = parse_amount(args.tax, exponent_for(currency))
        if getattr(args, "tip", None):
            spec["tip"] = parse_amount(args.tip, exponent_for(currency))
        if getattr(args, "tax_mode", None):
            spec["tax_mode"] = args.tax_mode
        if getattr(args, "tip_mode", None):
            spec["tip_mode"] = args.tip_mode
    elif mode == "adjustment":
        adjustments = _parse_kv(getattr(args, "adjustments", None), "--adjustments")
        if not adjustments:
            raise ValidationError(
                "--split adjustment needs --adjustments (e.g. --adjustments ali=10,sara=-10)"
            )
        spec["adjustments"] = {
            k: parse_amount(v, exponent_for(currency)) for k, v in adjustments.items()
        }
    return spec


def _open(args: argparse.Namespace) -> SplitKit:
    """Open the group named by the global flags."""
    cfg = load_config(getattr(args, "config", None))
    path = getattr(args, "file", None) or cfg.group_file
    return SplitKit.open(path, config_path=getattr(args, "config", None), auto_save=True)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_init(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    members = [m.strip() for m in (args.members or "").split(",") if m.strip()]
    if not members:
        raise ValidationError('--members is required (e.g. --members ali,sara,bilal)')
    path = args.file or cfg.group_file
    if _storage.group_exists(path) and not args.force:
        raise ValidationError(
            f"a group already exists at {path} — pass --force to overwrite it"
        )
    kit = SplitKit.create(
        args.name,
        members,
        currency=args.currency or cfg.default_currency,
        path=path,
        config_path=args.config,
    )
    if args.json:
        _print_json(kit.to_dict())
    else:
        print(f"Created \u201c{kit.group.name}\u201d ({kit.group.currency}) at {path}")
        print(f"  members: {', '.join(m.name for m in kit.group.members)}")
        print("Next: splitkit add \"Dinner\" 92.40 --paid-by " + kit.group.members[0].id)
    return 0


def cmd_add_member(args: argparse.Namespace) -> int:
    kit = _open(args)
    m = kit.add_member(args.name)
    if args.json:
        _print_json(m)
    else:
        print(f"Added {m['name']} (id: {m['id']})")
    return 0


def cmd_remove_member(args: argparse.Namespace) -> int:
    kit = _open(args)
    result = kit.remove_member(args.member, force=args.force)
    if args.json:
        _print_json(result)
    else:
        tail = " along with their expenses and settlements" if result["forced"] else ""
        print(f"Removed {args.member}{tail}")
    return 0


def cmd_members(args: argparse.Namespace) -> int:
    kit = _open(args)
    rows = kit.balance_table()
    if args.json:
        _print_json(rows)
        return 0
    exp = exponent_for(kit.group.currency)
    print(f"{kit.group.name} \u2014 {len(rows)} member(s), {kit.group.currency}")
    for r in rows:
        state = {"owed": f"is owed {r['balance_display']}",
                 "owes": f"owes {format_amount(-r['balance_minor'], exp)}",
                 "square": "square"}[r["state"]]
        print(f"  {r['name'].ljust(12)} {r['id'].ljust(12)} {state}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    kit = _open(args)
    currency = kit.group.currency
    exp = exponent_for(currency)
    amount = parse_amount(args.amount, exp)
    payer = args.paid_by or kit.group.members[0].id
    spec = _build_split(args, currency)
    detail = kit.add_expense(
        args.description,
        amount,
        payer,
        spec,
        date=args.date,
        category=args.category,
    )
    if args.json:
        _print_json(detail)
        return 0
    print(
        f"Added \u201c{detail['description']}\u201d {detail['amount_display']} "
        f"paid by {detail['paid_by_name']}"
    )
    print(f"  {detail['split_explained']}")
    for who, share in detail["shares"].items():
        print(f"    {share['name'].ljust(12)} {share['display']}")
    check = detail["shares_sum_check"]
    if not check["ok"]:  # pragma: no cover - resolve() prevents this
        print(
            f"  WARNING: shares sum to {check['sum_minor']} but the expense is "
            f"{check['expected_minor']}",
            file=sys.stderr,
        )
        return 1
    print(f"  shares sum exactly to {format_amount(check['sum_minor'], exp)} \u2713")
    return 0


def cmd_remove_expense(args: argparse.Namespace) -> int:
    kit = _open(args)
    result = kit.remove_expense(args.expense_id)
    if args.json:
        _print_json(result)
    else:
        print(f"Removed expense {result['removed']} (\u201c{result['description']}\u201d)")
    return 0


def cmd_expenses(args: argparse.Namespace) -> int:
    kit = _open(args)
    rows = kit.expenses()
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print("No expenses recorded yet.")
        return 0
    for r in rows:
        icon = CATEGORY_ICONS.get(r["category"], "\U0001f4cc")
        print(f"{icon} [{r['id']}] {r['date']}  {r['description']}  {r['amount_display']}  (paid by {r['paid_by_name']})")
        print(f"     {r['split_explained']}")
        for who, share in r["shares"].items():
            print(f"       {share['name'].ljust(12)} {share['display']}")
    return 0


def cmd_balances(args: argparse.Namespace) -> int:
    kit = _open(args)
    rows = kit.balance_table()
    if args.json:
        _print_json(rows)
        return 0
    exp = exponent_for(kit.group.currency)
    name_w = max([len(r["name"]) for r in rows] + [6])
    paid_w = max([len(r["paid_display"]) for r in rows] + [8])
    share_w = max([len(r["share_display"]) for r in rows] + [8])
    bal_w = max([len(r["balance_display"]) for r in rows] + [7])
    print(f"{kit.group.name} \u2014 balances ({kit.group.currency})")
    print(f"{'Member'.ljust(name_w)}  {'Paid'.rjust(paid_w)}  {'Share'.rjust(share_w)}  {'Net'.rjust(bal_w)}")
    print(f"{'-' * name_w}  {'-' * paid_w}  {'-' * share_w}  {'-' * bal_w}")
    for r in rows:
        note = ""
        if r["state"] == "owed":
            note = "owed"
        elif r["state"] == "owes":
            note = "owes " + format_amount(-r["balance_minor"], exp)
        print(
            f"{r['name'].ljust(name_w)}  {r['paid_display'].rjust(paid_w)}  "
            f"{r['share_display'].rjust(share_w)}  {r['balance_display'].rjust(bal_w)}  {note}"
        )
    total = sum(r["balance_minor"] for r in rows)
    print(f"\nSum of net positions: {format_amount(total, exp)}  (must be 0)")
    return 0 if total == 0 else 1


def cmd_settle(args: argparse.Namespace) -> int:
    kit = _open(args)
    min_transfer = 0
    if args.min_transfer:
        min_transfer = parse_amount(args.min_transfer, exponent_for(kit.group.currency))
    plan = kit.settle(args.strategy, min_transfer=min_transfer)
    if args.json:
        _print_json(plan)
        return 0
    if not plan["transfers"]:
        print("Everyone is square. Nothing to settle.")
        return 0
    width = max([len(t["from_name"]) for t in plan["transfers"]] + [4])
    for t in plan["transfers"]:
        print(f"{t['from_name'].ljust(width)}  \u2192  {t['to_name']}   {t['amount_display']}")
    print()
    head = f"{plan['transfer_count']} transfer{'s' if plan['transfer_count'] != 1 else ''}"
    if plan["optimal_feasible"] and plan["strategy"] == "optimal":
        head += " (proven minimum)"
    elif plan["optimal_feasible"] and plan["optimal_count"] < plan["greedy_count"]:
        head += f" (naive plan would need {plan['greedy_count']})"
    print(f"{head} clears the whole book.")
    for note in plan["notes"]:
        print(f"note: {note}")
    return 0


def cmd_whoowes(args: argparse.Namespace) -> int:
    kit = _open(args)
    rows = kit.balance_table()
    exp = exponent_for(kit.group.currency)
    lines = []
    for r in rows:
        if r["state"] == "owed":
            lines.append(f"{r['name']} is owed {format_amount(r['balance_minor'], exp)}")
        elif r["state"] == "owes":
            lines.append(f"{r['name']} owes {format_amount(-r['balance_minor'], exp)}")
    if args.json:
        _print_json({"lines": lines, "rows": rows})
        return 0
    if not lines:
        print("Everyone is square. Nothing to settle.")
        return 0
    print("\n".join(lines))
    return 0


def cmd_settle_record(args: argparse.Namespace) -> int:
    kit = _open(args)
    s = kit.record_settlement(
        args.from_member, args.to_member, args.amount, date=args.date
    )
    plan = kit.settle()
    if args.json:
        _print_json({"settlement": s, "plan": plan})
        return 0
    exp = exponent_for(kit.group.currency)
    print(
        f"Recorded: {kit.group.name_of(s['from'])} paid {kit.group.name_of(s['to'])} "
        f"{format_amount(s['amount'], exp)}"
    )
    if not plan["transfers"]:
        print("Everyone is now square.")
    else:
        print(f"{plan['transfer_count']} transfer(s) remaining:")
        for t in plan["readable"]:
            print(f"  {t}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    kit = _open(args)
    if args.format == "json":
        _print_json(kit.report(include_expenses=not args.no_expenses))
        return 0
    if args.format == "markdown":
        sys.stdout.write(kit.render_markdown(include_expenses=not args.no_expenses))
        return 0
    sys.stdout.write(kit.render_text(include_expenses=not args.no_expenses))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    kit = _open(args)
    text = kit.to_json()
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    text = Path(args.path).read_text(encoding="utf-8")
    kit = SplitKit.from_json(text, config=cfg)
    target = args.file or cfg.group_file
    if _storage.group_exists(target) and not args.force:
        raise ValidationError(f"a group already exists at {target} — pass --force to overwrite it")
    kit.save(target)
    if args.json:
        _print_json(kit.to_dict())
    else:
        print(f"Imported \u201c{kit.group.name}\u201d into {target}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    payload = {
        "settings": cfg.to_dict(),
        "defaults": dict(DEFAULTS),
        "sources": cfg.source_files,
        "group_file_exists": _storage.group_exists(cfg.group_file),
    }
    if args.json:
        _print_json(payload)
        return 0
    print("SplitKit settings")
    for key, value in cfg.to_dict().items():
        print(f"  {key.ljust(18)} {value}")
    if cfg.source_files:
        print("\nResolved from:")
        for src in cfg.source_files:
            print(f"  {src}")
    else:
        print("\nUsing built-in defaults (no config file or env overrides found).")
    return 0


def cmd_modes(args: argparse.Namespace) -> int:
    modes = describe_modes()
    if args.json:
        _print_json(modes)
        return 0
    print("Split modes")
    for name, blurb in modes.items():
        print(f"  {name.ljust(11)} {blurb}")
    return 0


def cmd_currencies(args: argparse.Namespace) -> int:
    ref = currency_reference()
    if args.json:
        _print_json(ref)
        return 0
    print(f"SplitKit knows {ref['count']} currencies (default exponent {ref['default_exponent']}).")
    by_exp: Dict[int, List[str]] = {}
    for code, exp in ref["exponents"].items():
        by_exp.setdefault(exp, []).append(code)
    for exp in sorted(by_exp):
        print(f"  {exp} decimal(s): {', '.join(by_exp[exp])}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Build a seeded example group so the engine can be exercised instantly."""
    from .demo import build_demo_group

    group = build_demo_group(args.currency or "USD")
    kit = SplitKit(group=group, config=load_config(args.config))
    if args.out:
        kit.save(args.out)
    if args.json:
        _print_json(kit.report())
        return 0
    sys.stdout.write(kit.render_text())
    if args.out:
        print(f"(saved to {args.out})")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Prove the ledger balances and the settle-up plan actually clears it."""
    kit = _open(args)
    net = kit.balances()
    exp = exponent_for(kit.group.currency)
    total = sum(net.values())
    plan = kit.settle()
    valid = kit.plan_is_valid()
    payload = {
        "balanced": total == 0,
        "sum_of_balances_minor": total,
        "settle_plan_valid": valid,
        "transfer_count": plan["transfer_count"],
        "lower_bound": _settle.lower_bound(net),
        "optimal_feasible": plan["optimal_feasible"],
    }
    if args.json:
        _print_json(payload)
        return 0 if (total == 0 and valid) else 1
    print(f"Ledger balances         : {'yes' if total == 0 else 'NO'} (sum {format_amount(total, exp)})")
    print(f"Settle plan clears it   : {'yes' if valid else 'NO'}")
    print(f"Transfers in plan       : {payload['transfer_count']}")
    print(f"Theoretical minimum     : at least {payload['lower_bound']}")
    if payload["optimal_feasible"]:
        print(f"Plan is proven minimum  : {'yes' if plan['transfer_count'] <= payload['lower_bound'] + plan['transfers_saved'] else 'see report'}")
    else:
        print("Exact minimum search    : skipped (group too large)")
    ok = total == 0 and valid
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="SplitKit \u2014 split shared costs and settle up in the fewest transfers.",
        epilog=(
            "Money is handled as exact integer minor units; a split that does not "
            "sum to its expense is refused rather than silently rounded."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument("--config", help="path to a splitkit.json config file")
    parser.add_argument(
        "--file", help="path to the group file (default: .splitkit/group.json)"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a new group")
    p.add_argument("name", help="group name, e.g. \"Goa Trip\"")
    p.add_argument("--members", required=True, help="comma-separated names, e.g. ali,sara,bilal")
    p.add_argument("--currency", help="ISO currency code (default from config)")
    p.add_argument("--force", action="store_true", help="overwrite an existing group file")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add-member", help="add a member")
    p.add_argument("name", help="display name, e.g. \"Ali Raza\"")
    p.set_defaults(func=cmd_add_member)

    p = sub.add_parser("remove-member", help="remove a member")
    p.add_argument("member", help="member id")
    p.add_argument("--force", action="store_true", help="also delete their expenses and settlements")
    p.set_defaults(func=cmd_remove_member)

    p = sub.add_parser("members", help="list members with their positions")
    p.set_defaults(func=cmd_members)

    p = sub.add_parser("add", help="record an expense")
    p.add_argument("description", help="what it was for")
    p.add_argument("amount", help="amount as a string, e.g. 92.40")
    p.add_argument("--paid-by", dest="paid_by", help="member id who fronted the money")
    p.add_argument("--split", choices=list(MODES), default=None, help="split mode")
    p.add_argument("--participants", help="comma-separated member ids (default: everyone)")
    p.add_argument("--amounts", action="append", help="exact amounts, e.g. ali=12,sara=6")
    p.add_argument("--shares", action="append", help="share weights, e.g. ali=2,sara=1")
    p.add_argument("--percents", action="append", help="percentages, e.g. ali=60,sara=40")
    p.add_argument("--items", action="append", help="itemized lines, e.g. \"Milk:4.50@ali,sara\"")
    p.add_argument("--adjustments", action="append", help="signed tweaks, e.g. ali=10,sara=-10")
    p.add_argument("--tax", help="tax amount for an itemized split")
    p.add_argument("--tip", help="tip amount for an itemized split")
    p.add_argument("--tax-mode", dest="tax_mode", choices=["proportional", "equal"],
                   help="how to spread tax (default proportional)")
    p.add_argument("--tip-mode", dest="tip_mode", choices=["proportional", "equal"],
                   help="how to spread tip (default proportional)")
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    p.add_argument("--category", default="other",
                   help=f"one of: {', '.join(KNOWN_CATEGORIES)}")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove-expense", help="delete an expense")
    p.add_argument("expense_id", help="expense id, e.g. e3")
    p.set_defaults(func=cmd_remove_expense)

    p = sub.add_parser("expenses", help="list expenses and their resolved shares")
    p.set_defaults(func=cmd_expenses)

    p = sub.add_parser("balances", help="show paid / share / net per member")
    p.set_defaults(func=cmd_balances)

    p = sub.add_parser("settle", help="compute the settle-up transfers")
    p.add_argument("--strategy", choices=["greedy", "optimal", "compare"], default=None,
                   help="optimal (default) proves the fewest transfers")
    p.add_argument("--min-transfer", dest="min_transfer",
                   help="absorb transfers at or below this amount")
    p.set_defaults(func=cmd_settle)

    p = sub.add_parser("whoowes", help="one line per member who is not square")
    p.set_defaults(func=cmd_whoowes)

    p = sub.add_parser("settle-record", help="record a repayment")
    p.add_argument("from_member", help="member id who paid")
    p.add_argument("to_member", help="member id who received")
    p.add_argument("amount", help="amount as a string, e.g. 30.00")
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    p.set_defaults(func=cmd_settle_record)

    p = sub.add_parser("report", help="full report (text, markdown or json)")
    p.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    p.add_argument("--no-expenses", dest="no_expenses", action="store_true",
                   help="summaries only, skip the expense list")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("export", help="print the group as JSON")
    p.add_argument("--out", help="write to this file instead of stdout")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="load a group from a JSON file")
    p.add_argument("path", help="path to the JSON file")
    p.add_argument("--force", action="store_true", help="overwrite an existing group file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("config", help="show the resolved configuration")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("modes", help="explain the split modes")
    p.set_defaults(func=cmd_modes)

    p = sub.add_parser("currencies", help="list known currencies and their exponents")
    p.set_defaults(func=cmd_currencies)

    p = sub.add_parser("demo", help="run a seeded example group")
    p.add_argument("--currency", help="currency for the demo group")
    p.add_argument("--out", help="also save the demo group to this path")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("verify", help="check the ledger balances and the plan clears it")
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except SplitKitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

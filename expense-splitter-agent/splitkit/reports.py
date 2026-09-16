"""Report rendering: balance sheets, settle-up plans, and spend breakdowns.

Three formats from one set of numbers:

* ``text`` — aligned terminal output, what the CLI prints
* ``markdown`` — for pasting into a group chat or a README
* ``json`` — for pipelines, and the source of truth the other two render from

All three are rendered from :func:`build_report`, so they can never disagree.
Alignment is computed from the widest formatted amount, which keeps columns
straight for currencies with different widths.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import ledger as _ledger
from . import settle as _settle
from .config import Config
from .models import CATEGORY_ICONS, Group
from .money import exponent_for, format_amount


def _fmt(minor: int, group: Group, config: Optional[Config] = None, *, plus: bool = False) -> str:
    cfg = config or Config()
    exp = exponent_for(group.currency)
    from .money import SYMBOLS

    return format_amount(
        minor,
        exp,
        symbol=SYMBOLS.get(group.currency) if cfg.show_symbols else None,
        code=None if cfg.show_symbols else group.currency,
        grouping=cfg.grouping,
        plus=plus,
    )


def build_report(
    group: Group,
    *,
    config: Optional[Config] = None,
    strategy: str = "optimal",
    include_expenses: bool = True,
    expense_limit: int = 0,
) -> Dict[str, Any]:
    """Assemble the complete report structure every renderer consumes."""
    cfg = config or Config()
    net = _ledger.balances(group)
    plan = _settle.settle(group, strategy=strategy)
    creditors, debtors = _ledger.creditors_and_debtors(group)
    exp = exponent_for(group.currency)
    from .money import SYMBOLS

    def fmt(minor: int, plus: bool = False) -> str:
        return format_amount(
            minor,
            exp,
            symbol=SYMBOLS.get(group.currency) if cfg.show_symbols else None,
            code=None if cfg.show_symbols else group.currency,
            grouping=cfg.grouping,
            plus=plus,
        )

    members = []
    paid = _ledger.paid_totals(group)
    owed = _ledger.owed_totals(group)
    for m in group.members:
        balance = net.get(m.id, 0)
        members.append({
            "id": m.id,
            "name": m.name,
            "paid_minor": paid.get(m.id, 0),
            "paid_display": fmt(paid.get(m.id, 0)),
            "share_minor": owed.get(m.id, 0),
            "share_display": fmt(owed.get(m.id, 0)),
            "balance_minor": balance,
            "balance_display": fmt(balance, plus=True),
            "state": "owed" if balance > 0 else ("owes" if balance < 0 else "square"),
        })

    transfers = [
        {
            "from": t.from_member,
            "from_name": group.name_of(t.from_member),
            "to": t.to_member,
            "to_name": group.name_of(t.to_member),
            "amount_minor": t.amount,
            "amount_display": fmt(t.amount),
        }
        for t in plan.transfers
    ]

    expenses: List[Dict[str, Any]] = []
    if include_expenses:
        items = group.expenses
        if expense_limit > 0:
            items = _ledger.top_expenses(group, expense_limit)
        for e in items:
            shares = _settle_and_shares(e, group)
            expenses.append({
                "id": e.id,
                "description": e.description,
                "amount_display": fmt(e.amount),
                "amount_minor": e.amount,
                "paid_by_name": group.name_of(e.paid_by),
                "date": e.date,
                "category": e.category,
                "category_icon": CATEGORY_ICONS.get(e.category, "\U0001f4cc"),
                "shares": {
                    group.name_of(k): fmt(v) for k, v in shares.items()
                },
            })

    return {
        "group": group.id,
        "name": group.name,
        "currency": group.currency,
        "summary": _ledger.health(group),
        "members": members,
        "creditors": [
            {"name": group.name_of(m), "amount_display": fmt(v), "amount_minor": v}
            for m, v in creditors
        ],
        "debtors": [
            {"name": group.name_of(m), "amount_display": fmt(-v), "amount_minor": -v}
            for m, v in debtors
        ],
        "settle_plan": {
            **_settle_plan_summary(plan, group, fmt),
            "transfers": transfers,
        },
        "expenses": expenses,
        "categories": [
            {
                "category": cat,
                "icon": CATEGORY_ICONS.get(cat, "\U0001f4cc"),
                "amount_minor": amount,
                "amount_display": fmt(amount),
                "share_of_total": (
                    round(100.0 * amount / group.total_minor, 1) if group.total_minor else 0.0
                ),
            }
            for cat, amount in _ledger.category_totals(group).items()
        ],
        "audit": _ledger.audit(group),
        "per_member_categories": {
            group.name_of(mid): {cat: fmt(v) for cat, v in cats.items()}
            for mid, cats in _ledger.member_spend_matrix(group).items()
        },
    }


def _settle_plan_summary(plan: _settle.SettlePlan, group: Group, fmt) -> Dict[str, Any]:
    return {
        "strategy": plan.strategy,
        "transfer_count": plan.count,
        "greedy_count": plan.greedy_count,
        "optimal_count": plan.optimal_count,
        "optimal_feasible": plan.optimal_feasible,
        "transfers_saved": plan.saved,
        "total_moved_minor": sum(t.amount for t in plan.transfers),
        "notes": list(plan.notes),
    }


def _settle_and_shares(expense, group: Group) -> Dict[str, int]:
    from .splits import resolve

    return resolve(expense.split, expense.amount, group.member_ids, currency=group.currency)


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #


def render_text(
    group: Group,
    *,
    config: Optional[Config] = None,
    strategy: str = "optimal",
    width: int = 60,
    include_expenses: bool = True,
) -> str:
    """Render the plain-text report the CLI prints."""
    cfg = config or Config()
    report = build_report(
        group, config=cfg, strategy=strategy, include_expenses=include_expenses
    )
    summary = report["summary"]
    lines: List[str] = []
    rule = "\u2500" * min(width, max(40, len(group.name) + 26))

    lines.append(f"\U0001f9fe  {group.name}  ({group.currency})")
    lines.append(rule)
    if not group.expenses:
        lines.append("No expenses recorded yet.")
        lines.append(f"Members: {', '.join(m.name for m in group.members)}")
        lines.append(rule)
        return "\n".join(lines) + "\n"

    lines.append(
        f"Total {summary['total_display']}  \u00b7  {summary['expense_count']} expense"
        f"{'s' if summary['expense_count'] != 1 else ''}  \u00b7  "
        f"{summary['member_count']} member{'s' if summary['member_count'] != 1 else ''}"
    )
    lines.append("")

    name_w = max([len(m["name"]) for m in report["members"]] + [6])
    paid_w = max([len(m["paid_display"]) for m in report["members"]] + [8])
    share_w = max([len(m["share_display"]) for m in report["members"]] + [8])
    bal_w = max([len(m["balance_display"]) for m in report["members"]] + [7])

    lines.append(
        f"{'Member'.ljust(name_w)}  {'Paid'.rjust(paid_w)}  {'Share'.rjust(share_w)}  "
        f"{'Net'.rjust(bal_w)}"
    )
    lines.append(
        f"{'-' * name_w}  {'-' * paid_w}  {'-' * share_w}  {'-' * bal_w}"
    )
    for m in report["members"]:
        marker = " \u2192 owed" if m["state"] == "owed" else (" \u2190 owes" if m["state"] == "owes" else "   square")
        lines.append(
            f"{m['name'].ljust(name_w)}  {m['paid_display'].rjust(paid_w)}  "
            f"{m['share_display'].rjust(share_w)}  {m['balance_display'].rjust(bal_w)}{marker}"
        )
    lines.append("")

    if report["categories"]:
        lines.append("Where it went")
        for c in report["categories"]:
            bar = "\u2588" * max(1, int(round(c["share_of_total"] / 5))) if c["share_of_total"] else ""
            lines.append(
                f"  {c['icon']} {c['category'].ljust(12)} {c['amount_display'].rjust(12)}"
                f"  {c['share_of_total']:>5.1f}%  {bar}"
            )
        lines.append("")

    lines.append("Settle up")
    lines.append("-" * 12)
    plan = report["settle_plan"]
    if not plan["transfers"]:
        lines.append("Everyone is square. Nothing to settle.")
    else:
        for t in plan["transfers"]:
            lines.append(
                f"  {t['from_name']}  \u2192  {t['to_name']}   {t['amount_display']}"
            )
        head = (
            f"{plan['transfer_count']} transfer"
            f"{'s' if plan['transfer_count'] != 1 else ''} clears the whole book"
        )
        if plan["transfers_saved"] > 0:
            head += (
                f" ({plan['transfers_saved']} fewer than the naive "
                f"{plan['greedy_count']}-transfer plan)"
            )
        lines.append(f"  {head}.")

    for note in plan["notes"]:
        lines.append(f"  note: {note}")

    if include_expenses and report["expenses"]:
        lines.append("")
        lines.append("Expenses")
        lines.append("-" * 8)
        for e in report["expenses"]:
            lines.append(
                f"  {e['category_icon']} {e['date']}  {e['description']}  "
                f"{e['amount_display']}  (paid by {e['paid_by_name']})"
            )
            for who, amount in e["shares"].items():
                lines.append(f"       \u2514 {who}: {amount}")

    findings = report["audit"]
    if findings:
        lines.append("")
        lines.append("Check these")
        lines.append("-" * 11)
        for f in findings:
            tag = "\u26a0" if f["level"] == "warning" else "\u2139"
            lines.append(f"  {tag} {f['message']}")

    lines.append("")
    if summary["is_settled"]:
        lines.append("Status: settled \u2014 nobody owes anybody.")
    else:
        lines.append(
            f"Status: {summary['tense_count']} owed \u00b7 {summary['owe_count']} owing \u00b7 "
            f"{summary['outstanding_minor'] and fmt_minor(summary['outstanding_minor'], cfg) or '0'} outstanding"
        )
    return "\n".join(lines) + "\n"


def fmt_minor(minor: int, config: Config, currency: str = "USD") -> str:
    """Small helper so render_text can format without a Group in hand."""
    from .money import SYMBOLS

    return format_amount(
        minor,
        exponent_for(currency),
        symbol=SYMBOLS.get(currency) if config.show_symbols else None,
        code=None if config.show_symbols else currency,
        grouping=config.grouping,
    )


def render_markdown(
    group: Group,
    *,
    config: Optional[Config] = None,
    strategy: str = "optimal",
    include_expenses: bool = True,
) -> str:
    """Render the report as Markdown, ready to paste into a group chat."""
    cfg = config or Config()
    report = build_report(
        group, config=cfg, strategy=strategy, include_expenses=include_expenses
    )
    s = report["summary"]
    out: List[str] = []
    out.append(f"# {group.name}")
    out.append("")
    out.append(
        f"**Total** {s['total_display']} \u00b7 **{s['expense_count']}** expense"
        f"{'s' if s['expense_count'] != 1 else ''} \u00b7 "
        f"**{s['member_count']}** member{'s' if s['member_count'] != 1 else ''} \u00b7 "
        f"currency **{group.currency}**"
    )

    if not group.expenses:
        out += ["", "No expenses recorded yet.", "", "**Members:** " + ", ".join(m.name for m in group.members)]
        return "\n".join(out) + "\n"

    out += ["", "## Balances", "", "| Member | Paid | Share | Net | State |", "|---|---:|---:|---:|---|"]
    for m in report["members"]:
        state = {"owed": "owed \U0001f7e2", "owes": "owes \U0001f534", "square": "square \u26aa"}[m["state"]]
        out.append(
            f"| {m['name']} | {m['paid_display']} | {m['share_display']} | "
            f"{m['balance_display']} | {state} |"
        )

    out += ["", "## Settle up", ""]
    plan = report["settle_plan"]
    if not plan["transfers"]:
        out.append("Everyone is square \u2014 nothing to settle.")
    else:
        out += ["| From | To | Amount |", "|---|---|---:|"]
        for t in plan["transfers"]:
            out.append(f"| {t['from_name']} | {t['to_name']} | {t['amount_display']} |")
        tail = (
            f"\n**{plan['transfer_count']}** transfer"
            f"{'s' if plan['transfer_count'] != 1 else ''} clears the book."
        )
        if plan["transfers_saved"]:
            tail += (
                f" That is **{plan['transfers_saved']}** fewer payment"
                f"{'s' if plan['transfers_saved'] != 1 else ''} than the naive "
                f"{plan['greedy_count']}-transfer plan."
            )
        out.append(tail)

    if report["categories"]:
        out += ["", "## Where it went", "", "| Category | Amount | Share |", "|---|---:|---:|"]
        for c in report["categories"]:
            out.append(f"| {c['icon']} {c['category']} | {c['amount_display']} | {c['share_of_total']:.1f}% |")

    if include_expenses and report["expenses"]:
        out += ["", "## Expenses", ""]
        for e in report["expenses"]:
            out.append(f"- **{e['description']}** \u2014 {e['amount_display']} \u2014 paid by {e['paid_by_name']} ({e['date']})")
            for who, amount in e["shares"].items():
                out.append(f"  - {who}: {amount}")

    if report["audit"]:
        out += ["", "## Check these", ""]
        for f in report["audit"]:
            icon = "\u26a0\ufe0f" if f["level"] == "warning" else "\u2139\ufe0f"
            out.append(f"- {icon} {f['message']}")

    for note in plan["notes"]:
        out += ["", f"> {note}"]
    return "\n".join(out) + "\n"


def render_settle_only(
    group: Group,
    *,
    config: Optional[Config] = None,
    strategy: str = "optimal",
    text: bool = True,
) -> str:
    """Render just the settle-up section \u2014 the most-requested output."""
    cfg = config or Config()
    report = build_report(group, config=cfg, strategy=strategy, include_expenses=False)
    plan = report["settle_plan"]
    lines: List[str] = []
    if not plan["transfers"]:
        lines.append("Everyone is square. Nothing to settle.")
    else:
        width = max([len(t["from_name"]) for t in plan["transfers"]] + [4])
        for t in plan["transfers"]:
            lines.append(f"{t['from_name'].ljust(width)}  \u2192  {t['to_name']}   {t['amount_display']}")
        lines.append("")
        lines.append(
            f"{plan['transfer_count']} transfer"
            f"{'s' if plan['transfer_count'] != 1 else ''} clears the whole book."
        )
        if plan["transfers_saved"]:
            lines.append(
                f"Saved {plan['transfers_saved']} payment"
                f"{'s' if plan['transfers_saved'] != 1 else ''} vs the naive "
                f"{plan['greedy_count']}-transfer plan."
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "build_report",
    "render_text",
    "render_markdown",
    "render_settle_only",
]

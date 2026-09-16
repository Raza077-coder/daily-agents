"""SplitKit facade.

Everything a caller needs in one class: load or create a group, add members and
expenses, read balances, get a settle-up plan, and render a report.

The point of a facade here is that the CLI, the REST API and the browser demo
all drive *the same* code path, so a number that appears in the terminal is the
number the API returns. :meth:`SplitKit.answer` is the convenience entry point
for free-text-ish requests ("who owes what") and returns both the structured
payload and a human sentence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import ledger as _ledger
from . import reports as _reports
from . import settle as _settle
from .config import Config, load_config, load_persona
from .errors import SplitKitError, ValidationError
from .models import Group
from .money import exponent_for, format_amount, parse_amount, SYMBOLS
from .splits import MODES, MODE_DESCRIPTIONS, describe, resolve
from . import storage as _storage


class SplitKit:
    """A group ledger with the operations you actually perform on it."""

    def __init__(
        self,
        group: Optional[Group] = None,
        *,
        config: Optional[Config] = None,
        path: Optional[str] = None,
        auto_save: bool = False,
    ) -> None:
        self.config = config or Config()
        self.path = path
        self.auto_save = auto_save
        self.group = group if group is not None else self._load(path)

    # ---- construction ------------------------------------------------------

    @classmethod
    def open(
        cls,
        path: Optional[str] = None,
        *,
        config_path: Optional[str] = None,
        auto_save: bool = True,
        read_only: bool = False,
    ) -> "SplitKit":
        """Open the group at ``path`` (or the configured default)."""
        cfg = load_config(config_path)
        target = path or cfg.group_file
        if read_only:
            return cls(group=_storage.load_group(target), config=cfg, path=target, auto_save=False)
        return cls(group=_storage.load_group(target), config=cfg, path=target, auto_save=auto_save)

    @classmethod
    def create(
        cls,
        name: str,
        members: Sequence[str],
        *,
        currency: Optional[str] = None,
        path: Optional[str] = None,
        config_path: Optional[str] = None,
        auto_save: bool = True,
    ) -> "SplitKit":
        """Create a new group and (by default) write it to disk."""
        cfg = load_config(config_path)
        group = _storage.new_group(
            name, list(members), currency=currency or cfg.default_currency
        )
        target = path or cfg.group_file
        kit = cls(group=group, config=cfg, path=target, auto_save=False)
        if auto_save:
            kit.save(target)
        return kit

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, config: Optional[Config] = None) -> "SplitKit":
        return cls(group=Group.from_dict(dict(raw)), config=config or Config())

    @classmethod
    def from_json(cls, text: str, *, config: Optional[Config] = None) -> "SplitKit":
        return cls(group=_storage.import_group(text), config=config or Config())

    @staticmethod
    def _load(path: Optional[str]) -> Group:
        return _storage.load_group(path)

    def _maybe_save(self) -> None:
        if self.auto_save and self.path:
            self.save()

    # ---- persistence -------------------------------------------------------

    def save(self, path: Optional[str] = None) -> Path:
        target = path or self.path
        if not target:
            raise ValidationError("no path set — pass one to save() or open with a path")
        written = _storage.save_group(self.group, target)
        self.path = str(written)
        return written

    def to_dict(self) -> Dict[str, Any]:
        return self.group.to_dict()

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.group.to_dict(), indent=indent, ensure_ascii=False)

    # ---- members -----------------------------------------------------------

    def add_member(self, name: str, *, member_id: Optional[str] = None) -> Dict[str, Any]:
        m = self.group.add_member(name, member_id=member_id)
        self._maybe_save()
        return m.to_dict()

    def remove_member(self, member_id: str, *, force: bool = False) -> Dict[str, Any]:
        self.group.remove_member(member_id, force=force)
        self._maybe_save()
        return {"removed": member_id, "forced": force}

    def members(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self.group.members]

    # ---- expenses ----------------------------------------------------------

    def add_expense(
        self,
        description: str,
        amount: Any,
        paid_by: str,
        split: Optional[Mapping[str, Any]] = None,
        *,
        expense_id: Optional[str] = None,
        date: Optional[str] = None,
        category: str = "other",
        note: str = "",
    ) -> Dict[str, Any]:
        """Record an expense. ``amount`` may be a string like ``"45.50"``."""
        exp = exponent_for(self.group.currency)
        minor = amount if isinstance(amount, int) and not isinstance(amount, bool) else parse_amount(amount, exp)
        payer = str(paid_by).strip().lower()
        spec = dict(split) if split else {"mode": self.config.default_split}
        # Validate the split before mutating the group, so a rejected expense
        # never lands half-applied in the ledger.
        resolve(spec, minor, self.group.member_ids, currency=self.group.currency)
        e = self.group.add_expense(
            description,
            minor,
            payer,
            spec,
            expense_id=expense_id,
            date=date,
            category=category,
            note=note,
        )
        self._maybe_save()
        return self.expense_detail(e.id)

    def remove_expense(self, expense_id: str) -> Dict[str, Any]:
        e = self.group.remove_expense(expense_id)
        self._maybe_save()
        return {"removed": e.id, "description": e.description}

    def expense_detail(self, expense_id: str) -> Dict[str, Any]:
        e = self.group.expense(expense_id)
        shares = resolve(e.split, e.amount, self.group.member_ids, currency=self.group.currency)
        exp = exponent_for(self.group.currency)
        return {
            "id": e.id,
            "description": e.description,
            "amount_minor": e.amount,
            "amount_display": format_amount(e.amount, exp),
            "paid_by": e.paid_by,
            "paid_by_name": self.group.name_of(e.paid_by),
            "date": e.date,
            "category": e.category,
            "split": e.split,
            "split_explained": describe(e.split, self.group.name_of, self.group.currency),
            "shares": {
                who: {
                    "name": self.group.name_of(who),
                    "minor": amt,
                    "display": format_amount(amt, exp),
                }
                for who, amt in shares.items()
            },
            "shares_sum_check": {
                "sum_minor": sum(shares.values()),
                "expected_minor": e.amount,
                "ok": sum(shares.values()) == e.amount,
            },
        }

    def expenses(self) -> List[Dict[str, Any]]:
        return [self.expense_detail(e.id) for e in self.group.expenses]

    # ---- settlements -------------------------------------------------------

    def record_settlement(
        self,
        from_member: str,
        to_member: str,
        amount: Any,
        *,
        date: Optional[str] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        """Record a repayment so it drops out of the balances."""
        exp = exponent_for(self.group.currency)
        minor = amount if isinstance(amount, int) and not isinstance(amount, bool) else parse_amount(amount, exp)
        s = self.group.add_settlement(
            str(from_member).strip().lower(),
            str(to_member).strip().lower(),
            minor,
            date=date,
            note=note,
        )
        self._maybe_save()
        return s.to_dict()

    def settlements(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.group.settlements]

    # ---- analysis ----------------------------------------------------------

    def balances(self) -> Dict[str, int]:
        return _ledger.balances(self.group)

    def balance_table(self) -> List[Dict[str, Any]]:
        net = self.balances()
        paid = _ledger.paid_totals(self.group)
        owed = _ledger.owed_totals(self.group)
        exp = exponent_for(self.group.currency)
        rows = []
        for m in self.group.members:
            bal = net.get(m.id, 0)
            rows.append({
                "id": m.id,
                "name": m.name,
                "paid_minor": paid.get(m.id, 0),
                "paid_display": format_amount(paid.get(m.id, 0), exp),
                "share_minor": owed.get(m.id, 0),
                "share_display": format_amount(owed.get(m.id, 0), exp),
                "balance_minor": bal,
                "balance_display": format_amount(bal, exp, plus=True),
                "state": "owed" if bal > 0 else ("owes" if bal < 0 else "square"),
            })
        return rows

    def settle(self, strategy: Optional[str] = None, *, min_transfer: int = 0) -> Dict[str, Any]:
        chosen = strategy or self.config.settle_strategy
        plan = _settle.settle(self.group, strategy=chosen, min_transfer=min_transfer)
        exp = exponent_for(self.group.currency)
        payload = plan.to_dict(self.group.currency)
        for t in payload["transfers"]:
            t["from_name"] = self.group.name_of(t["from"])
            t["to_name"] = self.group.name_of(t["to"])
        payload["readable"] = [
            f"{group_name(self.group, t.from_member)} pays "
            f"{group_name(self.group, t.to_member)} {format_amount(t.amount, exp)}"
            for t in plan.transfers
        ]
        return payload

    def plan_is_valid(self, strategy: Optional[str] = None) -> bool:
        """True when the settle-up plan provably clears every balance."""
        plan = _settle.settle(self.group, strategy=strategy or self.config.settle_strategy)
        try:
            _settle.verify_plan(self.balances(), plan.transfers)
        except SplitKitError:
            return False
        return True

    def summary(self) -> Dict[str, Any]:
        return _ledger.health(self.group)

    def report(
        self,
        *,
        strategy: Optional[str] = None,
        include_expenses: bool = True,
        expense_limit: int = 0,
    ) -> Dict[str, Any]:
        return _reports.build_report(
            self.group,
            config=self.config,
            strategy=strategy or self.config.settle_strategy,
            include_expenses=include_expenses,
            expense_limit=expense_limit,
        )

    def render_text(self, *, strategy: Optional[str] = None, include_expenses: bool = True) -> str:
        return _reports.render_text(
            self.group,
            config=self.config,
            strategy=strategy or self.config.settle_strategy,
            include_expenses=include_expenses,
        )

    def render_markdown(self, *, strategy: Optional[str] = None, include_expenses: bool = True) -> str:
        return _reports.render_markdown(
            self.group,
            config=self.config,
            strategy=strategy or self.config.settle_strategy,
            include_expenses=include_expenses,
        )

    # ---- agent-style entry point ------------------------------------------

    def answer(self, question: str = "who owes what") -> Dict[str, Any]:
        """Return an intent-tagged answer for a short natural request.

        Deliberately keyword-based rather than an LLM call: the request space is
        tiny and known, and a deterministic router means the same question
        always yields the same answer, offline, with no key.
        """
        q = (question or "").strip().lower()
        persona = load_persona(self.config.persona_file or None)

        if any(k in q for k in ("square", "settled", "nothing to settle")):
            plan = self.settle()
            return {
                "intent": "status",
                "text": (
                    "Everyone is square — nothing to settle."
                    if not plan["transfers"]
                    else f"Not settled yet: {plan['transfer_count']} transfer(s) remain."
                ),
                "data": {"settled": not plan["transfers"]},
            }

        if any(k in q for k in ("settle", "pay", "transfer", "who pays", "square up", "trip close")):
            plan = self.settle()
            if not plan["transfers"]:
                text = persona["phrases"]["balanced"][0]
            else:
                lines = plan["readable"]
                head = persona["phrases"]["settle"][0].format(
                    n=plan["transfer_count"], s="" if plan["transfer_count"] == 1 else "s"
                )
                saved = ""
                if plan["transfers_saved"]:
                    saved = "\n" + persona["phrases"]["saved"][0].format(
                        saved=plan["transfers_saved"],
                        s="" if plan["transfers_saved"] == 1 else "s",
                    )
                text = head + "\n" + "\n".join(lines) + saved
            return {"intent": "settle", "text": text, "data": plan}

        if any(k in q for k in ("balance", "who owes", "net", "position", "summary", "status")):
            rows = self.balance_table()
            exp = exponent_for(self.group.currency)
            lines = []
            for r in rows:
                if r["state"] == "owed":
                    lines.append(f"{r['name']} is owed {format_amount(r['balance_minor'], exp)}")
                elif r["state"] == "owes":
                    lines.append(f"{r['name']} owes {format_amount(-r['balance_minor'], exp)}")
            text = "\n".join(lines) if lines else persona["phrases"]["balanced"][0]
            return {"intent": "balances", "text": text, "data": {"rows": rows}}

        if any(k in q for k in ("category", "breakdown", "where did", "spend", "chart")):
            report = self.report(include_expenses=False)
            return {
                "intent": "categories",
                "text": "\n".join(
                    f"{c['icon']} {c['category']}: {c['amount_display']} ({c['share_of_total']:.1f}%)"
                    for c in report["categories"]
                )
                or "No expenses recorded yet.",
                "data": {"categories": report["categories"]},
            }

        if any(k in q for k in ("report", "everything", "full", "export", "print")):
            return {
                "intent": "report",
                "text": self.render_text(),
                "data": self.report(),
            }

        # Default: describe the group and its current position.
        s = self.summary()
        return {
            "intent": "overview",
            "text": (
                f"{self.group.name}: {s['expense_count']} expense"
                f"{'s' if s['expense_count'] != 1 else ''} totalling {s['total_display']} "
                f"across {s['member_count']} member"
                f"{'s' if s['member_count'] != 1 else ''}. "
                + ("Everyone is square." if s["is_settled"]
                   else f"{s['owe_count']} member(s) owe {s['tense_count']} member(s).")
            ),
            "data": s,
        }


def group_name(group: Group, member_id: str) -> str:
    """Member display name, falling back to the id for a stray reference."""
    try:
        return group.name_of(member_id)
    except SplitKitError:
        return member_id


def describe_modes() -> Dict[str, str]:
    """Split-mode reference, exposed by the CLI and the API."""
    return dict(MODE_DESCRIPTIONS)


def currency_reference() -> Dict[str, Any]:
    """Exponents and symbols for the currencies SplitKit knows."""
    from .money import CURRENCY_EXPONENTS, DEFAULT_EXPONENT

    return {
        "default_exponent": DEFAULT_EXPONENT,
        "exponents": dict(sorted(CURRENCY_EXPONENTS.items())),
        "symbols": dict(sorted(SYMBOLS.items())),
        "count": len(CURRENCY_EXPONENTS),
    }


__all__ = ["SplitKit", "group_name", "describe_modes", "currency_reference", "MODES"]

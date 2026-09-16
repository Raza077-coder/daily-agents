"""SplitKit — a deterministic shared-expense splitter and settle-up agent.

Quick start::

    from splitkit import SplitKit

    kit = SplitKit.create("Goa Trip", ["ali", "sara", "bilal"])
    kit.add_expense("Beach hut", "180.00", "ali")
    kit.add_expense("Dinner", "92.40", "sara", split={"mode": "shares", "shares": {"ali": 2, "sara": 1, "bilal": 1}})

    print(kit.render_text())     # full report
    print(kit.settle()["readable"])  # who pays whom

Money never touches a float: every amount is an integer count of minor units
and every split is guaranteed to sum exactly to the expense total.
"""

from .config import Config, load_config, load_persona
from .engine import SplitKit, describe_modes, currency_reference
from .errors import (
    MoneyError,
    SettleError,
    SplitError,
    SplitKitError,
    UnknownExpenseError,
    UnknownMemberError,
    ValidationError,
)
from .models import Expense, Group, Member, Settlement
from .money import (
    CURRENCY_EXPONENTS,
    allocate,
    exponent_for,
    format_amount,
    format_for,
    parse_amount,
    parse_percent,
    split_evenly,
)
from .splits import MODES, MODE_DESCRIPTIONS, describe, resolve

__version__ = "1.0.0"
__all__ = [
    "SplitKit",
    "Config",
    "Group",
    "Member",
    "Expense",
    "Settlement",
    "load_config",
    "load_persona",
    "describe_modes",
    "currency_reference",
    "MODES",
    "MODE_DESCRIPTIONS",
    "resolve",
    "describe",
    "allocate",
    "split_evenly",
    "parse_amount",
    "parse_percent",
    "format_amount",
    "format_for",
    "exponent_for",
    "CURRENCY_EXPONENTS",
    "SplitKitError",
    "ValidationError",
    "MoneyError",
    "SplitError",
    "SettleError",
    "UnknownMemberError",
    "UnknownExpenseError",
    "__version__",
]

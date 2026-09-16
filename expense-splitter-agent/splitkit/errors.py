"""Exception hierarchy for SplitKit.

Everything SplitKit raises derives from :class:`SplitKitError`, and every
input problem also derives from ``ValueError`` so callers that only know
Python's built-ins still behave sensibly. That split matters for the API
layer: ``ValueError`` becomes HTTP 400 (the caller's data is wrong), while a
bare :class:`SplitKitError` becomes HTTP 500 (we have a bug).
"""

from __future__ import annotations


class SplitKitError(Exception):
    """Base class for every error SplitKit raises."""

    #: Short machine-readable label used by the CLI and the API error body.
    code = "splitkit_error"


class ValidationError(SplitKitError, ValueError):
    """Malformed input: bad JSON shape, unknown member, missing field."""

    code = "validation_error"


class MoneyError(ValidationError):
    """An amount could not be parsed, formatted, or allocated."""

    code = "money_error"


class SplitError(ValidationError):
    """A split spec is structurally valid but cannot be resolved.

    Example: exact amounts that do not sum to the expense total, percents that
    do not add to 100, or a participant who is not a group member.
    """

    code = "split_error"


class SettleError(SplitKitError, ValueError):
    """Settle-up could not run — normally a ledger that fails to net to zero."""

    code = "settle_error"


class UnknownMemberError(ValidationError):
    """A referenced member id is not part of the group."""

    code = "unknown_member"


class UnknownExpenseError(ValidationError):
    """A referenced expense id is not part of the group."""

    code = "unknown_expense"


__all__ = [
    "SplitKitError",
    "ValidationError",
    "MoneyError",
    "SplitError",
    "SettleError",
    "UnknownMemberError",
    "UnknownExpenseError",
]

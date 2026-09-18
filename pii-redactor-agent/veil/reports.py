"""Report rendering: terminal, Markdown, and JSON.

One renderer per audience:

- **terminal** for a human reading a scan before deciding what to do;
- **markdown** for attaching to a ticket or a pull request;
- **json** for a pipeline, with ``include_values`` deciding whether the raw
  values are written down. Redacting a file and then printing every removed
  value into a CI log would defeat the exercise, so the CLI defaults to
  ``include_values=False`` for the file it is about to share.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from . import actions as actions_module
from .models import ENTITY_LABELS, Finding, RedactionResult

LEVEL_MARK = {
    "CRITICAL": "\u2588\u2588\u2588\u2588\u2588",
    "HIGH": "\u2588\u2588\u2588\u2588\u2591",
    "MODERATE": "\u2588\u2588\u2588\u2591\u2591",
    "LOW": "\u2588\u2588\u2591\u2591\u2591",
    "NONE": "\u2591\u2591\u2591\u2591\u2591",
}

#: Continuation glyphs. Named constants rather than inline escapes because an
#: f-string expression may not contain a backslash (Python < 3.12).
BRANCH_MARK = "\u21b3"
NOTE_MARK = "\u00b7"
EMPTY_CELL = ""


def _bar(level: str) -> str:
    return LEVEL_MARK.get(level, "\u2591\u2591\u2591\u2591\u2591")


def render_terminal(result: RedactionResult, *, show_values: bool = True,
                    show_offsets: bool = False, width: int = 78) -> str:
    """Human-readable summary for a terminal."""
    rule = "\u2500" * width
    lines = [rule, "VEIL \u2014 PII redaction report", rule]

    risk = result.risk
    if risk:
        lines.append(
            f"Risk    {_bar(risk.level)}  {risk.level}  (score {risk.score})"
        )
        lines.append(
            f"Found   {risk.distinct_findings} distinct value(s) "
            f"across {risk.total_occurrences} occurrence(s)"
        )
    lines.append(f"Policy  {result.policy_name}")

    verification = result.verification
    if verification:
        mark = "\u2713" if verification.clean else "\u2717"
        detail = (
            "no detectable value survived"
            if verification.clean
            else f"{len(verification.residuals)} residual value(s) still present"
        )
        lines.append(f"Verify  {mark} {verification.status} \u2014 {detail}")
        if verification.preserved:
            lines.append(
                f"        {len(verification.preserved)} value(s) deliberately kept "
                f"by the allowlist"
            )
    lines.append("")

    if not result.findings:
        lines.append("Nothing detected. The text is clean under this policy.")
        lines.append(rule)
        return "\n".join(lines)

    lines.append(f"{'ENTITY':<15}{'ACTION':<10}{'N':>3}  VALUE")
    lines.append("-" * width)
    for finding in result.findings:
        value = finding.value if show_values else "(withheld)"
        if len(value) > 34:
            value = value[:31] + "..."
        label = finding.entity
        lines.append(f"{label:<15}{finding.action:<10}{finding.count:>3}  {value}")
        if not finding.preserved and finding.replacement != finding.value:
            replacement = finding.replacement or "(removed)"
            if len(replacement) > 34:
                replacement = replacement[:31] + "..."
            lines.append(
                f"{EMPTY_CELL:<15}{BRANCH_MARK:<10}{EMPTY_CELL:>3}  {replacement}"
            )
        if show_offsets:
            spans = ", ".join(f"{s}-{e}" for s, e in finding.offsets[:4])
            more = "" if len(finding.offsets) <= 4 else f" (+{len(finding.offsets) - 4} more)"
            lines.append(f"{'':<15}{'@':<10}{'':>3}  {spans}{more}")
        if finding.note:
            lines.append(f"{EMPTY_CELL:<15}{NOTE_MARK:<10}{EMPTY_CELL:>3}  {finding.note}")

    lines.append(rule)
    return "\n".join(lines)


def render_markdown(result: RedactionResult, *, show_values: bool = True) -> str:
    """Markdown report, suitable for a ticket or a pull request body."""
    risk = result.risk
    lines = ["# VEIL redaction report", ""]

    if risk:
        lines.append(f"**Risk:** {risk.level} (score {risk.score})  ")
        lines.append(
            f"**Found:** {risk.distinct_findings} distinct value(s) across "
            f"{risk.total_occurrences} occurrence(s)  "
        )
    lines.append(f"**Policy:** `{result.policy_name}`  ")

    verification = result.verification
    if verification:
        mark = "\u2705" if verification.clean else "\u274c"
        detail = (
            "no detectable value survived"
            if verification.clean
            else f"{len(verification.residuals)} residual value(s) still present"
        )
        lines.append(f"**Verification:** {mark} `{verification.status}` \u2014 {detail}  ")
    lines.append("")

    if not result.findings:
        lines.append("Nothing detected. The text is clean under this policy.")
        return "\n".join(lines)

    lines.append("## Findings")
    lines.append("")
    lines.append("| Entity | Action | Count | Value | Replacement |")
    lines.append("| --- | --- | ---: | --- | --- |")
    for finding in result.findings:
        value = _escape(finding.value) if show_values else "_(withheld)_"
        replacement = _escape(finding.replacement) if finding.replacement else "_(removed)_"
        if finding.preserved:
            replacement = "_(kept)_"
        lines.append(
            f"| `{finding.entity}` | `{finding.action}` | {finding.count} | "
            f"`{value}` | `{replacement}` |"
        )
    lines.append("")

    if risk and risk.by_entity:
        lines.append("## Occurrences by entity")
        lines.append("")
        lines.append("| Entity | Label | Occurrences |")
        lines.append("| --- | --- | ---: |")
        for entity, count in risk.by_entity.items():
            lines.append(f"| `{entity}` | {ENTITY_LABELS.get(entity, entity)} | {count} |")
        lines.append("")

    if verification and verification.residuals:
        lines.append("## \u26a0\ufe0f Residuals")
        lines.append("")
        lines.append("These were still detectable in the output and need attention:")
        lines.append("")
        for residual in verification.residuals:
            lines.append(f"- `{residual['entity']}` \u2014 {_escape(str(residual['value']))}")
        lines.append("")

    return "\n".join(lines)


def render_json(result: RedactionResult, *, include_values: bool = True,
                indent: Optional[int] = 2) -> str:
    """Machine-readable report.

    ``redacted`` is populated only when the result actually came from a redaction
    (``redacted_emitted`` is true). A scan-only result leaves the original text
    untouched, and echoing it back under a key named "redacted" would hand the
    caller a file they believe is clean while it is still in the clear.
    """
    return json.dumps(
        result.to_dict(include_values=include_values),
        indent=indent,
        sort_keys=False,
        ensure_ascii=False,
    )


def render_diff(original: str, redacted: str, context: int = 1,
                max_lines: int = 60) -> str:
    """A line-oriented before/after, trimmed to the lines that changed."""
    import difflib

    before = original.splitlines()
    after = redacted.splitlines()
    lines = list(difflib.unified_diff(before, after, "original", "redacted",
                                      lineterm="", n=context))
    if not lines:
        return "No differences (nothing was changed)."
    if len(lines) > max_lines:
        shown = lines[:max_lines]
        shown.append(f"... {len(lines) - max_lines} more line(s)")
        lines = shown
    return "\n".join(lines)


def render_reference() -> str:
    """The entity + action reference tables, generated from the code."""
    from .models import RISK_WEIGHTS, ENTITY_VALIDATORS, OPT_IN_ENTITIES
    from .detectors import DETECTOR_ORDER

    lines = ["ENTITIES", "=" * 78]
    for entity in DETECTOR_ORDER:
        opt_in = " (opt-in)" if entity in OPT_IN_ENTITIES else ""
        lines.append(f"{entity}{opt_in}  \u2014  risk weight {RISK_WEIGHTS[entity]}")
        lines.append(f"    {ENTITY_VALIDATORS[entity]}")
    lines.append("")
    lines.append("ACTIONS")
    lines.append("=" * 78)
    for row in actions_module.describe_actions():
        reversible = "reversible" if row["reversible"] else "irreversible"
        lines.append(f"{row['action']}  ({reversible})")
        lines.append(f"    {row['summary']}")
        lines.append(f"    e.g. {row['example']}")
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("`", "\\`").replace("\n", " ")

"""VAULTGUARD security audit.

Scans a list of entries and produces a weighted, explainable hygiene score.
Deterministic and fully offline — nothing is ever sent to a breach-check API.

Scoring model (100 points, deductions capped per category):

===========================  ======  ==========================================
Category                     Weight  Trigger
===========================  ======  ==========================================
Weak passwords                  35   entropy score < 55
Reused passwords                25   the same secret on 2+ entries
Stale passwords                 15   older than ``stale_days`` (default 365)
Missing two-factor              10   no TOTP secret recorded
Duplicate titles                 5   two entries share a title
Missing username / URL           5   login entries with no identifier
Old vault / hygiene               5   nothing rotated in the last year
===========================  ======  ==========================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .models import VaultEntry, days_since
from .strength import estimate_strength

WEAK_THRESHOLD = 55
REASONABLE_THRESHOLD = 70
STALE_DAYS = 365

WEIGHTS = {
    "weak": 35,
    "reused": 25,
    "stale": 15,
    "no_2fa": 10,
    "duplicate_title": 5,
    "missing_identifier": 5,
    "rotation": 5,
}


@dataclass
class AuditFinding:
    """One problem found on one entry."""

    kind: str
    severity: str  # critical | high | medium | low
    title: str
    detail: str
    entry_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "entry_id": self.entry_id,
        }


@dataclass
class AuditReport:
    """Full audit result."""

    score: int = 0
    grade: str = "F"
    verdict: str = "critical"
    total_entries: int = 0
    health: Dict[str, int] = field(default_factory=dict)
    findings: List[AuditFinding] = field(default_factory=list)
    deductions: Dict[str, int] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "verdict": self.verdict,
            "total_entries": self.total_entries,
            "health": self.health,
            "deductions": self.deductions,
            "findings": [f.to_dict() for f in self.findings],
            "recommendations": self.recommendations,
            "counts": self._counts(),
        }

    def _counts(self) -> Dict[str, int]:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    def render(self) -> str:
        """Human-readable digest (used by the CLI)."""
        lines = [
            "VAULTGUARD SECURITY AUDIT",
            "=" * 58,
            f"Vault health : {self.score}/100  ({self.grade} · {self.verdict})",
            f"Entries      : {self.total_entries}",
            f"Strong       : {self.health.get('strong', 0)}",
            f"Reasonable   : {self.health.get('reasonable', 0)}",
            f"Weak         : {self.health.get('weak', 0)}",
            f"Reused       : {self.health.get('reused', 0)}",
            f"Stale        : {self.health.get('stale', 0)}",
            f"With 2FA     : {self.health.get('with_2fa', 0)}",
            "",
        ]
        if self.deductions:
            lines.append("Deductions")
            lines.append("-" * 58)
            for kind, points in sorted(self.deductions.items(), key=lambda kv: -kv[1]):
                lines.append(f"  -{points:>3}  {kind}")
            lines.append("")

        if self.findings:
            lines.append("Findings")
            lines.append("-" * 58)
            order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for finding in sorted(self.findings, key=lambda f: (order.get(f.severity, 9), f.title)):
                lines.append(f"  [{finding.severity.upper():<8}] {finding.title}")
                lines.append(f"             {finding.detail}")
            lines.append("")
        else:
            lines.append("No findings. Outstanding hygiene.")
            lines.append("")

        if self.recommendations:
            lines.append("Recommendations")
            lines.append("-" * 58)
            for rec in self.recommendations:
                lines.append(f"  • {rec}")
        return "\n".join(lines)


class VaultAuditor:
    """Runs the hygiene rules over a set of entries."""

    def __init__(
        self,
        *,
        stale_days: int = STALE_DAYS,
        weak_threshold: int = WEAK_THRESHOLD,
    ) -> None:
        self.stale_days = stale_days
        self.weak_threshold = weak_threshold

    # -- public ------------------------------------------------------------ #
    def audit(self, entries: Sequence[VaultEntry]) -> AuditReport:
        entries = list(entries or [])
        report = AuditReport(total_entries=len(entries))
        if not entries:
            report.grade, report.verdict = "F", "empty"
            report.recommendations = ["Add your first credential to begin tracking hygiene."]
            return report

        findings: List[AuditFinding] = []
        health = {
            "strong": 0,
            "reasonable": 0,
            "weak": 0,
            "reused": 0,
            "stale": 0,
            "with_2fa": 0,
        }

        # -- password reuse via fingerprints ------------------------------- #
        by_hint: Dict[str, List[VaultEntry]] = {}
        for entry in entries:
            if entry.password_hint:
                by_hint.setdefault(entry.password_hint, []).append(entry)

        reused_ids = set()
        for hint, group in by_hint.items():
            if len(group) > 1:
                titles = ", ".join(sorted(e.title for e in group)[:4])
                for entry in group:
                    reused_ids.add(entry.id)
                findings.append(
                    AuditFinding(
                        kind="reused",
                        severity="critical",
                        title=f"Password reused across {len(group)} entries",
                        detail=f"Shared by: {titles}. One breach exposes all of them.",
                        entry_id=group[0].id,
                    )
                )

        # -- per-entry rules ----------------------------------------------- #
        title_counts: Dict[str, int] = {}
        for entry in entries:
            title_counts[entry.title.strip().lower()] = title_counts.get(entry.title.strip().lower(), 0) + 1

        for entry in entries:
            age = days_since(entry.password_updated_at) or 0
            reused = entry.id in reused_ids
            strength = estimate_strength(
                entry.password,
                age_days=age,
                reused=reused,
            )

            if strength.score >= 80:
                health["strong"] += 1
            elif strength.score >= self.weak_threshold:
                health["reasonable"] += 1
            else:
                health["weak"] += 1
                findings.append(
                    AuditFinding(
                        kind="weak",
                        severity="high" if strength.score >= 40 else "critical",
                        title=f"Weak password on '{entry.title}'",
                        detail=(
                            f"Score {strength.score}/100 ({strength.label}); "
                            f"cracked in {strength.crack_time_human}. "
                            + (strength.suggestions[0] if strength.suggestions else "")
                        ),
                        entry_id=entry.id,
                    )
                )

            if reused:
                health["reused"] += 1

            if age > self.stale_days:
                health["stale"] += 1
                findings.append(
                    AuditFinding(
                        kind="stale",
                        severity="medium",
                        title=f"'{entry.title}' password is {age} days old",
                        detail=f"Rotate anything older than {self.stale_days} days.",
                        entry_id=entry.id,
                    )
                )

            if entry.totp:
                health["with_2fa"] += 1
            elif entry.category in ("login", "email", "banking", "work", "server", "api"):
                findings.append(
                    AuditFinding(
                        kind="no_2fa",
                        severity="low",
                        title=f"No two-factor secret for '{entry.title}'",
                        detail="Store a TOTP seed or enable 2FA at the provider.",
                        entry_id=entry.id,
                    )
                )

            if title_counts.get(entry.title.strip().lower(), 0) > 1:
                findings.append(
                    AuditFinding(
                        kind="duplicate_title",
                        severity="low",
                        title=f"Duplicate title '{entry.title}'",
                        detail="Give each entry a distinct title so lookups stay unambiguous.",
                        entry_id=entry.id,
                    )
                )

            if not entry.username and not entry.url and entry.category not in ("wifi", "secure-note"):
                findings.append(
                    AuditFinding(
                        kind="missing_identifier",
                        severity="low",
                        title=f"'{entry.title}' has no username or URL",
                        detail="Record the account identifier so the entry is actionable.",
                        entry_id=entry.id,
                    )
                )

        # -- vault-level rotation ------------------------------------------- #
        if all((days_since(e.password_updated_at) or 0) > self.stale_days for e in entries):
            findings.append(
                AuditFinding(
                    kind="rotation",
                    severity="medium",
                    title="Nothing has been rotated in over a year",
                    detail="Run a rotation sweep across the vault.",
                )
            )

        # -- scoring -------------------------------------------------------- #
        deductions: Dict[str, int] = {}
        by_kind: Dict[str, List[AuditFinding]] = {}
        for finding in findings:
            by_kind.setdefault(finding.kind, []).append(finding)

        for kind, weight in WEIGHTS.items():
            group = by_kind.get(kind, [])
            if not group:
                continue
            ratio = len(group) / max(len(entries), 1)
            deductions[kind] = max(1, int(round(weight * min(1.0, ratio))))

        score = max(0, 100 - sum(deductions.values()))
        report.score = score
        report.grade, report.verdict = _grade(score)
        report.findings = findings
        report.deductions = deductions
        report.health = health
        report.recommendations = _recommendations(findings, health, len(entries))
        return report


def _grade(score: int) -> tuple:
    if score >= 95:
        return "A+", "pristine"
    if score >= 90:
        return "A", "excellent"
    if score >= 80:
        return "B", "good"
    if score >= 65:
        return "C", "needs attention"
    if score >= 50:
        return "D", "poor"
    return "F", "critical"


def _recommendations(
    findings: Sequence[AuditFinding], health: Dict[str, int], total: int
) -> List[str]:
    recs: List[str] = []
    if health.get("reused"):
        recs.append(
            f"Break up {health['reused']} reused password(s) — start with your email and banking logins."
        )
    if health.get("weak"):
        recs.append(
            f"Replace {health['weak']} weak password(s) using the built-in generator (20+ characters)."
        )
    if health.get("stale"):
        recs.append(f"Rotate {health['stale']} password(s) that are over a year old.")
    if total and health.get("with_2fa", 0) == 0:
        recs.append("Enable two-factor authentication and store the TOTP seed for each account.")
    if any(f.kind == "duplicate_title" for f in findings):
        recs.append("Rename duplicate entries so each account is uniquely identifiable.")
    if not recs:
        recs.append("Vault is in great shape. Keep rotating on a yearly cadence.")
    return recs


def quick_score(entries: Sequence[VaultEntry]) -> int:
    """Convenience accessor returning just the 0-100 score."""
    return VaultAuditor().audit(entries).score

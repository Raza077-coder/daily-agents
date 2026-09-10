"""VAULTGUARD password strength estimation.

Deterministic, offline, and explainable — no network call to any breach API.

Entropy model
    ``H = L * log2(pool_size)``   when the password is purely random-looking.
    Repetition, sequences, keyboard walks, dates and dictionary words are then
    penalised multiplicatively so a memorable-but-weak secret scores low even
    when long.

Crack time
    ``seconds = 2 ** H / guesses_per_second`` for an offline-attack budget
    configured with ``guesses_per_second`` (default 10^10 ≈ a modern GPU rig
    against a fast hash).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = LOWER.upper()
DIGITS = "0123456789"
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/\\|~`'\"<>"

# gitleaks:allow — this is the *dictionary* the scorer penalises, not a credential
COMMON_PASSWORDS: Sequence[str] = (
    "password", "123456", "123456789", "qwerty", "abc123", "letmein", "welcome",
    "monkey", "dragon", "football", "iloveyou", "admin", "login", "master",
    "sunshine", "princess", "trustno1", "qwerty123", "passw0rd", "zaq12wsx",
    "baseball", "superman", "batman", "starwars", "freedom", "whatever", "ninja",
    "shadow", "michael", "jennifer", "hunter2", "changeme", "secret", "root",
)

KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")
SEQUENCE_PATTERNS = ("abcdefghijklmnopqrstuvwxyz", "0123456789")


@dataclass
class StrengthReport:
    """Explainable strength verdict for one password."""

    password_length: int = 0
    pool_size: int = 0
    entropy_bits: float = 0.0
    score: int = 0
    grade: str = "F"
    label: str = "very weak"
    crack_time_seconds: float = 0.0
    crack_time_human: str = "instantly"
    character_classes: List[str] = field(default_factory=list)
    penalties: List[Dict[str, object]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    is_common: bool = False
    reused: bool = False
    age_days: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "length": self.password_length,
            "pool_size": self.pool_size,
            "entropy_bits": round(self.entropy_bits, 1),
            "score": self.score,
            "grade": self.grade,
            "label": self.label,
            "crack_time_seconds": self.crack_time_seconds,
            "crack_time_human": self.crack_time_human,
            "character_classes": self.character_classes,
            "penalties": self.penalties,
            "suggestions": self.suggestions,
            "is_common": self.is_common,
            "reused": self.reused,
            "age_days": self.age_days,
        }


def character_pool(password: str) -> Tuple[int, List[str]]:
    """Return ``(pool_size, class_names)`` for the password."""
    classes: List[str] = []
    pool = 0
    if any(c in LOWER for c in password):
        pool += len(LOWER)
        classes.append("lowercase")
    if any(c in UPPER for c in password):
        pool += len(UPPER)
        classes.append("uppercase")
    if any(c in DIGITS for c in password):
        pool += len(DIGITS)
        classes.append("digits")
    if any(c in SYMBOLS for c in password):
        pool += len(SYMBOLS)
        classes.append("symbols")
    if any(ord(c) > 127 for c in password):
        pool += 128
        classes.append("unicode")
    return max(pool, 1), classes


def _repetition_penalty(password: str) -> Tuple[float, int]:
    """Penalise runs such as ``aaa`` and repeated blocks such as ``abcabc``."""
    penalty = 1.0
    runs = re.findall(r"(.)\1{2,}", password)
    if runs:
        penalty *= 0.6 ** len(runs)
    for size in (1, 2, 3, 4):
        block = password[:size]
        if size and len(password) >= size * 3 and password == block * (len(password) // size):
            penalty *= 0.5
            break
    return penalty, len(runs)


def _sequence_penalty(password: str) -> Tuple[float, int]:
    """Penalise alphabetic/numeric runs and keyboard walks."""
    lowered = password.lower()
    hits = 0
    for seq in SEQUENCE_PATTERNS:
        for size in range(4, len(seq) + 1):
            for start in range(0, len(seq) - size + 1):
                chunk = seq[start : start + size]
                if len(chunk) >= 4 and (chunk in lowered or chunk[::-1] in lowered):
                    hits += 1
    for row in KEYBOARD_ROWS:
        for size in range(4, len(row) + 1):
            for start in range(0, len(row) - size + 1):
                chunk = row[start : start + size]
                if len(chunk) >= 4 and chunk in lowered:
                    hits += 1
    return (0.7 ** hits if hits else 1.0), hits


def _dictionary_penalty(password: str) -> Tuple[float, List[str]]:
    """Penalise common passwords and bare dictionary words."""
    lowered = password.lower()
    found: List[str] = []
    for candidate in COMMON_PASSWORDS:
        if candidate in lowered:
            found.append(candidate)
    return (0.25 ** len(found) if found else 1.0), found


def _date_penalty(password: str) -> Tuple[float, int]:
    """Penalise years and date-like fragments."""
    hits = len(re.findall(r"(19|20)\d{2}", password))
    hits += len(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", password))
    return (0.8 ** hits if hits else 1.0), hits


def humanise_duration(seconds: float) -> str:
    """Render a crack time as a human sentence."""
    if seconds < 1:
        return "instantly"
    units = (
        ("century", "centuries", 60 * 60 * 24 * 365 * 100),
        ("year", "years", 60 * 60 * 24 * 365),
        ("month", "months", 60 * 60 * 24 * 30),
        ("day", "days", 60 * 60 * 24),
        ("hour", "hours", 60 * 60),
        ("minute", "minutes", 60),
        ("second", "seconds", 1),
    )
    for singular, plural, size in units:
        if seconds >= size:
            value = seconds / size
            unit = singular if 1 <= value < 2 else plural
            return f"{value:,.1f} {unit}".replace(".0 ", " ")
    return "instantly"


def _grade_for(score: int) -> Tuple[str, str]:
    if score >= 90:
        return "A+", "fortress"
    if score >= 80:
        return "A", "very strong"
    if score >= 70:
        return "B", "strong"
    if score >= 55:
        return "C", "reasonable"
    if score >= 40:
        return "D", "weak"
    return "F", "very weak"


def estimate_strength(
    password: str,
    *,
    guesses_per_second: float = 1e10,
    age_days: int = 0,
    reused: bool = False,
) -> StrengthReport:
    """Estimate strength, penalty-aware and fully deterministic."""
    report = StrengthReport(password_length=len(password or ""), age_days=age_days, reused=reused)
    if not password:
        report.suggestions.append("Set a password for this entry.")
        report.grade, report.label = "F", "empty"
        return report

    pool, classes = character_pool(password)
    report.pool_size = pool
    report.character_classes = classes

    base_entropy = len(password) * math.log2(pool)
    penalty = 1.0

    dup_factor, dup_hits = _repetition_penalty(password)
    if dup_hits:
        penalty *= dup_factor
        report.penalties.append({"reason": "repeated characters", "hits": dup_hits})

    seq_factor, seq_hits = _sequence_penalty(password)
    if seq_hits:
        penalty *= seq_factor
        report.penalties.append({"reason": "predictable sequence", "hits": seq_hits})

    dict_factor, words = _dictionary_penalty(password)
    if words:
        penalty *= dict_factor
        report.penalties.append({"reason": "dictionary word", "hits": len(words), "matched": words[:3]})

    date_factor, date_hits = _date_penalty(password)
    if date_hits:
        penalty *= date_factor
        report.penalties.append({"reason": "date-like fragment", "hits": date_hits})

    if len(set(password)) <= max(3, len(password) // 4):
        penalty *= 0.7
        report.penalties.append({"reason": "tiny character variety", "hits": len(set(password))})

    entropy = base_entropy * penalty
    report.entropy_bits = entropy
    report.is_common = any(c in password.lower() for c in COMMON_PASSWORDS)

    # Map entropy to a 0-100 score: 30 bits -> 0, 100 bits -> 100 (linear clip)
    score = int(max(0, min(100, (entropy - 30) * (100 / 70))))
    if reused:
        score = int(score * 0.55)
    if age_days > 365:
        score = int(score * 0.9)
    report.score = score
    report.grade, report.label = _grade_for(score)

    seconds = (2 ** entropy) / max(guesses_per_second, 1.0)
    report.crack_time_seconds = seconds
    report.crack_time_human = humanise_duration(seconds)

    # -- guidance ---------------------------------------------------------- #
    if len(password) < 16:
        report.suggestions.append("Use at least 16 characters.")
    if "uppercase" not in classes or "lowercase" not in classes:
        report.suggestions.append("Mix upper and lower case letters.")
    if "digits" not in classes:
        report.suggestions.append("Add digits.")
    if "symbols" not in classes:
        report.suggestions.append("Add a symbol or two.")
    if report.is_common:
        report.suggestions.append("Replace dictionary words with a random passphrase.")
    if reused:
        report.suggestions.append("Reused password — give this account a unique secret.")
    if age_days > 365:
        report.suggestions.append(f"Rotate this password (last changed {age_days} days ago).")
    if not report.suggestions:
        report.suggestions.append("Nothing to improve. Excellent hygiene.")
    return report


def score_password(password: str, **kwargs) -> int:
    """Convenience accessor returning only the 0-100 score."""
    return estimate_strength(password, **kwargs).score

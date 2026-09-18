"""Detectors.

Every detector is two stages: a permissive **regex** finds candidates, then a
**validator** decides whether a candidate is real. The split matters because the
entities that matter most (cards, IBANs, SSNs, IPv4) are defined by arithmetic or
issuance rules, not by shape \u2014 a 16-digit run is not a card unless it passes
Luhn, and a `GB..` string is not an IBAN unless it satisfies mod-97.

That is why a card typo is *not* reported: VEIL would rather miss an invalid
number than redact a random 16-digit order id, and it never guesses.

Detection is pure: no clock, no randomness, no network. The same text always
yields the same spans in the same order.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .models import Span

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

PHONE_CONTEXT: Sequence[str] = (
    "phone", "telephone", "tel", "mobile", "cell", "cellphone", "whatsapp",
    "call", "fax", "contact", "hotline", "dial", "sms", "number",
)

BIRTH_CONTEXT: Sequence[str] = (
    "dob", "d.o.b", "date of birth", "birthdate", "birth day", "birthday",
    "born", "yob", "birth", "naissance", "geburtsdatum",
)

#: Values that look like secrets but are obviously placeholders. Flagging these
#: fills reports with noise and trains people to ignore them.
PLACEHOLDER_VALUES: Set[str] = {
    "changeme", "change_me", "change-me", "changes_me", "your_api_key",
    "yourapikey", "your-key-here", "your_token_here", "your-password",
    "your_password", "replace_me", "replaceme", "replace-me", "replace",
    "insert_key_here", "xxx", "xxxx", "todo", "none", "null", "true",
    "false", "example", "password", "secret", "token", "undefined",
    "placeholder", "dummy", "redacted", "removed", "string", "value",
    "here", "notset", "unset", "n/a", "na", "empty", "<your-key>",
}

#: Common given names used by the opt-in PERSON detector. Kept deliberately
#: small and unambiguous; anything beyond this belongs in policy `names`.
FIRST_NAMES: Sequence[str] = (
    "aaron", "adam", "adrian", "ahmed", "aisha", "alex", "alexander", "alice",
    "ali", "amanda", "amelia", "amy", "andrew", "angela", "anna", "anthony",
    "arthur", "ashley", "austin", "benjamin", "beverly", "bilal", "brandon",
    "brenda", "brian", "bruce", "caleb", "cameron", "carla", "carlos",
    "carmen", "carol", "catherine", "charles", "charlotte", "cheryl", "chloe",
    "christina", "christopher", "claire", "clara", "colin", "connor", "craig",
    "cynthia", "daniel", "danielle", "david", "dawn", "deborah", "denise",
    "dennis", "derek", "diana", "diego", "dmitri", "donald", "donna", "dorothy",
    "douglas", "duncan", "edward", "elena", "elizabeth", "emily", "emma",
    "eric", "erica", "ethan", "eugene", "evelyn", "fatima", "felix", "fiona",
    "frances", "frank", "gabriel", "gareth", "gary", "george", "gerald",
    "gina", "gloria", "grace", "graham", "gregory", "hannah", "harold",
    "hassan", "hayley", "heather", "helen", "henry", "hillary", "holly",
    "hugo", "ian", "ibrahim", "irene", "isaac", "isabel", "ivan", "jack",
    "jacob", "james", "jamie", "janet", "jason", "javier", "jeffrey", "jennifer",
    "jeremy", "jessica", "joan", "joel", "john", "jonathan", "jordan", "jose",
    "joseph", "joshua", "joyce", "julia", "julian", "julie", "justin", "karen",
    "katherine", "kathleen", "katie", "keith", "kelly", "kenneth", "kevin",
    "kimberly", "kiran", "larry", "laura", "lauren", "lawrence", "leah",
    "leonard", "leslie", "liam", "lily", "linda", "lisa", "logan", "lorenzo",
    "louis", "lucas", "lucy", "lydia", "madison", "margaret", "maria",
    "marie", "marilyn", "mark", "martha", "martin", "mary", "matthew",
    "maureen", "megan", "melissa", "michael", "michelle", "miguel", "mohamed",
    "mohammed", "molly", "monica", "muhammad", "mustafa", "nadia", "nancy",
    "naomi", "natalie", "nathan", "neil", "nicholas", "nicole", "nigel",
    "noah", "nora", "oliver", "olivia", "omar", "oscar", "owen", "pamela",
    "patricia", "patrick", "paul", "paula", "peter", "philip", "phillip",
    "rachel", "ralph", "raymond", "rebecca", "rehan", "richard", "robert",
    "roberto", "roger", "roland", "ronald", "rosa", "rose", "ross", "ruby",
    "russell", "ruth", "ryan", "saeed", "sally", "samuel", "sandra", "sara",
    "sarah", "scott", "sean", "simon", "sofia", "sonia", "sophia", "stanley",
    "stella", "stephen", "steven", "stuart", "susan", "sylvia", "tanya",
    "teresa", "terry", "theodore", "theresa", "thomas", "tiffany", "timothy",
    "tina", "tobias", "tom", "tony", "tracy", "travis", "trevor", "tyler",
    "usman", "valerie", "vanessa", "vera", "victor", "victoria", "vincent",
    "virginia", "walter", "warren", "wayne", "wendy", "wesley", "william",
    "willow", "yusuf", "zachary", "zainab", "zara", "zoe",
)

# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def luhn_valid(digits: str) -> bool:
    """Luhn (mod-10) checksum used by every major payment card.

    Doubles every second digit from the right; a digit above 9 has 9 subtracted.
    The number is well-formed when the total is divisible by 10.
    """
    if not digits or not digits.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iban_valid(raw: str) -> bool:
    """ISO 7064 mod-97-10 check used by IBANs.

    Moves the first four characters to the end, maps letters to 10-35, then
    requires the resulting integer's remainder modulo 97 to be exactly 1.
    """
    compact = re.sub(r"[\s\-]", "", raw).upper()
    if not 15 <= len(compact) <= 34:
        return False
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    remainder = 0
    for char in rearranged:
        chunk = char if char.isdigit() else str(ord(char) - 55)
        remainder = (remainder * (100 if len(chunk) == 2 else 10) + int(chunk)) % 97
    return remainder == 1


def ssn_valid(digits: str) -> bool:
    """US SSA issuance rules.

    The area cannot be 000, 666 or 900-999; the group cannot be 00; the serial
    cannot be 0000. ``123-45-6789`` is famously reserved as an example and is
    excluded too, because flagging it in a test fixture is pure noise.
    """
    if not re.fullmatch(r"\d{9}", digits):
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in ("000", "666"):
        return False
    if int(area) >= 900:
        return False
    if group == "00" or serial == "0000":
        return False
    if digits in ("123456789", "111111111", "000000000"):
        return False
    return True


def ipv4_valid(octets: Sequence[str]) -> bool:
    """Four groups, each 0-255."""
    if len(octets) != 4:
        return False
    for octet in octets:
        if not octet.isdigit():
            return False
        if len(octet) > 1 and octet.startswith("0"):
            return False
        if not 0 <= int(octet) <= 255:
            return False
    return True


def email_valid(value: str) -> bool:
    """Structural email check: sane local part, dotted domain, alphabetic TLD."""
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    if not local or len(local) > 64:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
    if not re.fullmatch(r"[A-Za-z]{2,24}", labels[-1]):
        return False
    return True


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_ipv4_shape(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value))


def _is_date_shape(value: str) -> bool:
    """True when a dashed/dotted number group is almost certainly a date.

    Without this guard ``2026-09-18`` trips the phone detector: eight digits and
    two separators is exactly the shape it looks for.
    """
    parts = re.split(r"[-/.]", value)
    if len(parts) != 3:
        return False
    if not all(p.isdigit() for p in parts):
        return False
    lengths = tuple(len(p) for p in parts)
    if lengths not in {(4, 2, 2), (2, 2, 4), (1, 2, 4), (2, 1, 4), (4, 1, 2), (4, 2, 1)}:
        return False
    nums = [int(p) for p in parts]
    if lengths[0] == 4:
        year, month, day = nums
    elif lengths[2] == 4:
        year, month, day = nums[2], nums[0], nums[1]
    else:
        year, month, day = nums[0], nums[1], nums[2]
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def _is_valid_date_parts(year: int, month: int, day: int) -> bool:
    return 1900 <= year <= 2026 and 1 <= month <= 12 and 1 <= day <= 31


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
)


def _window_before(text: str, start: int, size: int) -> str:
    return text[max(0, start - size):start].lower()


def _window_after(text: str, end: int, size: int) -> str:
    return text[end:end + size].lower()


def _mentions(haystack: str, words: Iterable[str]) -> bool:
    for word in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", haystack):
            return True
    return False


def _has_context(text: str, start: int, end: int, words: Iterable[str],
                 before: int = 32, after: int = 12) -> bool:
    return _mentions(_window_before(text, start, before), words) or _mentions(
        _window_after(text, end, after), words
    )


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

SECRET_PATTERNS: Sequence[tuple] = (
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("github_token", re.compile(r"(?<![\w])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})(?![\w])")),
    ("slack_token", re.compile(r"(?<![\w])xox[abprs]-[A-Za-z0-9-]{10,}(?![\w])")),
    ("stripe_key", re.compile(r"(?<![\w])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}(?![\w])")),
    ("jwt", re.compile(r"(?<![\w])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?![\w])")),
    ("pem_private_key", re.compile(
        r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z]+ )*PRIVATE KEY-----"
    )),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+([A-Za-z0-9_\-\.]{16,})")),
)

SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    # Optional word prefixes, so `db_password`, `my_api_key` and
    # `stripe_secret_key` are recognised, not just the bare keyword.
    r"(?:[A-Za-z0-9]{1,20}[_\-]){0,3}"
    r"(?:api[_-]?key|apikey|api[_-]?secret|access[_-]?key|secret[_-]?key|"
    r"client[_-]?secret|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"bearer[_-]?token|private[_-]?key|password|passwd|pwd|secret|token)"
    r"(?![A-Za-z0-9_])"
    # Horizontal space only: an assignment is always on one line. Allowing
    # \s here lets the match cross a newline and swallow the next line's key.
    r"[ \t]*[:=][ \t]*[\"']?([^\s\"'`,;<>{}$]{6,})[\"']?"
)

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}(?![\w.-])")

CARD_RE = re.compile(r"(?<![\w])(?:\d[ \-]?){12,18}\d(?![\w])")

IBAN_RE = re.compile(r"(?<![\w])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){2,8}(?![\w])")

SSN_RE = re.compile(r"(?<![\d\-])(\d{3})([ \-])(\d{2})\2(\d{4})(?![\d\-])")

NATIONAL_ID_RE = re.compile(r"(?<![\d\-])\d{5}-\d{7}-\d(?![\d\-])")

IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")

IPV6_RE = re.compile(
    r"(?<![\w:.])(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|"
    r"(?:[0-9A-Fa-f]{1,4}:){1,7}:|"
    r"(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}|"
    r"(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}|"
    r"(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}|"
    r"(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[0-9A-Fa-f]{1,4}){1,4}|"
    r"(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}|"
    r"[0-9A-Fa-f]{1,4}:(?::[0-9A-Fa-f]{1,4}){1,6}|"
    r":(?:(?::[0-9A-Fa-f]{1,4}){1,7}|:)"
    r")(?![\w:.])"
)

MAC_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:])")

URL_RE = re.compile(r"https?://[^\s<>\"'`\)\]\}]+")

# Horizontal whitespace only: a phone number never spans a line break, and
# allowing \s lets a masked value swallow the start of the next log line.
PHONE_CAND_RE = re.compile(r"(?<![\w.\-+])(\+?\(?\d[\d \t().\-]{5,22}\d)(?![\w])")

DATE_ISO_RE = re.compile(r"(?<![\d])(\d{4})-(\d{2})-(\d{2})(?![\d])")
DATE_SLASH_RE = re.compile(r"(?<![\d])(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?![\d])")
DATE_TEXT_RE = re.compile(
    r"(?i)\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\.?\s+(\d{4})\b"
)
DATE_TEXT_REV_RE = re.compile(
    r"(?i)\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b"
)

HONORIFIC_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Sir|Dame|Rev|Capt|Sgt)\.?\s+"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"
)


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_secrets(text: str) -> List[Span]:
    spans: List[Span] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if name == "bearer_token":
                start, end = match.start(1), match.end(1)
                value = match.group(1)
            else:
                start, end = match.start(), match.end()
                value = match.group(0)
            spans.append(Span(start, end, "SECRET", value, name, 0.99,
                              "matched a known provider credential format"))

    for match in SECRET_ASSIGN_RE.finditer(text):
        value = match.group(1)
        if value.lower().strip("\"'") in PLACEHOLDER_VALUES:
            continue
        if len(set(value)) < 3:
            continue
        spans.append(Span(match.start(1), match.end(1), "SECRET", value,
                          "assignment", 0.9,
                          "value assigned to a credential-shaped key"))
    return spans


def detect_emails(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in EMAIL_RE.finditer(text):
        value = match.group(0)
        if not email_valid(value):
            continue
        spans.append(Span(match.start(), match.end(), "EMAIL", value,
                          "email", 0.98, "structurally valid address"))
    return spans


def detect_cards(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in CARD_RE.finditer(text):
        raw = match.group(0)
        digits = _digits_only(raw)
        if not 13 <= len(digits) <= 19:
            continue
        if not luhn_valid(digits):
            continue
        spans.append(Span(match.start(), match.end(), "CREDIT_CARD", raw,
                          "luhn", 0.97, "passes the Luhn checksum"))
    return spans


def detect_ibans(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in IBAN_RE.finditer(text):
        raw = match.group(0)
        if not iban_valid(raw):
            continue
        spans.append(Span(match.start(), match.end(), "IBAN", raw,
                          "mod97", 0.97, "passes the ISO 7064 mod-97 check"))
    return spans


def detect_ssns(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in SSN_RE.finditer(text):
        if not ssn_valid(_digits_only(match.group(0))):
            continue
        spans.append(Span(match.start(), match.end(), "SSN", match.group(0),
                          "ssa_rules", 0.95, "valid SSA area/group/serial"))
    return spans


def detect_national_ids(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in NATIONAL_ID_RE.finditer(text):
        spans.append(Span(match.start(), match.end(), "NATIONAL_ID",
                          match.group(0), "cnic", 0.95,
                          "matches the CNIC format #####-#######-#"))
    return spans


def detect_ipv4(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in IPV4_RE.finditer(text):
        if not ipv4_valid(match.groups()):
            continue
        spans.append(Span(match.start(), match.end(), "IPV4", match.group(0),
                          "octets", 0.95, "all four octets in range 0-255"))
    return spans


def detect_ipv6(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in IPV6_RE.finditer(text):
        value = match.group(0)
        if value.count(":") < 2:
            continue
        spans.append(Span(match.start(), match.end(), "IPV6", value,
                          "hextets", 0.92, "two or more hextet groups"))
    return spans


def detect_macs(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in MAC_RE.finditer(text):
        spans.append(Span(match.start(), match.end(), "MAC", match.group(0),
                          "six_octets", 0.95, "six hex octets"))
    return spans


def detect_urls(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in URL_RE.finditer(text):
        value = match.group(0)
        trimmed = value.rstrip(".,;:!?")
        if trimmed.count("(") != trimmed.count(")"):
            trimmed = trimmed.rstrip(")")
        host = trimmed.split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0]
        host = host.split("@")[-1].split(":", 1)[0]
        if "." not in host and host != "localhost":
            continue
        end = match.start() + len(trimmed)
        spans.append(Span(match.start(), end, "URL", trimmed, "url", 0.9,
                          "http(s) URL with a dotted host"))
    return spans


def detect_phones(text: str) -> List[Span]:
    spans: List[Span] = []
    for match in PHONE_CAND_RE.finditer(text):
        raw = match.group(0)
        digits = _digits_only(raw)
        if not 7 <= len(digits) <= 15:
            continue
        if _is_ipv4_shape(raw) or _is_date_shape(raw):
            continue
        has_plus = raw.lstrip().startswith("+")
        has_paren = "(" in raw
        separators = sum(1 for c in raw if c in " -().")
        if not (has_plus or has_paren or separators >= 2):
            if not _has_context(text, match.start(), match.end(), PHONE_CONTEXT):
                continue
        elif not (has_plus or has_paren or separators >= 2):
            continue
        note = "leading + country code" if has_plus else (
            "parenthesised area code" if has_paren else f"{separators} separators"
        )
        if not (has_plus or has_paren or separators >= 2):
            note = "phone keyword nearby"
        spans.append(Span(match.start(), match.end(), "PHONE", raw,
                          "e164_shape", 0.85, note))
    return spans


def detect_dob(text: str) -> List[Span]:
    spans: List[Span] = []
    candidates: List[tuple] = []
    for match in DATE_ISO_RE.finditer(text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _is_valid_date_parts(year, month, day):
            candidates.append((match.start(), match.end(), match.group(0)))
    for match in DATE_SLASH_RE.finditer(text):
        month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _is_valid_date_parts(year, month, day):
            candidates.append((match.start(), match.end(), match.group(0)))
    for match in list(DATE_TEXT_RE.finditer(text)) + list(DATE_TEXT_REV_RE.finditer(text)):
        years = re.findall(r"\b(\d{4})\b", match.group(0))
        if not years:
            continue
        year = int(years[0])
        numbers = [int(n) for n in re.findall(r"\b(\d{1,2})\b", match.group(0))]
        day = next((n for n in numbers if 1 <= n <= 31), 1)
        if _is_valid_date_parts(year, 1, day):
            candidates.append((match.start(), match.end(), match.group(0)))

    for start, end, value in candidates:
        if not _has_context(text, start, end, BIRTH_CONTEXT, before=24, after=12):
            continue
        spans.append(Span(start, end, "DATE_OF_BIRTH", value, "birth_context",
                          0.88, "date next to a birth-context keyword"))
    return spans


def detect_persons(text: str, extra_names: Iterable[str] = ()) -> List[Span]:
    """Opt-in. Honourifics always, plus whole-word surname following a known name."""
    spans: List[Span] = []
    for match in HONORIFIC_RE.finditer(text):
        spans.append(Span(match.start(), match.end(), "PERSON", match.group(0),
                          "honorific", 0.9, "preceded by an honorific"))

    names = list(FIRST_NAMES) + [n.lower() for n in extra_names if n]
    if names:
        # The dictionary is lowercase but prose capitalises names, so the name
        # group alone is marked case-insensitive. Scoping it with (?i:...) keeps
        # the surname group case-SENSITIVE, which is what stops ordinary
        # lowercase prose ("the alice johnson report" would be fine, but
        # "backup johnson" should not become a person).
        pattern = re.compile(
            r"\b(?i:"
            + "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True))
            + r")\s+([A-Z][a-z]{1,20})\b"
        )
        for match in pattern.finditer(text):
            spans.append(Span(match.start(), match.end(), "PERSON", match.group(0),
                              "name_dictionary", 0.7,
                              "known given name followed by a surname"))
    return spans


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

#: Fixed iteration order keeps output stable regardless of dict ordering.
DETECTOR_ORDER: Sequence[str] = (
    "SECRET", "CREDIT_CARD", "SSN", "IBAN", "NATIONAL_ID", "EMAIL", "PHONE",
    "DATE_OF_BIRTH", "IPV6", "IPV4", "MAC", "URL", "PERSON",
)


def detect_all(text: str, enabled: Optional[Iterable[str]] = None,
               extra_names: Iterable[str] = ()) -> List[Span]:
    """Run every enabled detector and return spans in a deterministic order.

    Overlap is *not* resolved here \u2014 the scanner does that, so this function
    stays a pure catalogue of what each detector believes it saw. That is why two
    detectors may return overlapping spans for the same person (an honorific
    match and a dictionary match); resolving them is a policy decision, not a
    detection one.

    ``enabled`` accepts entity names, or the keyword ``ALL`` to turn on every
    detector including the opt-in ones, matching how the CLI and a policy
    document spell it.
    """
    if enabled is None:
        active: Set[str] = {e for e in DETECTOR_ORDER if e != "PERSON"}
    else:
        requested = {str(e).strip().upper() for e in enabled}
        active = set(DETECTOR_ORDER) if "ALL" in requested else requested

    found: List[Span] = []
    if "SECRET" in active:
        found.extend(detect_secrets(text))
    if "CREDIT_CARD" in active:
        found.extend(detect_cards(text))
    if "SSN" in active:
        found.extend(detect_ssns(text))
    if "IBAN" in active:
        found.extend(detect_ibans(text))
    if "NATIONAL_ID" in active:
        found.extend(detect_national_ids(text))
    if "EMAIL" in active:
        found.extend(detect_emails(text))
    if "PHONE" in active:
        found.extend(detect_phones(text))
    if "DATE_OF_BIRTH" in active:
        found.extend(detect_dob(text))
    if "IPV6" in active:
        found.extend(detect_ipv6(text))
    if "IPV4" in active:
        found.extend(detect_ipv4(text))
    if "MAC" in active:
        found.extend(detect_macs(text))
    if "URL" in active:
        found.extend(detect_urls(text))
    if "PERSON" in active:
        found.extend(detect_persons(text, extra_names))

    found.sort(key=lambda s: (s.start, s.end, s.entity, s.detector))
    return found

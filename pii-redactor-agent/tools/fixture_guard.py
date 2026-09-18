#!/usr/bin/env python3
"""Fail the build when a synthetic fixture could be mistaken for a real secret.

This tool exists because of a real incident. The first push of this project was
rejected by GitHub with:

    Repository rule violations found
    Secret detected in content
    (token_type: STRIPE_LIVE_API_SECRET_KEY)

Nothing was leaked: the value was a synthetic fixture invented for the test
suite. But GitHub's push protection cannot tell a fixture from a leak, and
neither can a human skimming the diff \u2014 so a fake secret that *looks* real is
still a problem. It blocks the push, it trains reviewers to wave past a secret
warning, and if someone copies the fixture into their own config it becomes a
real leak.

The rule this tool enforces, which is now a project convention:

    A synthetic credential must be self-evidently synthetic.

Concretely, it must contain one of the marker words below (SYNTHETIC,
PLACEHOLDER, EXAMPLE, NOTREAL, FAKE, DUMMY, TESTKEY, DEMO) or belong to a set of
values that are published in provider documentation as universally reserved.

    python3 tools/fixture_guard.py          # check
    python3 tools/fixture_guard.py --list   # also print every fixture found

Exit code 0 when every fixture is safely marked, 1 otherwise, so it can run in
CI as a gate.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Iterator, List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Directories that never hold project fixtures, or hold vendored code.
SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "node_modules", ".venv", "venv"}

#: File extensions worth scanning.
SCAN_EXTENSIONS = {".py", ".js", ".json", ".md", ".yaml", ".yml", ".txt", ".html", ".css"}

#: Words that mark a value as deliberately fake. Case-insensitive.
MARKERS = ("synthetic", "placeholder", "example", "notreal", "fake", "dummy",
           "testkey", "demo", "sample", "redacted")

#: Values that are reserved by their provider and documented as safe to publish.
#: These are exempt even without a marker word, because they are *designed* to
#: appear in public repos.
KNOWN_SAFE = {
    "4111111111111111",       # Visa's published test PAN (Luhn-valid on purpose)
    "4242424242424242",       # Stripe's published test PAN
    "5500000000000004",       # Mastercard's published test PAN
    "GB82WEST12345698765432",  # the canonical IBAN used in ISO 13616 examples
    "AKIASYNTHETICKEY0000",   # the AWS example key used by this project's fixtures
    "123456789",              # reserved SSN example
}

#: Patterns that indicate a credential-shaped value, named for the report.
CREDENTIAL_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Stripe live secret key", re.compile(r"sk_live_[A-Za-z0-9]{16,}")),
    ("Stripe live restricted key", re.compile(r"rk_live_[A-Za-z0-9]{16,}")),
    ("Stripe test key", re.compile(r"(?:sk|rk)_test_[A-Za-z0-9]{16,}")),
    ("AWS access key id", re.compile(r"(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("Slack token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    # The whole block, not just the header. A `-----BEGIN ...-----` line on its
    # own encodes nothing, so flagging it would fail every fixture that needs a
    # header to exercise the detector; it is the body that could be a real key.
    ("PEM private key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    )),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
]

#: A value inside a credential assignment is suspicious when it is long, mixed
#: and not obviously a placeholder. Short values are ignored: `api_key=short`
#: is documentation, not a fixture.
ASSIGN_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|password|passwd|token|passphrase)"
    r"[ \t]*[:=][ \t]*[\"']([^\"'\s,;]{8,})[\"']"
)


def iter_files(root: str) -> Iterator[str]:
    for base, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            if os.path.splitext(filename)[1].lower() in SCAN_EXTENSIONS:
                path = os.path.join(base, filename)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue
                yield path


def is_marked(value: str) -> bool:
    """True when the value announces its own syntheticness, or is a known-safe literal."""
    lowered = value.lower()
    if any(marker in lowered for marker in MARKERS):
        return True
    stripped = re.sub(r"[^A-Za-z0-9]", "", value)
    for safe in KNOWN_SAFE:
        if re.sub(r"[^A-Za-z0-9]", "", safe).lower() == stripped.lower():
            return True
    return False


def scan_text(text: str, path: str) -> List[Tuple[str, int, str, str]]:
    """Return (kind, line_number, value, reason) for every unmarked candidate."""
    problems: List[Tuple[str, int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in CREDENTIAL_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(0)
                if not is_marked(value):
                    problems.append((kind, number, value, "matches a provider credential format"))
        for match in ASSIGN_RE.finditer(line):
            value = match.group(1)
            if len(value) < 8:
                continue
            if is_marked(value):
                continue
            # A value made of one repeated character, or of digits only, reads as
            # documentation ("api_key=12345678") rather than a token.
            if len(set(value)) < 3 or value.isdigit():
                continue
            if not any(c.isalpha() for c in value):
                continue
            problems.append(("credential-shaped assignment", number, value,
                             "assigned to a credential key without a marker word"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="print every fixture-shaped value that passed")
    parser.add_argument("--root", default=PROJECT_ROOT, help="directory to scan")
    args = parser.parse_args()

    total = 0
    failures: List[Tuple[str, str, int, str, str]] = []
    passed: List[Tuple[str, int, str]] = []

    for path in iter_files(args.root):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        relative = os.path.relpath(path, args.root)

        found_here = scan_text(text, relative)
        for kind, number, value, reason in found_here:
            total += 1
            failures.append((relative, kind, number, value, reason))

        if args.list:
            for kind, pattern in CREDENTIAL_PATTERNS:
                for match in pattern.finditer(text):
                    if is_marked(match.group(0)):
                        passed.append((relative, kind, match.group(0)))

    if args.list and passed:
        print("Marked fixtures (safe to publish):")
        for relative, kind, value in passed:
            print(f"  {relative:<44} {kind:<28} {value}")
        print()

    if failures:
        print(f"FIXTURE GUARD FAILED \u2014 {len(failures)} credential-shaped value(s) "
              f"are not marked as synthetic:\n")
        for relative, kind, number, value, reason in failures:
            shown = value if len(value) <= 48 else value[:45] + "..."
            print(f"  {relative}:{number}")
            print(f"    kind   {kind}")
            print(f"    value  {shown}")
            print(f"    why    {reason}")
            print()
        print("A synthetic credential must be self-evidently synthetic. Either:")
        print(f"  - add a marker word to the value ({', '.join(MARKERS[:5])}, ...), e.g.")
        print("        SAMPLE_KEY = \"sk_test_SYNTHETICPLACEHOLDER00\"")
        print("  - or use a value the provider publishes as reserved (see KNOWN_SAFE).")
        print()
        print("This is not pedantry: GitHub push protection rejects the push "
              "outright, so an unmarked fixture cannot even be committed.")
        return 1

    print(f"FIXTURE GUARD OK \u2014 {total} unmarked credential-shaped value(s) found "
          f"across the tree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

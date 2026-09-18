#!/usr/bin/env python3
"""Check that everything the README references actually exists and runs.

A README is a promise. When it documents a file that was never committed, or a
command that fails on a fresh clone, every other claim in the document becomes
suspect — and the failure is invisible to the author, who has the missing file
locally.

This script reads README.md, extracts every backticked path-like token, and
verifies it resolves inside this project. It also *executes* the two commands the
README tells a reader to run for verification, so a documented command that has
been renamed or broken is caught here rather than by the reader.

    python3 tools/readme_check.py           # check paths and commands
    python3 tools/readme_check.py --paths   # only check paths (fast)

Exit 0 means the README's file and command claims hold.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(PROJECT_ROOT, "README.md")

#: Tokens that look like paths but are deliberately not files here.
IGNORED_PREFIXES = (
    "http://", "https://", "mailto:", "git+", "pip ", "python3 -m", "pytest",
    "npm ", "cd ", "curl ", "node ", "uvicorn", "docker", "vercel",
)

#: Shell metacharacters that mean the token is a command, not a path.
COMMAND_MARKERS = (" ", "|", ">", "<", "$", "&&", "||", "=")

#: Bare words that read as paths but are prose.
IGNORED_PATHS = {
    "true", "false", "none", "null", "yaml", "json", "md", "py", "js",
    "utf-8", "mask", "redact", "hash", "tokenize", "remove", "keep",
}

#: URL schemes that are example syntax, not files.
IGNORED_SCHEMES = ("file://", "file:", "data:", "about:")

#: Files the README legitimately references that are *generated* by
#: `veil demo` rather than committed. They are verified by running that command
#: in a temp directory (see `verify_demo_artifacts`), not by os.path.exists.
DEMO_ARTIFACTS = {
    "vendor_email.txt", "deployment.env", "app.log",
    "strict.yaml", "pseudonymize.yaml", "shareable.yaml",
}

#: The directory `veil demo` writes into by default. A README reference to
#: `./veil-demo/` is documentation of where output lands, not a committed folder.
DEMO_DIR = "veil-demo"


def looks_like_masked_value(token: str) -> bool:
    """True for illustrative snippets like `al\u2022\u2022\u2022@example.com`.

    These appear in prose to show what masking produces. They are output, not
    filenames, and a checker that demands they exist on disk is simply wrong.
    """
    return ("\u2022" in token) or token.startswith("al\u2022")

TIMEOUT = 300


def read_readme() -> str:
    if not os.path.exists(README):
        raise SystemExit(f"README.md not found at {README}")
    with open(README, "r", encoding="utf-8") as handle:
        return handle.read()


def extract_candidates(text: str) -> list:
    """Every backticked token that plausibly names a path in this project."""
    candidates = []
    seen = set()
    for token in re.findall(r"`([^`\n]+)`", text):
        token = token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        if any(token.startswith(prefix) for prefix in IGNORED_PREFIXES):
            continue
        if any(token.startswith(scheme) for scheme in IGNORED_SCHEMES):
            continue
        if any(marker in token for marker in COMMAND_MARKERS):
            continue
        if token in IGNORED_PATHS:
            continue

        has_extension = bool(re.search(r"\.[A-Za-z0-9]{1,6}$", token))

        # An HTTP route: a leading slash with no file extension. `/health` is an
        # endpoint, not a path. `/tmp/clean.env` keeps its extension and is
        # treated as a path.
        if token.startswith("/") and not has_extension:
            continue

        # Illustrative output such as a masked address.
        if looks_like_masked_value(token):
            continue

        # Files produced by `veil demo` — checked by running it, below.
        if token.split("/")[-1] in DEMO_ARTIFACTS:
            continue

        # The demo output directory itself (e.g. `./veil-demo/`), which does not
        # exist until the reader runs the command.
        if token.strip("./").rstrip("/") == DEMO_DIR:
            continue

        # Only things that look like a path or a filename with an extension.
        looks_like_path = "/" in token or token.startswith(".") or has_extension
        if not looks_like_path:
            continue
        candidates.append(token)
    return sorted(candidates)


def verify_demo_artifacts(python: str) -> list:
    """Run `veil demo` in a temp directory and confirm every promised file appears.

    The README tells a reader that `veil demo` produces a bundle of six specific
    files. Claiming that is cheap; running the command and listing the output is
    not. Returns a list of problems (empty means all present).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        completed = subprocess.run(
            [python, "-m", "veil", "demo", "-d", tmp],
            capture_output=True, text=True, timeout=TIMEOUT, env=env,
        )
        if completed.returncode != 0:
            return [f"`veil demo` exited {completed.returncode}",
                    completed.stderr.strip()[-400:]]

        produced = set()
        for root, _dirs, files in os.walk(tmp):
            for name in files:
                produced.add(name)

        missing = sorted(name for name in DEMO_ARTIFACTS if name not in produced)
        extra = sorted(name for name in produced if name not in DEMO_ARTIFACTS)
        problems = []
        if missing:
            problems.append("veil demo did not produce: " + ", ".join(missing))
        if extra:
            problems.append("veil demo produced undocumented files: " + ", ".join(extra))
        return problems


def check_path(token: str) -> tuple:
    """Resolve one token. Trims trailing punctuation already handled upstream."""
    cleaned = token.strip().rstrip(".,;:")
    # A token may name a file OR a directory that legitimately exists.
    target = os.path.join(PROJECT_ROOT, cleaned)
    if os.path.exists(target):
        kind = "dir " if os.path.isdir(target) else "file"
        return True, f"{kind}  {cleaned}"

    # `veil/foo.py` style references from a code fence are sometimes written
    # without the package prefix when the README is already inside the project.
    for prefix in ("veil/", "web-live/", "tools/", "tests/", "api/"):
        if cleaned.startswith(prefix):
            continue
        alt = os.path.join(PROJECT_ROOT, prefix + cleaned)
        if os.path.exists(alt):
            return True, f"file  {prefix}{cleaned}"

    return False, cleaned


def run_command(label: str, command: list) -> tuple:
    """Run a documented command and report whether it succeeded."""
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=TIMEOUT,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, f"{label}: exit {completed.returncode}", output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", action="store_true",
                        help="only verify paths, skip running commands")
    args = parser.parse_args()

    text = read_readme()
    candidates = extract_candidates(text)

    print(f"README.md is {len(text):,} characters; "
          f"{len(candidates)} path-like tokens found\n")

    missing = []
    for token in candidates:
        ok, detail = check_path(token)
        status = "  ok  " if ok else " MISS "
        print(f"{status} {detail}")
        if not ok:
            missing.append(token)

    if missing:
        print(f"\nREADME CHECK FAILED — {len(missing)} referenced path(s) do not exist:")
        for token in missing:
            print(f"  - {token}")
        return 1

    print("\nAll referenced paths exist.")

    demo_problems = verify_demo_artifacts(sys.executable)
    if demo_problems:
        print("\nREADME CHECK FAILED — the documented demo bundle is wrong:")
        for problem in demo_problems:
            print(f"  - {problem}")
        return 1
    print(f"`veil demo` produces all {len(DEMO_ARTIFACTS)} documented files, and no others.")

    if args.paths:
        return 0

    print("\nRunning the commands the README documents as verification:\n")
    commands = [
        ("pytest", [sys.executable, "-m", "pytest", "-q"]),
        ("parity_check", [sys.executable, "tools/parity_check.py"]),
        ("web_smoke", ["node", "tools/web_smoke.js"]),
        ("build_web_data --check",
         [sys.executable, "tools/build_web_data.py", "--check"]),
    ]
    failures = []
    for label, command in commands:
        ok, summary, output = run_command(label, command)
        print(f"  {'ok  ' if ok else 'FAIL'} {summary}")
        if not ok:
            failures.append((label, output))

    if failures:
        print(f"\nREADME CHECK FAILED — {len(failures)} documented command(s) failed:")
        for label, output in failures:
            print(f"\n--- {label} ---")
            print("\n".join(output.strip().splitlines()[-25:]))
        return 1

    print("\nREADME CHECK OK — every path exists and every documented command passes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

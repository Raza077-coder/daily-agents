"""Command line interface.

Exit codes are part of the contract, because the main use of this tool is inside
a CI job that must fail when a document is dirty:

==== ============================================================
Code Meaning
==== ============================================================
0    Success. For ``scan``/``check``: nothing above the fail threshold.
1    ``scan``/``check`` found findings at or above the fail threshold (or a
     residual survived verification).
2    A usage, policy, config, or input error. The message names the offender.
==== ============================================================
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import actions as actions_module
from . import demo as demo_module
from . import reports, vault as vault_module
from .errors import VeilError
from .models import ALL_ENTITIES
from .policy import Policy, apply_env, load as load_policy
from .scanner import Scanner, build_vault

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

LEVEL_ORDER = ["NONE", "LOW", "MODERATE", "HIGH", "CRITICAL"]

PROG = "veil"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        raise VeilError(f"no such file: {path}") from None
    except OSError as exc:
        raise VeilError(f"could not read {path}: {exc}") from exc


def _write_text(path: str, body: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def _load_policy_document(args) -> Policy:
    """Resolve the effective policy from ``--policy`` plus env overrides."""
    if getattr(args, "policy_document", None):
        return apply_env(load_policy(args.policy_document))
    return apply_env(Policy())


def _resolve_policy(args) -> Policy:
    """CLI overrides layered on top of the policy document."""
    policy = _load_policy_document(args)

    entities = getattr(args, "entities", None)
    if entities:
        resolved: List[str] = []
        for token in entities:
            key = token.strip().upper()
            if key == "ALL":
                resolved.extend(ALL_ENTITIES)
                continue
            if key.startswith("!"):
                target = key[1:].upper()
                if target not in ALL_ENTITIES:
                    raise VeilError(
                        f"unknown entity {target!r}. Known: {', '.join(ALL_ENTITIES)}"
                    )
                resolved = [e for e in resolved if e != target]
                continue
            if key not in ALL_ENTITIES:
                raise VeilError(
                    f"unknown entity {key!r}. Known: {', '.join(ALL_ENTITIES)}"
                )
            if key not in resolved:
                resolved.append(key)
        if not resolved:
            raise VeilError("--entities resolved to an empty set; nothing would be scanned")
        policy.entities = resolved

    pairs = getattr(args, "action", None) or []
    for pair in pairs:
        if "=" not in pair:
            raise VeilError(f"--action expects ENTITY=ACTION, got {pair!r}")
        entity, _, action = pair.partition("=")
        entity = entity.strip().upper()
        action = action.strip().lower()
        if entity not in ALL_ENTITIES:
            raise VeilError(
                f"unknown entity {entity!r} in --action. Known: {', '.join(ALL_ENTITIES)}"
            )
        if action not in actions_module.ACTIONS and action not in ("hash", "tokenize"):
            raise VeilError(
                f"unknown action {action!r} in --action. Valid: "
                f"{', '.join(actions_module.ACTIONS)}"
            )
        policy.actions[entity] = action

    if getattr(args, "name", None):
        policy.name = args.name
    if getattr(args, "keep_first", None) is not None:
        policy.keep_first = args.keep_first
    if getattr(args, "keep_last", None) is not None:
        policy.keep_last = args.keep_last
    if getattr(args, "mask_char", None):
        policy.mask_char = args.mask_char
    if getattr(args, "hash_salt", None):
        policy.hash_salt = args.hash_salt
    if getattr(args, "token_prefix", None):
        policy.token_prefix = args.token_prefix
    if getattr(args, "allow", None):
        policy.allowlist = list(policy.allowlist) + list(args.allow)
    if getattr(args, "deny", None):
        policy.denylist = list(policy.denylist) + list(args.deny)
    if getattr(args, "names", None):
        policy.names = list(policy.names) + list(args.names)
    return policy


def fail_on_level_arg(value: str) -> str:
    """Normalise a `--fail-on` value to upper case.

    `argparse choices` matches literally, so `--fail-on high` would be rejected
    while `--fail-on HIGH` was accepted \u2014 even though the help text and the
    README both show the lower-case form. Normalising here means the documented
    form works and the accepted set stays exactly `LEVEL_ORDER + any/never`.
    """
    normalised = str(value).strip().upper()
    if normalised not in LEVEL_ORDER and normalised not in ("ANY", "NEVER"):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a level; choose from "
            + ", ".join(LEVEL_ORDER + ["any", "never"])
        )
    return normalised


def _fail_on_level(level: str, threshold: str) -> bool:
    """True when `level` reaches `threshold`.

    `never` and `any` are the two sentinels, and both must be handled *before*
    the index lookup \u2014 `LEVEL_ORDER` contains only real levels, so passing `any`
    through would raise ValueError instead of gating the build.
    """
    if threshold == "NEVER":
        return False
    if threshold == "ANY":
        return LEVEL_ORDER.index(level) >= LEVEL_ORDER.index("LOW")
    return LEVEL_ORDER.index(level) >= LEVEL_ORDER.index(threshold)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_demo(args) -> int:
    directory = args.dir
    written = demo_module.write_samples(directory, overwrite=args.force)
    print(f"Sample bundle written to {os.path.abspath(directory)}")
    print()
    for row in demo_module.document_summary():
        print(f"  {row['name']:<20} {row['kind']:<6} {row['why']}")
    print()
    print("Policies:")
    for row in demo_module.policy_summary():
        reversible = "reversible" if row["reversible"] else "irreversible"
        print(f"  {row['name']:<20} {row['posture']:<22} ({reversible})")
        print(f"  {'':<20} {row['when']}")
    print()
    print("Next:")
    print(f"  {PROG} scan {os.path.join(directory, 'app.log')}")
    print(f"  {PROG} redact {os.path.join(directory, 'vendor_email.txt')} "
          f"-o {os.path.join(directory, 'clean.txt')}")
    return EXIT_OK


def cmd_entities(args) -> int:
    if args.policy_document:
        policy = apply_env(load_policy(args.policy_document))
    else:
        policy = apply_env(Policy())
    scanner = Scanner(policy)
    rows = scanner.entity_report()
    if args.json:
        import json
        print(json.dumps(rows, indent=2))
        return EXIT_OK

    print(f"Policy: {policy.name}   (source: {policy.source})")
    print()
    print(f"{'ENTITY':<15}{'ON':<4}{'ACTION':<10}{'W':<4}LABEL")
    print("-" * 74)
    for row in rows:
        mark = "yes" if row["enabled"] else "no"
        action = row["action"] or "-"
        opt = " (opt-in)" if row["opt_in"] else ""
        print(f"{row['entity']:<15}{mark:<4}{action:<10}{row['risk_weight']:<4}"
              f"{row['label']}{opt}")
    return EXIT_OK


def cmd_reference(args) -> int:
    print(reports.render_reference())
    return EXIT_OK


def cmd_scan(args) -> int:
    policy = _resolve_policy(args)
    scanner = Scanner(policy)
    text = _read_text(args.path)

    if args.redacted:
        result = scanner.redact(text, verify_output=True)
        if args.json:
            print(reports.render_json(result, include_values=not args.no_values))
        else:
            print(reports.render_terminal(
                result, show_values=not args.no_values,
                show_offsets=args.offsets,
            ))
            if args.diff:
                print()
                print(reports.render_diff(text, result.text))
        level = result.risk.level if result.risk else "NONE"
        dirty = bool(result.findings) or (
            result.verification is not None and not result.verification.clean
        )
        if args.fail_on != "NEVER" and (dirty or _fail_on_level(level, args.fail_on)):
            return EXIT_FINDINGS
        return EXIT_OK

    result = scanner.scan(text)
    if args.json:
        print(reports.render_json(result, include_values=not args.no_values))
    else:
        print(reports.render_terminal(
            result, show_values=not args.no_values, show_offsets=args.offsets
        ))

    level = result.risk.level if result.risk else "NONE"
    if args.fail_on == "NEVER":
        return EXIT_OK
    if args.fail_on == "ANY":
        return EXIT_FINDINGS if result.findings else EXIT_OK
    return EXIT_FINDINGS if _fail_on_level(level, args.fail_on) else EXIT_OK


def cmd_redact(args) -> int:
    policy = _resolve_policy(args)
    scanner = Scanner(policy)
    text = _read_text(args.path)
    result = scanner.redact(text, verify_output=not args.no_verify)

    if args.stdout:
        sys.stdout.write(result.text)
        if not result.text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        _write_text(args.output, result.text)
        print(f"Wrote {args.output}")

    if result.token_map:
        vault = build_vault(result)
        vault_path = args.vault or vault_module.default_vault_path(
            args.output if not args.stdout else "redacted.txt"
        )
        vault.save(vault_path)
        print(f"Token vault written to {vault_path}  (mode 0600 \u2014 it holds the "
              f"original values)")
        print(f"  {len(vault)} token(s); restore with: {PROG} detokenize "
              f"<file> --vault {vault_path}")

    if args.report:
        _write_text(args.report, reports.render_markdown(
            result, show_values=not args.no_values
        ))
        print(f"Report written to {args.report}")

    if not args.quiet:
        print()
        print(reports.render_terminal(result, show_values=not args.no_values))

    if args.fail_on != "NEVER":
        level = result.risk.level if result.risk else "NONE"
        if _fail_on_level(level, args.fail_on):
            return EXIT_FINDINGS
    return EXIT_OK


def cmd_verify(args) -> int:
    policy = _resolve_policy(args)
    scanner = Scanner(policy)
    text = _read_text(args.path)
    result = scanner.redact(text, verify_output=True)
    verification = result.verification

    if args.json:
        import json
        print(json.dumps(verification.to_dict() if verification else {}, indent=2))
        return EXIT_OK if (verification and verification.clean) else EXIT_FINDINGS

    if verification is None:
        print("Verification was skipped.")
        return EXIT_OK

    mark = "\u2713" if verification.clean else "\u2717"
    print(f"{mark} {verification.status}")
    print(f"  checked: {', '.join(verification.checked_entities)}")
    if verification.residuals:
        print()
        print(f"  {len(verification.residuals)} residual value(s) survived:")
        for residual in verification.residuals:
            value = residual["value"] if not args.no_values else "(withheld)"
            print(f"    {residual['entity']:<15} {value}")
    if verification.preserved:
        print()
        print(f"  {len(verification.preserved)} value(s) deliberately kept by the allowlist")
    return EXIT_OK if verification.clean else EXIT_FINDINGS


def cmd_detokenize(args) -> int:
    vault = vault_module.Vault.load(args.vault)
    text = _read_text(args.path)

    restored = text
    for token in sorted(vault.to_mapping(), key=len, reverse=True):
        restored = restored.replace(token, vault.to_mapping()[token])

    if args.stdout:
        sys.stdout.write(restored)
        if not restored.endswith("\n"):
            sys.stdout.write("\n")
    else:
        _write_text(args.output, restored)
        print(f"Wrote {args.output}")

    if not args.quiet:
        print()
        print(f"Restored {len(vault)} token(s) from {args.vault}")
        for entity, count in vault.summary().items():
            print(f"  {entity:<15} {count}")
    return EXIT_OK


def cmd_vault(args) -> int:
    vault = vault_module.Vault.load(args.vault)
    if args.json:
        import json
        print(json.dumps(vault.to_dict(), indent=2))
        return EXIT_OK
    print(f"Vault      {args.vault}")
    print(f"Format     {vault.format}")
    print(f"Policy     {vault.policy}")
    print(f"Entries    {len(vault)}")
    if vault.summary():
        print()
        print(f"{'ENTITY':<15}COUNT")
        print("-" * 24)
        for entity, count in vault.summary().items():
            print(f"{entity:<15}{count}")
    if not args.no_values:
        print()
        print(f"{'TOKEN':<26}VALUE")
        print("-" * 74)
        for token, value in sorted(vault.to_mapping().items()):
            shown = value if len(value) <= 40 else value[:37] + "..."
            print(f"{token:<26}{shown}")
    return EXIT_OK


def cmd_check(args) -> int:
    """Multi-file CI gate: scan or redact a whole tree, one summary, one exit code."""
    policy = _resolve_policy(args)
    scanner = Scanner(policy)

    targets: List[str] = []
    for root in args.paths:
        if os.path.isdir(root):
            for base, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(
                    d for d in dirnames if d not in {".git", "__pycache__", ".venv", "node_modules"}
                )
                for filename in sorted(filenames):
                    targets.append(os.path.join(base, filename))
        else:
            targets.append(root)

    if not targets:
        raise VeilError(f"no files found under: {', '.join(args.paths)}")

    worst = "NONE"
    dirty = 0
    rows: List[dict] = []
    for path in targets:
        try:
            text = _read_text(path)
        except VeilError as exc:
            raise VeilError(f"could not read {path}: {exc.message}") from None
        result = scanner.scan(text)
        level = result.risk.level if result.risk else "NONE"
        if result.findings:
            dirty += 1
        if LEVEL_ORDER.index(level) > LEVEL_ORDER.index(worst):
            worst = level
        rows.append({
            "path": path,
            "level": level,
            "score": result.risk.score if result.risk else 0,
            "distinct": result.risk.distinct_findings if result.risk else 0,
            "occurrences": result.risk.total_occurrences if result.risk else 0,
        })

    if args.json:
        import json
        print(json.dumps({
            "policy": policy.name,
            "files": len(rows),
            "dirty": dirty,
            "worst_level": worst,
            "threshold": args.fail_on,
            "results": rows,
        }, indent=2))
    else:
        print(f"Policy {policy.name}   files {len(rows)}   "
              f"with findings {dirty}   worst {worst}")
        print()
        print(f"{'LEVEL':<10}{'SCORE':>6}{'DISTINCT':>10}{'OCCUR':>7}  FILE")
        print("-" * 78)
        for row in rows:
            flag = " " if row["level"] == "NONE" else "*"
            print(f"{row['level']:<10}{row['score']:>6}{row['distinct']:>10}"
                  f"{row['occurrences']:>7}{flag} {row['path']}")

    if args.fail_on == "NEVER":
        return EXIT_OK
    if args.fail_on == "ANY":
        return EXIT_FINDINGS if dirty else EXIT_OK
    return EXIT_FINDINGS if _fail_on_level(worst, args.fail_on) else EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "VEIL \u2014 deterministic PII redaction. Finds personal and secret data in "
            "text, redacts it under a policy you control, and verifies the result. "
            "Fully offline: no network calls, no API keys, no model."
        ),
        epilog=(
            "Examples:\n"
            f"  {PROG} demo\n"
            f"  {PROG} scan vendor_email.txt\n"
            f"  {PROG} redact app.log -o clean.log --report report.md\n"
            f"  {PROG} redact notes.txt -o out.txt --policy pseudonymize.yaml\n"
            f"  {PROG} detokenize out.txt --vault out.txt.veilvault.json\n"
            f"  {PROG} check ./logs --fail-on high\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} 1.0.0")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add_policy_args(target: argparse.ArgumentParser) -> None:
        group = target.add_argument_group("policy")
        group.add_argument("-p", "--policy", dest="policy_document", metavar="FILE",
                           help="YAML policy file (see config.example.yaml)")
        group.add_argument("-e", "--entities", action="append", metavar="LIST",
                           help="comma-separated entities; ALL enables everything, !X removes X")
        group.add_argument("-a", "--action", action="append", metavar="ENTITY=ACTION",
                           help="override one entity's action (repeatable)")
        group.add_argument("--name", metavar="NAME", help="policy name to record in reports")
        group.add_argument("--keep-first", type=int, metavar="N",
                           help="characters preserved at the start when masking")
        group.add_argument("--keep-last", type=int, metavar="N",
                           help="characters preserved at the end when masking")
        group.add_argument("--mask-char", metavar="CHAR", help="single character used to mask")
        group.add_argument("--hash-salt", metavar="SALT",
                           help="key for the hash action; without it hashes are unsalted")
        group.add_argument("--token-prefix", metavar="PREFIX",
                           help="prefix for tokenize placeholders (default VEIL)")
        group.add_argument("--allow", action="append", metavar="VALUE",
                           help="literal value to report but never change (repeatable)")
        group.add_argument("--deny", action="append", metavar="VALUE",
                           help="literal value to always redact, even if undetected (repeatable)")
        group.add_argument("--names", action="append", metavar="NAME",
                           help="extra name for the PERSON detector (repeatable)")

    def add_output_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--json", action="store_true", help="emit JSON")
        target.add_argument("--no-values", action="store_true",
                            help="withhold raw values from the report (safe for shared logs)")
        target.add_argument("--offsets", action="store_true",
                            help="include character offsets for each occurrence")

    demo_cmd = sub.add_parser("demo", help="write the synthetic sample bundle and print a tour")
    demo_cmd.add_argument("-d", "--dir", default="veil-demo", metavar="DIR",
                          help="directory to write samples into (default: veil-demo)")
    demo_cmd.add_argument("-f", "--force", action="store_true",
                          help="overwrite existing sample files")
    demo_cmd.set_defaults(func=cmd_demo)

    entities_cmd = sub.add_parser("entities", help="list entities, whether they are on, and their action")
    entities_cmd.add_argument("-p", "--policy", dest="policy_document", metavar="FILE",
                              help="YAML policy file to describe")
    entities_cmd.add_argument("--json", action="store_true", help="emit JSON")
    entities_cmd.set_defaults(func=cmd_entities)

    reference_cmd = sub.add_parser("reference", help="print the entity and action reference tables")
    reference_cmd.set_defaults(func=cmd_reference)

    scan_cmd = sub.add_parser("scan", help="detect without changing anything")
    scan_cmd.add_argument("path", help="file to scan, or - for stdin")
    add_policy_args(scan_cmd)
    add_output_args(scan_cmd)
    scan_cmd.add_argument("--redacted", action="store_true",
                          help="also produce the redacted text and show a diff")
    scan_cmd.add_argument("--diff", action="store_true", help="show the unified diff")
    scan_cmd.add_argument("--fail-on", default="ANY", metavar="LEVEL",
                          type=fail_on_level_arg,
                          help="exit 1 when the risk reaches this level (default: any)")
    scan_cmd.set_defaults(func=cmd_scan)

    redact_cmd = sub.add_parser("redact", help="redact a file and write the result")
    redact_cmd.add_argument("path", help="file to redact, or - for stdin")
    redact_cmd.add_argument("-o", "--output", default="redacted.txt", metavar="FILE",
                            help="output path (default: redacted.txt)")
    redact_cmd.add_argument("--stdout", action="store_true",
                            help="write redacted text to stdout instead of a file")
    redact_cmd.add_argument("--vault", metavar="FILE",
                            help="where to write the token vault (only when tokenizing)")
    redact_cmd.add_argument("--report", metavar="FILE", help="write a Markdown report here")
    add_policy_args(redact_cmd)
    add_output_args(redact_cmd)
    redact_cmd.add_argument("--no-verify", action="store_true",
                            help="skip the post-redaction verification pass")
    redact_cmd.add_argument("-q", "--quiet", action="store_true",
                            help="do not print the summary")
    redact_cmd.add_argument("--fail-on", default="NEVER", metavar="LEVEL",
                            type=fail_on_level_arg,
                            help="exit 1 when risk reaches this level (default: never)")
    redact_cmd.set_defaults(func=cmd_redact)

    verify_cmd = sub.add_parser("verify", help="redact and assert nothing survived")
    verify_cmd.add_argument("path", help="file to check, or - for stdin")
    add_policy_args(verify_cmd)
    add_output_args(verify_cmd)
    verify_cmd.set_defaults(func=cmd_verify)

    detok_cmd = sub.add_parser("detokenize", help="restore tokenized values from a vault")
    detok_cmd.add_argument("path", help="redacted file to restore, or - for stdin")
    detok_cmd.add_argument("--vault", required=True, metavar="FILE",
                           help="vault file written during redaction")
    detok_cmd.add_argument("-o", "--output", default="restored.txt", metavar="FILE",
                           help="output path (default: restored.txt)")
    detok_cmd.add_argument("--stdout", action="store_true", help="write to stdout")
    detok_cmd.add_argument("-q", "--quiet", action="store_true", help="suppress the summary")
    detok_cmd.set_defaults(func=cmd_detokenize)

    vault_cmd = sub.add_parser("vault", help="inspect a token vault")
    vault_cmd.add_argument("--vault", required=True, metavar="FILE", help="vault file")
    vault_cmd.add_argument("--json", action="store_true", help="emit JSON")
    vault_cmd.add_argument("--no-values", action="store_true",
                           help="show only the token summary, not the original values")
    vault_cmd.set_defaults(func=cmd_vault)

    check_cmd = sub.add_parser("check", help="scan a file or tree and gate a CI job on the result")
    check_cmd.add_argument("paths", nargs="+", metavar="PATH",
                           help="files or directories to check")
    add_policy_args(check_cmd)
    check_cmd.add_argument("--json", action="store_true", help="emit JSON")
    check_cmd.add_argument("--fail-on", default="HIGH", metavar="LEVEL",
                           type=fail_on_level_arg,
                           help="exit 1 when the worst level reaches this (default: high)")
    check_cmd.set_defaults(func=cmd_check)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        return args.func(args)
    except VeilError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

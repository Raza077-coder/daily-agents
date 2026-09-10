"""VAULTGUARD command-line interface.

Non-interactive by design so it composes with scripts and CI:

* ``--vault PATH``      override the vault location (also ``VAULTGUARD_PATH``)
* ``--master PASS``     supply the master password (also ``VAULTGUARD_MASTER``)
* ``--master-stdin``    read the master password from stdin (safest for pipes)

Exit codes: ``0`` success, ``1`` usage/vault error, ``2`` authentication error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .audit import VaultAuditor
from .engine import (
    SORT_KEYS,
    SearchQuery,
    VaultAuthError,
    VaultEngine,
    VaultError,
)
from .generator import GeneratorError
from .models import CATEGORIES
from .storage import DEFAULT_VAULT_PATH, VaultFormatError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2

BANNER = r"""
 __     __   _   _  _     _____  _   _   ___   _   _  ____
 \ \   / /  / \ | || |   |_   _|/ \ | | | | / _ \ | | | |  _ \
  \ \ / /  / _ \| || |     | | / _ \| | | |/ /_\ \| | | | | | |
   \ V /  / ___ \__   _|   | |/ ___ \ |_| |  _  | |_| | |_| |
    \_/  /_/   \_\ |_|     |_/_/   \_\___/|_| |_|\___/|____/
        Encrypted offline vault · zero network calls · v1.0
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _resolve_master(args: argparse.Namespace) -> Optional[str]:
    """Read the master password from flag, stdin or environment."""
    if getattr(args, "master_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    if getattr(args, "master", None):
        return args.master
    env = os.environ.get("VAULTGUARD_MASTER")
    if env:
        return env
    if sys.stdin.isatty():
        import getpass

        return getpass.getpass("Master password: ")
    return None


def _engine(args: argparse.Namespace) -> VaultEngine:
    path = getattr(args, "vault", None) or os.environ.get("VAULTGUARD_PATH") or DEFAULT_VAULT_PATH
    iterations = getattr(args, "iterations", None)
    return VaultEngine(path, iterations=iterations)


def _unlocked_engine(args: argparse.Namespace) -> VaultEngine:
    engine = _engine(args)
    if not engine.exists():
        raise VaultError(f"no vault at {engine.path} — run 'init' first")
    master = _resolve_master(args)
    if not master:
        raise VaultAuthError("no master password supplied (use --master, --master-stdin or VAULTGUARD_MASTER)")
    engine.unlock(master)
    return engine


def emit(payload: Any, as_json: bool, text_renderer=None) -> None:
    """Print either JSON or a human rendering."""
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif text_renderer is not None:
        print(text_renderer(payload))
    else:
        print(payload)


def _render_entries(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return "  (no entries)"
    lines = [f"  {'ID':<14}{'TITLE':<26}{'CATEGORY':<12}{'USERNAME':<22}AGE"]
    lines.append("  " + "-" * 82)
    for entry in entries:
        lines.append(
            f"  {entry['id']:<14}{entry['title'][:25]:<26}{entry['category'][:11]:<12}"
            f"{(entry['username'] or '-')[:21]:<22}{entry.get('age_days', '')}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# command implementations
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace) -> int:
    engine = _engine(args)
    master = _resolve_master(args)
    if not master:
        raise VaultAuthError("a master password is required to create a vault")
    result = engine.create(master, force=args.force)
    if args.demo:
        seed_demo(engine, quick=True)
        result["seeded"] = True
    emit(result, args.json)
    if not args.json:
        print(f"\n  Vault ready at {result['path']}")
        print(f"  KDF: PBKDF2-HMAC-SHA256 · {result['iterations']:,} iterations")
        print("  Keep the master password safe — it cannot be recovered.\n")
    return EXIT_OK


def cmd_add(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    result = engine.add(
        title=args.title,
        password=args.password or "",
        username=args.username or "",
        url=args.url or "",
        category=args.category or "login",
        notes=args.notes or "",
        tags=tags,
        totp=args.totp or "",
        favorite=bool(args.favorite),
        generate=bool(args.generate),
        length=args.length or 20,
    )
    emit(result, args.json, lambda r: f"  Added '{r['entry']['title']}' [{r['entry']['id']}]"
         + ("  (password generated)" if r.get("generated") else ""))
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    entries = engine.list_all()
    emit(entries, args.json, _render_entries)
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    query = SearchQuery(
        text=args.query or "",
        category=args.category or "",
        tag=args.tag or "",
        favorites_only=bool(args.favorite),
        weak_only=bool(args.weak),
        stale_only=bool(args.stale),
        sort=args.sort or "title",
        descending=bool(args.desc),
        limit=args.limit or 0,
    )
    entries = engine.search(query)
    emit(entries, args.json, _render_entries)
    return EXIT_OK


def cmd_get(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    entry = engine.get(args.entry_id, reveal=bool(args.reveal))
    emit(entry, args.json, lambda e: "\n".join(
        f"  {k:<18}{v}" for k, v in e.items() if v not in ("", None, [], "")
    ))
    return EXIT_OK


def cmd_reveal(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    secret = engine.reveal(args.entry_id)
    if args.copy:
        try:
            import pyperclip  # type: ignore

            pyperclip.copy(secret["password"])
            secret["copied"] = True
        except Exception:
            secret["copied"] = False
    emit(secret, args.json, lambda s: f"  password: {s['password']}")
    return EXIT_OK


def cmd_update(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    changes: Dict[str, Any] = {}
    for field in ("title", "username", "password", "url", "notes", "category", "totp"):
        value = getattr(args, field, None)
        if value is not None:
            changes[field] = value
    if args.tags is not None:
        changes["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.favorite:
        changes["favorite"] = True
    if not changes:
        raise VaultError("nothing to update — pass at least one field flag")
    result = engine.update(args.entry_id, **changes)
    emit(result, args.json, lambda r: f"  Updated '{r['entry']['title']}'"
         + ("  (password rotated)" if r.get("password_changed") else ""))
    return EXIT_OK


def cmd_delete(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    result = engine.delete(args.entry_id)
    emit(result, args.json, lambda r: f"  Deleted '{r['entry']['title']}'")
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    report = engine.audit()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.render())
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    data = engine.stats()
    emit(data, args.json, lambda d: "\n".join(
        [
            f"  Entries     : {d['total']}",
            f"  Favorites   : {d['favorites']}",
            f"  With 2FA    : {d['with_totp']}",
            f"  With URL    : {d['with_url']}",
            f"  Duplicate   : {len(engine.duplicates())} groups",
            f"  Categories  : {', '.join(f'{k}({v})' for k, v in d['categories'].items()) or '-'}",
            f"  Health      : {engine.health_score()}/100",
        ]
    ))
    return EXIT_OK


def cmd_suggest(args: argparse.Namespace) -> int:
    engine = _engine(args)
    if engine.exists():
        master = _resolve_master(args)
        if master:
            engine.unlock(master)
    if engine.is_unlocked:
        result = engine.suggest(args.length, symbols=not args.no_symbols)
    else:
        from .generator import generate_password
        from .strength import estimate_strength

        password = generate_password(args.length, use_symbols=not args.no_symbols)
        result = {"password": password, "strength": estimate_strength(password).to_dict()}
    emit(result, args.json, lambda r: (
        f"  {r['password']}\n"
        f"  score {r['strength']['score']}/100 ({r['strength']['label']}) · "
        f"crack time {r['strength']['crack_time_human']}"
    ))
    return EXIT_OK


def cmd_generate(args: argparse.Namespace) -> int:
    engine = _engine(args)
    master = _resolve_master(args)
    if engine.exists() and master:
        engine.unlock(master)
    if engine.is_unlocked:
        result = engine.generate(args.kind, length=args.length, words=args.words)
    else:
        from .generator import generate_passphrase, generate_password, generate_pin
        from .strength import estimate_strength

        if args.kind == "passphrase":
            secret = generate_passphrase(args.words, add_number=True)
        elif args.kind == "pin":
            secret = generate_pin(args.length)
        else:
            secret = generate_password(args.length)
        result = {"kind": args.kind, "secret": secret, "strength": estimate_strength(secret).to_dict()}
    emit(result, args.json, lambda r: f"  {r['secret']}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    from .strength import estimate_strength

    password = args.password
    if password is None:
        password = sys.stdin.readline().rstrip("\n")
    report = estimate_strength(password).to_dict()
    emit(report, args.json, lambda r: "\n".join(
        [
            f"  Score        : {r['score']}/100  ({r['grade']} · {r['label']})",
            f"  Entropy      : {r['entropy_bits']} bits",
            f"  Length       : {r['length']}",
            f"  Classes      : {', '.join(r['character_classes']) or '-'}",
            f"  Crack time   : {r['crack_time_human']}",
            f"  Suggestions  : {'; '.join(r['suggestions'])}",
        ]
    ))
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    path = engine.export_plain(args.output, include_passwords=not args.masked)
    print(f"  Exported to {path}" + ("  (UNENCRYPTED — delete after use)" if not args.masked else "  (masked)"))
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    result = engine.import_plain(args.source)
    emit(result, args.json, lambda r: f"  Imported {r['added']} entries ({r['total']} total)")
    return EXIT_OK


def cmd_backup(args: argparse.Namespace) -> int:
    engine = _engine(args)
    path = engine.backup(args.destination)
    print(f"  Encrypted backup written to {path} (still protected by the master password)")
    return EXIT_OK


def cmd_info(args: argparse.Namespace) -> int:
    engine = _engine(args)
    data = engine.info()
    emit(data, args.json, lambda d: "\n".join(f"  {k:<14}{v}" for k, v in d.items()))
    return EXIT_OK


def cmd_rekey(args: argparse.Namespace) -> int:
    engine = _unlocked_engine(args)
    new_master = args.new_master or sys.stdin.readline().rstrip("\n")
    result = engine.change_master_password(engine._master or "", new_master)
    emit(result, args.json, lambda r: f"  Master password rotated. Vault re-encrypted with a fresh salt.")
    return EXIT_OK


def cmd_destroy(args: argparse.Namespace) -> int:
    engine = _engine(args)
    result = engine.destroy(confirm=True)
    print(f"  Vault at {result['path']} destroyed.")
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    """Seed a throwaway vault so the agent can be demonstrated instantly."""
    import tempfile

    path = args.vault or os.path.join(tempfile.mkdtemp(prefix="vaultguard-demo-"), "vault.json")
    engine = VaultEngine(path, iterations=args.iterations or 50_000)
    engine.create("demo-master-password", force=True)
    seed_demo(engine)
    engine.save()
    print(BANNER)
    print(f"  Demo vault : {path}")
    print("  Password   : demo-master-password\n")
    print(engine.audit().render())
    print("\n  Sample entries")
    print(_render_entries(engine.list_all()))
    return EXIT_OK


def seed_demo(engine: VaultEngine, *, quick: bool = False) -> None:
    """Populate a vault with realistic sample credentials."""
    samples = [
        dict(title="GitHub", username="raza.dev", category="work", url="https://github.com",
             password="Xk9#mQ2vLp7$Zw4Rt8Nb", tags=["dev", "work"], favorite=True,
             totp="JBSWY3DPEHPK3PXP"),
        dict(title="Personal Email", username="raza@example.com", category="email",
             url="https://mail.example.com", password="summer2024!", tags=["personal"]),
        dict(title="Bank Portal", username="raza-8842", category="banking",
             url="https://bank.example.com", password="Xk9#mQ2vLp7$Zw4Rt8Nb", totp="KRSXG5CTMVRXEZLU"),
        dict(title="Home WiFi", username="admin", category="wifi",
             password="0123456789", tags=["home"]),
    ]
    if quick:
        samples = samples[:2]
    for sample in samples:
        engine.add(save=False, **sample)
    engine.save()


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def _add_common_flags(target: argparse.ArgumentParser) -> None:
    """Register the global flags on a parser.

    ``default=argparse.SUPPRESS`` is deliberate: when the same flag is defined
    on both the main parser and a subparser, argparse's subparser only sets the
    attribute when the user actually passed it. That lets ``--vault``/``--json``
    work on either side of the command without the subparser's default silently
    overwriting a value already given before the command.
    """
    target.add_argument(
        "--vault", default=argparse.SUPPRESS,
        help="vault file path (default: ~/.vaultguard/vault.json)",
    )
    target.add_argument(
        "--master", default=argparse.SUPPRESS,
        help="master password (prefer --master-stdin or VAULTGUARD_MASTER)",
    )
    target.add_argument(
        "--master-stdin", action="store_true", default=argparse.SUPPRESS,
        help="read master password from stdin",
    )
    target.add_argument(
        "--iterations", type=int, default=argparse.SUPPRESS,
        help="PBKDF2 iteration override (tests/demos)",
    )
    target.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaultguard",
        description="VAULTGUARD — encrypted, offline password vault agent.",
        epilog="Docs: see README.md. Never share your master password.",
    )
    _add_common_flags(parser)

    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="create a new vault")
    p.add_argument("--force", action="store_true", help="overwrite an existing vault")
    p.add_argument("--demo", action="store_true", help="seed sample credentials")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add", help="add an entry")
    p.add_argument("title")
    p.add_argument("--username", "-u", default="")
    p.add_argument("--password", "-p", default="")
    p.add_argument("--url", default="")
    p.add_argument("--category", "-c", default="login", choices=list(CATEGORIES))
    p.add_argument("--notes", "-n", default="")
    p.add_argument("--tags", "-t", default="")
    p.add_argument("--totp", default="")
    p.add_argument("--favorite", "-f", action="store_true")
    p.add_argument("--generate", "-g", action="store_true", help="generate the password for me")
    p.add_argument("--length", "-l", type=int, default=20)
    p.set_defaults(func=cmd_add)

    sub.add_parser("list", help="list all entries").set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="search and filter entries")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--category", "-c", default="")
    p.add_argument("--tag", "-t", default="")
    p.add_argument("--favorite", "-f", action="store_true")
    p.add_argument("--weak", action="store_true")
    p.add_argument("--stale", action="store_true")
    p.add_argument("--sort", default="title", choices=sorted(SORT_KEYS))
    p.add_argument("--desc", action="store_true")
    p.add_argument("--limit", "-l", type=int, default=0)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", help="show one entry")
    p.add_argument("entry_id")
    p.add_argument("--reveal", action="store_true", help="include the plaintext secret")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("reveal", help="print just the secret for one entry")
    p.add_argument("entry_id")
    p.add_argument("--copy", action="store_true", help="copy to clipboard when pyperclip is installed")
    p.set_defaults(func=cmd_reveal)

    p = sub.add_parser("update", help="modify an entry")
    p.add_argument("entry_id")
    p.add_argument("--title")
    p.add_argument("--username", "-u")
    p.add_argument("--password", "-p")
    p.add_argument("--url")
    p.add_argument("--category", "-c")
    p.add_argument("--notes", "-n")
    p.add_argument("--tags", "-t")
    p.add_argument("--totp")
    p.add_argument("--favorite", "-f", action="store_true")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("delete", help="delete an entry")
    p.add_argument("entry_id")
    p.set_defaults(func=cmd_delete)

    sub.add_parser("audit", help="run the security audit").set_defaults(func=cmd_audit)
    sub.add_parser("stats", help="vault statistics").set_defaults(func=cmd_stats)

    p = sub.add_parser("suggest", help="suggest a strong password")
    p.add_argument("--length", "-l", type=int, default=20)
    p.add_argument("--no-symbols", action="store_true")
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("generate", help="generate a password, passphrase or PIN")
    p.add_argument("kind", nargs="?", default="password", choices=["password", "passphrase", "pin"])
    p.add_argument("--length", "-l", type=int, default=20)
    p.add_argument("--words", "-w", type=int, default=4)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("check", help="score an arbitrary password")
    p.add_argument("password", nargs="?", default=None)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("export", help="export entries to JSON")
    p.add_argument("output")
    p.add_argument("--masked", action="store_true", help="omit plaintext secrets")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="import entries from JSON")
    p.add_argument("source")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("backup", help="copy the encrypted vault file")
    p.add_argument("destination")
    p.set_defaults(func=cmd_backup)

    sub.add_parser("info", help="vault metadata (works while locked)").set_defaults(func=cmd_info)

    p = sub.add_parser("rekey", help="change the master password")
    p.add_argument("--new-master", default=None)
    p.set_defaults(func=cmd_rekey)

    sub.add_parser("destroy", help="delete the vault file").set_defaults(func=cmd_destroy)

    p = sub.add_parser("demo", help="seed a throwaway demo vault and print an audit")
    p.set_defaults(func=cmd_demo)

    # Accept the global flags on either side of the command name.
    for subparser in sub.choices.values():
        _add_common_flags(subparser)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Flags suppressed on one parser may not exist on the namespace at all.
    for key, default in (
        ("vault", None),
        ("master", None),
        ("master_stdin", False),
        ("iterations", None),
        ("json", False),
    ):
        if not hasattr(args, key):
            setattr(args, key, default)
    if not getattr(args, "command", None):
        print(BANNER)
        parser.print_help()
        return EXIT_OK
    try:
        return int(args.func(args))
    except VaultAuthError as exc:
        print(f"  auth error: {exc}", file=sys.stderr)
        return EXIT_AUTH
    except (VaultError, VaultFormatError, GeneratorError, KeyError, ValueError) as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        print("\n  interrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
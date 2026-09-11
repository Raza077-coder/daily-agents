"""PANTRY command-line interface.

Everything the engine can do is reachable from here, and every command supports
``--json`` so the output can be piped into other tooling.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .engine import PantryEngine, load_profile, sample_pantry
from .library import RecipeLibrary
from .shopping import (
    render_match_text,
    render_plan_text,
    render_shopping_list_text,
)
from .units import canonical_name

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 3


# --------------------------------------------------------------------- helpers
def _emit(payload: Any, as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(text)


def _read_pantry_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"pantry file not found: {path}")
    entries: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                entries.append(stripped)
    return entries


def _build_engine(args: argparse.Namespace) -> PantryEngine:
    profile = load_profile(getattr(args, "profile", None))
    staples = set(profile.get("staples", [])) if profile.get("staples") else None
    engine = PantryEngine(profile=profile, staples=staples)

    pantry_file = getattr(args, "pantry", None)
    if pantry_file:
        engine.load_pantry(_read_pantry_file(pantry_file))
    elif getattr(args, "demo", False):
        engine.load_pantry(sample_pantry())
    return engine


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pantry",
        help="Path to a pantry file (one item per line).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use the bundled sample pantry instead of a pantry file.",
    )
    parser.add_argument(
        "--profile",
        help="Path to a household profile JSON (defaults to the bundled one).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )


# ------------------------------------------------------------------- commands
def cmd_cook(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    results = engine.cook_now(
        limit=args.limit,
        max_minutes=args.max_minutes,
        vegetarian=args.vegetarian,
        cuisine=args.cuisine,
        include_partial=not args.only_cookable,
    )
    if args.as_json:
        _emit([m.to_dict() for m in results], True)
    else:
        _emit(None, False, render_match_text(results, limit=args.limit))
    return EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    slots = [s.strip() for s in args.slots.split(",") if s.strip()]
    payload = engine.plan_week(
        days=args.days,
        slots=slots,
        max_minutes=args.max_minutes,
        vegetarian=args.vegetarian,
        cuisine=args.cuisine,
        fill_missing=not args.leftovers_only,
    )
    if args.as_json:
        _emit(payload, True)
    else:
        text = render_plan_text(payload["entries"])
        summary = payload["summary"]
        text += (
            f"\nPlan totals \u00b7 {summary['total_minutes']} min of cooking \u00b7 "
            f"{summary['distinct_recipes']} distinct recipes"
        )
        _emit(None, False, text)
    return EXIT_OK


def cmd_shop(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    slots = [s.strip() for s in args.slots.split(",") if s.strip()]
    payload = engine.shopping_list(
        days=args.days,
        slots=slots,
        max_minutes=args.max_minutes,
        vegetarian=args.vegetarian,
        cuisine=args.cuisine,
        include_optional=args.include_optional,
    )
    if args.as_json:
        _emit(payload, True)
    else:
        _emit(None, False, render_shopping_list_text(payload))
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    recipes = engine.search(args.text, limit=args.limit)
    if args.as_json:
        _emit([r.to_dict() for r in recipes], True)
        return EXIT_OK
    if not recipes:
        print(f"No recipes matched {args.text!r}.")
        return EXIT_NOT_FOUND
    lines = [f"\U0001f50e {len(recipes)} recipe(s) matching {args.text!r}", "=" * 46]
    for recipe in recipes:
        lines.append(
            f"  {recipe.id:<26} {recipe.name} \u00b7 {recipe.minutes} min \u00b7 {recipe.cuisine}"
        )
    _emit(None, False, "\n".join(lines))
    return EXIT_OK


def cmd_recipe(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    recipe = engine.scale(args.recipe_id, args.servings) if args.servings else engine.library.get(args.recipe_id)
    if recipe is None:
        print(f"Recipe not found: {args.recipe_id}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if args.as_json:
        _emit(recipe.to_dict(), True)
        return EXIT_OK

    lines = [f"\U0001f4d6 {recipe.name}", "=" * 46]
    lines.append(
        f"  {recipe.cuisine} \u00b7 serves {recipe.servings} \u00b7 {recipe.minutes} min \u00b7 {recipe.difficulty}"
    )
    if recipe.tags:
        lines.append(f"  tags: {', '.join(recipe.tags)}")
    lines.append("")
    lines.append("  Ingredients")
    for ing in recipe.ingredients:
        lines.append(f"    \u2022 {ing.display()}")
    lines.append("")
    lines.append("  Method")
    for index, step in enumerate(recipe.steps, start=1):
        lines.append(f"    {index}. {step}")
    if recipe.notes:
        lines.append("")
        lines.append(f"  Note: {recipe.notes}")
    _emit(None, False, "\n".join(lines))
    return EXIT_OK


def cmd_pantry(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    action = args.action

    if action == "list":
        if args.as_json:
            _emit({"items": engine.pantry_list(), "expiring": engine.expiring(3)}, True)
        else:
            lines = ["\U0001f955 PANTRY", "=" * 46]
            for item in engine.pantry_list():
                lines.append(f"  \u2022 {item['display']:<32} [{item['aisle']}]")
            expiring = engine.expiring(3)
            if expiring:
                lines.append("")
                lines.append("  \u23f3 Use soon")
                for row in expiring:
                    lines.append(f"     {row['name']} \u2014 {row['expires_in_days']} day(s)")
            _emit(None, False, "\n".join(lines))
        return EXIT_OK

    if action == "add":
        for entry in args.items:
            engine.add_pantry_item(entry)
        engine.index.expiring  # touch, keeps the attribute meaningful
        if args.expires_in is not None:
            for entry in args.items:
                engine.set_expiry(canonical_name(entry), args.expires_in)
        _emit(
            {"added": args.items, "pantry_size": len(engine.index)},
            args.as_json,
            f"Added {len(args.items)} item(s). Pantry now holds {len(engine.index)}.",
        )
        return EXIT_OK

    if action == "remove":
        removed = [name for name in args.items if engine.remove_pantry_item(name)]
        missing = [name for name in args.items if name not in removed]
        payload = {"removed": removed, "not_found": missing, "pantry_size": len(engine.index)}
        _emit(
            payload,
            args.as_json,
            f"Removed {len(removed)} item(s)."
            + (f" Not found: {', '.join(missing)}" if missing else ""),
        )
        return EXIT_OK

    if action == "expiring":
        rows = engine.expiring(args.within_days)
        _emit(rows, args.as_json, f"{len(rows)} item(s) expiring within {args.within_days} days.")
        return EXIT_OK

    print(f"Unknown pantry action: {action}", file=sys.stderr)
    return EXIT_ERROR


def cmd_profile(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    if args.as_json:
        _emit(engine.profile_summary(), True)
    else:
        _emit(None, False, engine.profile_text())
    return EXIT_OK


def cmd_library(args: argparse.Namespace) -> int:
    library = RecipeLibrary.bundled()
    stats = library.stats() if args.stats else None
    recipes = library.filter(cuisine=args.cuisine, vegetarian=args.vegetarian)

    if args.as_json:
        _emit(
            {"stats": stats, "recipes": [r.to_dict() for r in recipes], "count": len(recipes)},
            True,
        )
        return EXIT_OK

    lines = [f"\U0001f4da RECIPE LIBRARY \u2014 {len(library)} recipes", "=" * 60]
    if stats:
        lines.append(f"  cuisines : {stats['cuisines']}")
        lines.append(f"  avg time : {stats['avg_minutes']} min")
        lines.append("")
    for recipe in recipes:
        lines.append(
            f"  {recipe.id:<26} {recipe.name:<36} {recipe.minutes:>3} min"
        )
    _emit(None, False, "\n".join(lines))
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    payload = engine.stats()
    if args.as_json:
        _emit(payload, True)
        return EXIT_OK
    lib = payload["library"]
    pan = payload["pantry"]
    lines = [
        "\U0001f4ca PANTRY ENGINE STATS",
        "=" * 46,
        f"  recipes          : {lib['recipes']}",
        f"  distinct items   : {lib['ingredients_indexed']}",
        f"  avg cook time    : {lib['avg_minutes']} min",
        f"  pantry items     : {pan['items']}",
        f"  quantified       : {pan['quantified']}",
        f"  unquantified     : {pan['unquantified']}",
        f"  expiring in 3d   : {pan['expiring_soon']}",
    ]
    _emit(None, False, "\n".join(lines))
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    """One-shot tour: pantry in, ranked recipes, plan, and shopping list out."""
    engine = PantryEngine(profile=load_profile())
    engine.load_pantry(sample_pantry())

    steps: List[str] = []
    steps.append("\U0001f955 STEP 1 \u2014 Sample pantry loaded")
    steps.append(f"   {len(engine.index)} items on hand")

    ranked = engine.cook_now(limit=5)
    steps.append("")
    steps.append("\U0001f9d1\u200d\U0001f373 STEP 2 \u2014 Best matches right now")
    steps.append(render_match_text(ranked))

    plan = engine.plan_week(days=5, slots=("dinner",))
    steps.append("")
    steps.append("\U0001f5d3  STEP 3 \u2014 Five dinners")
    steps.append(render_plan_text(plan["entries"]))

    shopping = engine.shopping_list(days=5, slots=("dinner",))
    steps.append("")
    steps.append("\U0001f6d2 STEP 4 \u2014 Shopping list")
    steps.append(render_shopping_list_text(shopping))

    print("\n".join(steps))
    return EXIT_OK


def cmd_seed(args: argparse.Namespace) -> int:
    """Write a pantry file so you can start editing your own."""
    target = args.output
    if os.path.exists(target) and not args.force:
        print(f"{target} already exists \u2014 refusing to overwrite (use --force).", file=sys.stderr)
        return EXIT_ERROR
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(
            "# PANTRY pantry file \u2014 one item per line.\n"
            "# '200 g rice' = an amount.  'olive oil' = I have some.\n\n"
        )
        for entry in sample_pantry():
            handle.write(f"{entry}\n")
    print(f"Wrote {target} with {len(sample_pantry())} starter items.")
    return EXIT_OK


# ----------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pantry",
        description="PANTRY \u2014 offline recipe finder, meal planner and shopping list builder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  pantry demo\n"
            "  pantry cook --demo --limit 8\n"
            "  pantry plan --pantry my_pantry.txt --days 5 --slots dinner\n"
            "  pantry shop --pantry my_pantry.txt --days 3 --slots breakfast,lunch,dinner\n"
            "  pantry pantry add --pantry my_pantry.txt '500 g rice' 'olive oil'\n"
        ),
    )
    parser.add_argument("--version", action="version", version="pantry 1.0.0")
    sub = parser.add_subparsers(dest="command")

    p_cook = sub.add_parser("cook", help="What can I cook right now?")
    _add_common(p_cook)
    p_cook.add_argument("--limit", type=int, default=10)
    p_cook.add_argument("--max-minutes", type=int, dest="max_minutes")
    p_cook.add_argument("--vegetarian", action="store_true")
    p_cook.add_argument("--cuisine")
    p_cook.add_argument("--only-cookable", action="store_true")
    p_cook.set_defaults(func=cmd_cook)

    p_plan = sub.add_parser("plan", help="Build a multi-day meal plan.")
    _add_common(p_plan)
    p_plan.add_argument("--days", type=int, default=5)
    p_plan.add_argument("--slots", default="dinner", help="Comma-separated: dinner,breakfast,lunch")
    p_plan.add_argument("--max-minutes", type=int, dest="max_minutes")
    p_plan.add_argument("--vegetarian", action="store_true")
    p_plan.add_argument("--cuisine")
    p_plan.add_argument(
        "--leftovers-only",
        action="store_true",
        help="Restrict the plan to recipes that need no shopping at all.",
    )
    p_plan.set_defaults(func=cmd_plan)

    p_shop = sub.add_parser("shop", help="Shopping list for a generated plan.")
    _add_common(p_shop)
    p_shop.add_argument("--days", type=int, default=5)
    p_shop.add_argument("--slots", default="dinner")
    p_shop.add_argument("--max-minutes", type=int, dest="max_minutes")
    p_shop.add_argument("--vegetarian", action="store_true")
    p_shop.add_argument("--cuisine")
    p_shop.add_argument("--include-optional", action="store_true")
    p_shop.set_defaults(func=cmd_shop)

    p_search = sub.add_parser("search", help="Keyword search across the library.")
    _add_common(p_search)
    p_search.add_argument("text")
    p_search.add_argument("--limit", type=int)
    p_search.set_defaults(func=cmd_search)

    p_recipe = sub.add_parser("recipe", help="Show one recipe in full.")
    _add_common(p_recipe)
    p_recipe.add_argument("recipe_id")
    p_recipe.add_argument("--servings", type=int)
    p_recipe.set_defaults(func=cmd_recipe)

    p_pantry = sub.add_parser("pantry", help="Inspect or edit your pantry.")
    _add_common(p_pantry)
    p_pantry.add_argument("action", choices=["list", "add", "remove", "expiring"])
    p_pantry.add_argument("items", nargs="*", help="Items for add/remove.")
    p_pantry.add_argument("--expires-in", type=int, dest="expires_in")
    p_pantry.add_argument("--within-days", type=int, dest="within_days", default=3)
    p_pantry.set_defaults(func=cmd_pantry)

    p_profile = sub.add_parser("profile", help="Show the household profile.")
    _add_common(p_profile)
    p_profile.set_defaults(func=cmd_profile)

    p_library = sub.add_parser("library", help="List the recipe library.")
    _add_common(p_library)
    p_library.add_argument("--stats", action="store_true")
    p_library.add_argument("--cuisine")
    p_library.add_argument("--vegetarian", action="store_true")
    p_library.set_defaults(func=cmd_library)

    p_stats = sub.add_parser("stats", help="Library and pantry counters.")
    _add_common(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    p_demo = sub.add_parser("demo", help="Guided four-step tour on the sample pantry.")
    _add_common(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_seed = sub.add_parser("seed", help="Write a starter pantry file.")
    p_seed.add_argument("--output", default="my_pantry.txt")
    p_seed.add_argument("--force", action="store_true")
    p_seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    # ``pantry add --pantry FILE ITEM...`` mixes optionals with a trailing
    # positional, and argparse's greedy optional handling rejects that ordering
    # (parse_intermixed_args cannot help here because the command uses
    # nargs="*"). So parse what we recognise and fold the leftovers back into
    # the positional list.
    args, extras = parser.parse_known_args(argv)
    if extras:
        items = getattr(args, "items", None)
        if items is not None:
            args.items = list(items) + [e for e in extras if e != "--"]
        else:
            parser.error("unrecognized arguments: %s" % " ".join(extras))

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    if not hasattr(args, "as_json"):
        args.as_json = False

    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:  # pragma: no cover - piping into `head`
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

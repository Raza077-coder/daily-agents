"""Shopping-list building: scale, merge, subtract stock, group by aisle.

The important guarantee is *no double counting*: if two planned dinners both
need garlic, the list shows the combined amount once. Amounts are merged only
when their units are convertible; otherwise the entries stay separate rather
than being added together into a meaningless number.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from .aisles import group_by_aisle
from .library import PantryIndex
from .models import DEFAULT_STAPLES, Ingredient, Recipe, ShoppingItem
from .units import canonical_name, humanize

_EPSILON = 1e-9


def collect_requirements(
    recipes: Iterable[Recipe],
    servings: Optional[Dict[str, int]] = None,
    include_optional: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Merge the ingredient needs of several recipes keyed by canonical name.

    Every distinct ``(canonical, unit, label)`` pair accumulates separately, so
    incompatible units surface as separate lines instead of a bogus total.
    """
    servings = servings or {}
    merged: Dict[str, Dict[str, Any]] = {}

    for recipe in recipes:
        scaled = recipe.scale_to(servings[recipe.id]) if recipe.id in servings else recipe
        for ingredient in scaled.ingredients:
            if ingredient.optional and not include_optional:
                continue
            if not ingredient.canonical:
                continue
            key = f"{ingredient.canonical}|{ingredient.base_unit}"
            slot = merged.setdefault(
                key,
                {
                    "canonical": ingredient.canonical,
                    "name": ingredient.name,
                    "unit": ingredient.base_unit,
                    "qty": 0.0,
                    "sources": [],
                },
            )
            slot["qty"] += ingredient.base_qty or 0.0
            if recipe.name not in slot["sources"]:
                slot["sources"].append(recipe.name)

    return merged


def subtract_pantry(
    requirements: Dict[str, Dict[str, Any]],
    index: PantryIndex,
    staples: Optional[Set[str]] = None,
) -> List[ShoppingItem]:
    """Remove what is already in stock; return only what must be bought."""
    staples = set(DEFAULT_STAPLES) if staples is None else set(staples)
    outstanding: List[ShoppingItem] = []

    for slot in sorted(requirements.values(), key=lambda s: (s["canonical"], s["unit"])):
        if slot["canonical"] in staples:
            continue

        need = slot["qty"]
        candidates = index.candidates(slot["name"])

        if candidates:
            # An unquantified pantry entry means "covered", full stop.
            if any(not c.qty_known for c in candidates):
                continue
            matching = [c for c in candidates if c.base_unit == slot["unit"]]
            if matching:
                have = sum(c.base_qty or 0.0 for c in matching)
                shortfall = need - have
                if shortfall <= _EPSILON:
                    continue
                need = shortfall
            # No matching unit: fall through and buy the full amount, because
            # we cannot honestly claim the stock covers it.

        outstanding.append(
            ShoppingItem(
                canonical=slot["canonical"],
                name=slot["name"],
                qty=need,
                unit=slot["unit"],
                sources=list(slot["sources"]),
            )
        )

    return outstanding


def build_shopping_list(
    recipes: Iterable[Recipe],
    index: PantryIndex,
    servings: Optional[Dict[str, int]] = None,
    include_optional: bool = False,
    staples: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Full pipeline: gather -> merge -> subtract stock -> group by aisle."""
    requirements = collect_requirements(recipes, servings, include_optional)
    outstanding = subtract_pantry(requirements, index, staples)
    rows = [item.to_dict() for item in outstanding]

    return {
        "lines": len(rows),
        "items": rows,
        "aisles": group_by_aisle(rows),
        "subtotal_needed": len(requirements),
        "covered_by_pantry": len(requirements) - len(rows),
    }


def render_shopping_list_text(payload: Dict[str, Any]) -> str:
    """Render a shopping list as aligned plain text for the terminal."""
    lines: List[str] = []
    lines.append("\U0001f6d2 SHOPPING LIST")
    lines.append("=" * 46)

    if not payload.get("lines"):
        lines.append("Nothing to buy \u2014 your pantry covers every planned recipe.")
        return "\n".join(lines)

    for group in payload.get("aisles", []):
        lines.append("")
        lines.append(f"\u2014 {group['aisle'].upper()} \u2014")
        for item in group["items"]:
            lines.append(f"  [ ] {item['display']}")

    lines.append("")
    lines.append("-" * 46)
    lines.append(
        f"{payload['lines']} to buy \u00b7 {payload['covered_by_pantry']} already in stock"
    )
    return "\n".join(lines)


def render_plan_text(entries: List[Dict[str, Any]]) -> str:
    """Render a meal plan as day blocks with cookability markers."""
    lines: List[str] = []
    lines.append("\U0001f37d  MEAL PLAN")
    lines.append("=" * 46)

    if not entries:
        lines.append("No plan could be built from the current filters.")
        return "\n".join(lines)

    current_day: Optional[int] = None
    for entry in entries:
        if entry["day"] != current_day:
            current_day = entry["day"]
            lines.append("")
            lines.append(f"Day {current_day}")
        marker = "\u2705" if entry.get("can_cook") else "\U0001f6d2"
        lines.append(
            f"  {marker} {entry['meal']:<9} {entry['name']} "
            f"({entry['minutes']} min)"
        )
        if entry.get("missing"):
            lines.append(f"       missing: {', '.join(entry['missing'])}")

    lines.append("")
    lines.append("-" * 46)
    cookable = sum(1 for e in entries if e.get("can_cook"))
    lines.append(
        f"{len(entries)} meals \u00b7 {cookable} cookable now \u00b7 "
        f"{len(entries) - cookable} need shopping"
    )
    return "\n".join(lines)


def render_match_text(results: List[Any], limit: Optional[int] = None) -> str:
    """Render ranked match results with a coverage bar."""
    lines: List[str] = []
    lines.append("\U0001f9d1\u200d\U0001f373 WHAT CAN I COOK?")
    lines.append("=" * 46)

    shown = results if limit is None else results[:limit]
    if not shown:
        lines.append("No recipes matched those filters.")
        return "\n".join(lines)

    for index, match in enumerate(shown, start=1):
        filled = int(round(match.coverage * 10))
        bar = "\u2588" * filled + "\u2591" * (10 - filled)
        status = "ready to cook" if match.can_cook else f"missing {len(match.missing)}"
        lines.append(
            f"{index:>2}. {match.recipe.name} \u2014 {bar} "
            f"{int(round(match.coverage * 100))}% \u00b7 {status}"
        )
        if match.missing:
            lines.append(f"      buy: {', '.join(i.name for i in match.missing)}")
        if match.substitutes:
            for sub in match.substitutes:
                lines.append(
                    f"      sub: {sub['ingredient']} \u2190 using {sub['using']}"
                )

    lines.append("")
    lines.append("-" * 46)
    ready = sum(1 for m in results if m.can_cook)
    lines.append(f"{ready} of {len(results)} recipes need no shopping")
    return "\n".join(lines)

"""The PANTRY facade -- one object that ties library, pantry and planning together.

This is the single entry point used by the CLI, the REST API and the test suite.
Nothing here touches the network or the clock, so every call is reproducible.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .library import (
    PantryIndex,
    RecipeLibrary,
    load_sample_pantry_text,
    match_recipe,
)
from .models import (
    DEFAULT_STAPLES,
    Ingredient,
    MatchResult,
    PantryItem,
    Recipe,
)
from .planner import MealPlanner, summarize_plan
from .shopping import build_shopping_list
from .units import canonical_name

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PROFILE_PATH = os.path.join(DATA_DIR, "profile.json")


class PantryEngine:
    """Offline recipe finder, meal planner and shopping-list builder."""

    def __init__(
        self,
        library: Optional[RecipeLibrary] = None,
        pantry: Optional[Iterable[str]] = None,
        staples: Optional[Set[str]] = None,
        prioritize_expiring: bool = True,
        profile: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.library = library or RecipeLibrary.bundled()
        self.staples = set(DEFAULT_STAPLES) if staples is None else set(staples)
        self.prioritize_expiring = prioritize_expiring
        self.profile: Dict[str, Any] = profile or load_profile()

        self.index = PantryIndex()
        if pantry:
            self.index.extend(pantry)

    # ------------------------------------------------------------------ pantry
    def load_pantry(self, entries: Iterable[str]) -> "PantryEngine":
        """Replace the pantry wholesale from free-text lines.

        Staples are deliberately *not* inserted here: they are handled by the
        matching rules directly, so the pantry list always reflects exactly
        what the cook wrote down.
        """
        self.index = PantryIndex()
        self.index.extend(list(entries))
        return self

    def add_pantry_items(self, entries: Iterable[str]) -> "PantryEngine":
        self.index.extend(list(entries))
        return self

    def add_pantry_item(self, entry: str) -> "PantryEngine":
        self.index.add(PantryItem.from_text(entry))
        return self

    def remove_pantry_item(self, name: str) -> bool:
        return self.index.remove(name)

    def set_expiry(self, name: str, days: int) -> "PantryEngine":
        """Mark an ingredient as expiring in ``days`` days."""
        key = canonical_name(name)
        self.index.expiring[key] = int(days)
        for item in self.index.items:
            if item.canonical == key:
                item.expires_in_days = int(days)
        return self

    def pantry_list(self) -> List[Dict[str, Any]]:
        return self.index.to_list()

    def expiring(self, within_days: int = 3) -> List[Dict[str, Any]]:
        return self.index.expiring_items(within_days)

    # ------------------------------------------------------------------- cook
    def cook_now(
        self,
        limit: Optional[int] = None,
        max_minutes: Optional[int] = None,
        vegetarian: bool = False,
        cuisine: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        include_partial: bool = True,
        only_cookable: bool = False,
    ) -> List[MatchResult]:
        """Recipes ranked by how completely the pantry covers them."""
        recipes = self.library.filter(
            max_minutes=max_minutes,
            vegetarian=vegetarian,
            cuisine=cuisine,
            tags=tags,
        )
        ranked = self.library.rank(
            self.index, recipes, prioritize_expiring=self.prioritize_expiring
        )
        if only_cookable or not include_partial:
            ranked = [m for m in ranked if m.can_cook]
        if limit is not None:
            ranked = ranked[: max(0, int(limit))]
        return ranked

    def match(self, recipe_id: str) -> Optional[MatchResult]:
        recipe = self.library.get(recipe_id)
        if recipe is None:
            return None
        return match_recipe(recipe, self.index, self.staples)

    def search(self, text: str, limit: Optional[int] = None) -> List[Recipe]:
        found = self.library.filter(text=text)
        return found if limit is None else found[: max(0, int(limit))]

    def scale(self, recipe_id: str, servings: int) -> Optional[Recipe]:
        recipe = self.library.get(recipe_id)
        return None if recipe is None else recipe.scale_to(servings)

    # -------------------------------------------------------------------- plan
    def planner(self) -> MealPlanner:
        return MealPlanner(
            self.library, self.index, prioritize_expiring=self.prioritize_expiring
        )

    def plan_week(
        self,
        days: int = 5,
        slots: Sequence[str] = ("dinner",),
        max_minutes: Optional[int] = None,
        vegetarian: bool = False,
        cuisine: Optional[str] = None,
        prefer_cookable: bool = True,
        fill_missing: bool = True,
    ) -> Dict[str, Any]:
        entries = self.planner().plan(
            days=days,
            slots=slots,
            max_minutes=max_minutes,
            vegetarian=vegetarian,
            cuisine=cuisine,
            prefer_cookable=prefer_cookable,
            fill_missing=fill_missing,
        )
        return {"entries": entries, "summary": summarize_plan(entries)}

    def leftovers_plan(self, days: int = 5) -> Dict[str, Any]:
        entries = self.planner().leftovers_plan(days=days)
        return {"entries": entries, "summary": summarize_plan(entries)}

    # ----------------------------------------------------------- shopping list
    def shopping_list(
        self,
        days: int = 5,
        slots: Sequence[str] = ("dinner",),
        max_minutes: Optional[int] = None,
        vegetarian: bool = False,
        cuisine: Optional[str] = None,
        include_optional: bool = False,
        entries: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build the shopping list for a freshly generated plan."""
        plan = entries if entries is not None else self.plan_week(
            days=days,
            slots=slots,
            max_minutes=max_minutes,
            vegetarian=vegetarian,
            cuisine=cuisine,
        )["entries"]

        recipes: List[Recipe] = []
        servings: Dict[str, int] = {}
        for entry in plan:
            recipe = self.library.get(entry["recipe_id"])
            if recipe is None:
                continue
            recipes.append(recipe)
            servings[recipe.id] = int(entry.get("servings", recipe.servings))

        payload = build_shopping_list(
            recipes,
            self.index,
            servings=servings,
            include_optional=include_optional,
            staples=self.staples,
        )
        payload["for_plan"] = summarize_plan(plan)
        return payload

    # ------------------------------------------------------------------ report
    def stats(self) -> Dict[str, Any]:
        return {
            "library": self.library.stats(),
            "pantry": {
                "items": len(self.index),
                "quantified": sum(1 for i in self.index.items if i.qty_known),
                "unquantified": sum(1 for i in self.index.items if not i.qty_known),
                "expiring_soon": len(self.expiring(3)),
            },
        }

    def profile_summary(self) -> Dict[str, Any]:
        """Headline numbers from the bundled seed profile."""
        return {
            "household": self.profile.get("household", {}),
            "diet": self.profile.get("diet", {}),
            "allergies": self.profile.get("allergies", []),
            "dislikes": self.profile.get("dislikes", []),
            "preferences": self.profile.get("preferences", []),
            "cook_days": self.profile.get("week", {}).get("cook_days", []),
            "shop_days": self.profile.get("week", {}).get("shop_days", []),
        }

    def profile_text(self) -> str:
        data = self.profile_summary()
        household = data["household"]
        diet = data["diet"]
        lines = [
            "\U0001f468\u200d\U0001f373 HOUSEHOLD PROFILE",
            "=" * 46,
            f"  household     : {household.get('label', 'unknown')} "
            f"({household.get('adults', 0)} adults, {household.get('children', 0)} children)",
            f"  default serves: {household.get('default_servings', 2)}",
            f"  diet          : {diet.get('style', 'omnivore')}",
            f"  allergies     : {', '.join(data['allergies']) or 'none'}",
            f"  dislikes      : {', '.join(data['dislikes']) or 'none'}",
            f"  preferences   : {', '.join(data['preferences']) or 'none'}",
            f"  cook days     : {', '.join(data['cook_days']) or 'any'}",
            f"  shop days     : {', '.join(data['shop_days']) or 'any'}",
        ]
        return "\n".join(lines)


def staple_items(staples: Set[str]) -> List[PantryItem]:
    """Pantry entries for staples: present, amount unspecified."""
    return [
        PantryItem(name=name, qty=1.0, unit="piece", qty_known=False)
        for name in sorted(staples)
    ]


def load_profile(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the bundled household profile; never fail, always return a dict."""
    target = path or PROFILE_PATH
    if not os.path.exists(target):
        return {}
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def sample_pantry() -> List[str]:
    """The demo pantry used by ``pantry demo`` and the web HUD."""
    return load_sample_pantry_text()


def sample_engine(**kwargs: Any) -> PantryEngine:
    """An engine preloaded with the sample pantry -- handy for demos and tests."""
    engine = PantryEngine(**kwargs)
    engine.load_pantry(sample_pantry())
    return engine

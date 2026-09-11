"""Deterministic meal planning: pick recipes into day/meal slots.

The planner is a greedy, seeded selector. "Seeded" here means the input to the
rotation is deterministic (day, slot, sorted candidate ids) rather than a random
number generator, so the same pantry always yields the same plan.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .library import PantryIndex, RecipeLibrary
from .models import MatchResult, Recipe

MEAL_SLOTS = ("breakfast", "lunch", "dinner")

# Which tags count for which slot. A recipe can serve several slots.
SLOT_TAGS: Dict[str, Tuple[str, ...]] = {
    "breakfast": ("breakfast", "quick", "vegetarian", "vegan"),
    "lunch": ("lunch", "quick", "salad", "soup", "vegetarian", "vegan"),
    "dinner": ("dinner", "one-pot", "comfort", "high-protein", "vegetarian", "vegan"),
}

# Slot weighting: we allow at most this many cooked (non-leftover) entries per day.
MAX_PER_DAY = 3


class MealPlanner:
    """Turns a ranked set of matches into a day-by-day plan."""

    def __init__(
        self,
        library: RecipeLibrary,
        index: PantryIndex,
        prioritize_expiring: bool = True,
    ) -> None:
        self.library = library
        self.index = index
        self.prioritize_expiring = prioritize_expiring

    # ------------------------------------------------------------------ helpers
    def _candidates(self, slots: Sequence[str]) -> List[MatchResult]:
        """Ranked matches whose tags suit at least one of ``slots``."""
        allowed = {tag for slot in slots for tag in SLOT_TAGS.get(slot, ())}
        pool = [
            r
            for r in self.library.all()
            if allowed & {t.lower() for t in r.tags}
        ]
        if not pool:
            pool = self.library.all()
        return self.library.rank(
            self.index, pool, prioritize_expiring=self.prioritize_expiring
        )

    @staticmethod
    def _covered_ingredients(matches: Iterable[MatchResult]) -> set:
        covered = set()
        for match in matches:
            covered.update(i.canonical for i in match.have)
        return covered

    # -------------------------------------------------------------------- plans
    def plan(
        self,
        days: int = 5,
        slots: Sequence[str] = ("dinner",),
        max_minutes: Optional[int] = None,
        vegetarian: bool = False,
        cuisine: Optional[str] = None,
        exclude_ids: Optional[Sequence[str]] = None,
        prefer_cookable: bool = True,
        fill_missing: bool = True,
        start_day: int = 1,
    ) -> List[Dict[str, Any]]:
        """Build a plan and return it as a list of serialisable rows.

        ``fill_missing=False`` restricts the plan to recipes that can be cooked
        from the pantry as-is (useful for a "use it up" week).
        ``prefer_cookable=True`` still allows a gap when nothing cookable fits,
        unless ``fill_missing`` is False.
        """
        days = max(1, int(days))
        slots = tuple(slots) or ("dinner",)
        banned = set(exclude_ids or [])
        filters: Dict[str, Any] = {}
        if max_minutes is not None:
            filters["max_minutes"] = max_minutes
        if vegetarian:
            filters["vegetarian"] = True
        if cuisine:
            filters["cuisine"] = cuisine

        raw_candidates = self.library.filter(**filters)
        allowed = {tag for slot in slots for tag in SLOT_TAGS.get(slot, ())}
        scoped = [
            r for r in raw_candidates if allowed & {t.lower() for t in r.tags}
        ]
        if not scoped:
            scoped = raw_candidates

        ranked_all = self.library.rank(
            self.index, scoped, prioritize_expiring=self.prioritize_expiring
        )
        by_id = {m.recipe.id: m for m in ranked_all}

        chosen: List[Dict[str, Any]] = []
        used_ids: List[str] = list(banned)
        covered = self._covered_ingredients(
            m for m in ranked_all if m.recipe.id in banned
        )
        entries_per_day: Dict[int, int] = {}

        for offset in range(days):
            day = start_day + offset
            for slot in slots:
                if entries_per_day.get(day, 0) >= MAX_PER_DAY:
                    break

                pool = [
                    m
                    for m in ranked_all
                    if m.recipe.id not in used_ids
                    and (
                        not prefer_cookable
                        or m.can_cook
                        or not fill_missing
                        or True
                    )
                ]
                if not prefer_cookable:
                    cookable = [m for m in pool if m.can_cook]
                    pool = cookable
                if fill_missing is False:
                    pool = [m for m in pool if m.can_cook]
                if not pool:
                    continue

                # Prefer recipes that use up what is already expiring, then the
                # ranked order. Ties always resolve on recipe id.
                def sort_key(match: MatchResult):
                    expiring = 0
                    for ing in match.have:
                        days_left = self.index.expiring_days(ing.name)
                        if days_left is not None and days_left <= 3:
                            expiring += 1
                    reuse = len({i.canonical for i in match.have} & covered)
                    return (
                        0 if match.can_cook else 1,
                        -expiring,
                        -reuse,
                        len(match.missing),
                        match.recipe.minutes,
                        match.recipe.id,
                    )

                best = sorted(pool, key=sort_key)[0]
                used_ids.append(best.recipe.id)
                covered |= {i.canonical for i in best.have}

                chosen.append(
                    {
                        "day": day,
                        "meal": slot,
                        "recipe_id": best.recipe.id,
                        "name": best.recipe.name,
                        "cuisine": best.recipe.cuisine,
                        "minutes": best.recipe.minutes,
                        "servings": best.recipe.servings,
                        "can_cook": best.can_cook,
                        "coverage": best.coverage,
                        "missing": [i.display() for i in best.missing],
                        "have": [i.display() for i in best.have],
                        "tags": list(best.recipe.tags),
                    }
                )
                entries_per_day[day] = entries_per_day.get(day, 0) + 1

        return chosen

    def plan_cookable_now(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Just the recipes that need no shopping at all, best first."""
        ranked = self.library.rank(
            self.index, prioritize_expiring=self.prioritize_expiring
        )
        out = [m.to_dict(with_recipe=False) for m in ranked if m.can_cook]
        return out[:limit]

    def leftovers_plan(self, days: int = 5) -> List[Dict[str, Any]]:
        """A zero-shop plan: nothing on the list that is not already in stock.

        If there are not enough cookable recipes the plan is simply short --
        it never invents a recipe or silently adds shopping.
        """
        return self.plan(days=days, fill_missing=False, prefer_cookable=True)


def summarize_plan(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a plan into headline numbers for reports."""
    if not entries:
        return {
            "entries": 0,
            "days": 0,
            "cookable": 0,
            "needs_shopping": 0,
            "total_minutes": 0,
            "distinct_recipes": 0,
            "coverage": 0.0,
        }
    cookable = sum(1 for e in entries if e.get("can_cook"))
    return {
        "entries": len(entries),
        "days": len({e["day"] for e in entries}),
        "cookable": cookable,
        "needs_shopping": len(entries) - cookable,
        "total_minutes": sum(int(e.get("minutes", 0)) for e in entries),
        "distinct_recipes": len({e["recipe_id"] for e in entries}),
        "coverage": round(
            sum(float(e.get("coverage", 0.0)) for e in entries) / len(entries), 4
        ),
    }

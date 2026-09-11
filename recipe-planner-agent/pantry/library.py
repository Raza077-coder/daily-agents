from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .models import (
    DEFAULT_STAPLES,
    Ingredient,
    MatchResult,
    PantryItem,
    Recipe,
)
from .units import canonical_name, humanize, names_match

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BUNDLED_RECIPES = os.path.join(DATA_DIR, "recipes.json")
SAMPLE_PANTRY = os.path.join(DATA_DIR, "pantry_sample.txt")

_EPSILON = 1e-9


class PantryIndex:
    """A fast, deterministic lookup over the cook's pantry."""

    def __init__(
        self,
        items: Iterable[PantryItem] = (),
        expiring: Optional[Dict[str, int]] = None,
    ) -> None:
        self.items: List[PantryItem] = list(items)
        self.expiring: Dict[str, int] = {
            canonical_name(k): int(v) for k, v in (expiring or {}).items()
        }
        self._by_canonical: Dict[str, List[PantryItem]] = {}
        self._reindex()

    def _reindex(self) -> None:
        self._by_canonical = {}
        for item in self.items:
            if item.canonical:
                self._by_canonical.setdefault(item.canonical, []).append(item)

    # ---------------------------------------------------------------- mutation
    def add(self, item: PantryItem) -> "PantryIndex":
        if item.canonical:
            self.items.append(item)
            self._by_canonical.setdefault(item.canonical, []).append(item)
            if item.expires_in_days is not None:
                self.expiring[item.canonical] = int(item.expires_in_days)
        return self

    def remove(self, name: str) -> bool:
        key = canonical_name(name)
        kept = [i for i in self.items if i.canonical != key]
        removed = len(kept) != len(self.items)
        if removed:
            self.items = kept
            self._reindex()
            self.expiring.pop(key, None)
        return removed

    def extend(self, texts: Iterable[str]) -> "PantryIndex":
        for text in texts:
            self.add(PantryItem.from_text(text))
        return self

    # ------------------------------------------------------------------- query
    def candidates(self, name: str) -> List[PantryItem]:
        """Pantry entries that refer to the same ingredient as ``name``."""
        direct = self._by_canonical.get(canonical_name(name))
        if direct:
            return list(direct)
        return [i for i in self.items if names_match(i.name, name)]

    def has(self, name: str) -> bool:
        return bool(self.candidates(name))

    def qty_for(self, name: str) -> Optional[str]:
        items = [i for i in self.candidates(name) if i.qty_known]
        if not items:
            return None
        total = sum(i.base_qty or 0.0 for i in items)
        return humanize(total, items[0].base_unit)

    def expiring_days(self, name: str) -> Optional[int]:
        key = canonical_name(name)
        if key in self.expiring:
            return self.expiring[key]
        for cand in self.candidates(name):
            if cand.expires_in_days is not None:
                return int(cand.expires_in_days)
        return None

    def expiring_items(self, within_days: int = 3) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in self.items:
            days = item.expires_in_days
            if days is None:
                days = self.expiring.get(item.canonical)
            if days is not None and int(days) <= within_days:
                rows.append(
                    {
                        "name": item.name,
                        "canonical": item.canonical,
                        "expires_in_days": int(days),
                        "display": item.display(),
                    }
                )
        rows.sort(key=lambda r: (r["expires_in_days"], r["canonical"]))
        return rows

    def to_list(self) -> List[Dict[str, Any]]:
        return [i.to_dict() for i in self.items]

    def __len__(self) -> int:
        return len(self.items)


def _satisfy(
    ingredient: Ingredient,
    index: PantryIndex,
    staples: Set[str],
) -> tuple:
    """Decide the status of one ingredient: have / partial / missing / sub."""
    need = ingredient.base_qty or 0.0

    if ingredient.canonical in staples:
        return "have", None

    candidates = index.candidates(ingredient.name)

    if candidates:
        # "I have some" beats any quantity question.
        if any(not c.qty_known for c in candidates):
            return "have", None

        matching = [c for c in candidates if c.dimension == ingredient.dimension]
        if matching:
            total = sum(c.base_qty or 0.0 for c in matching)
            if total + _EPSILON >= need:
                return "have", None
            if total > _EPSILON:
                return (
                    "partial",
                    {
                        "name": ingredient.name,
                        "need": humanize(need, ingredient.base_unit),
                        "have": humanize(total, matching[0].base_unit),
                        "reason": "not enough",
                    },
                )
            return "missing", None

        # Present, but measured in an incompatible dimension -- say so instead
        # of pretending either way.
        return (
            "partial",
            {
                "name": ingredient.name,
                "need": humanize(need, ingredient.base_unit),
                "have": candidates[0].display(),
                "reason": "unit mismatch",
            },
        )

    # Not in the pantry: does a declared substitute cover it?
    for sub in ingredient.substitutes:
        if canonical_name(sub) in staples or index.has(sub):
            return "sub", sub

    return "missing", None


def match_recipe(
    recipe: Recipe,
    index: PantryIndex,
    staples: Optional[Set[str]] = None,
) -> MatchResult:
    """Score how well ``recipe`` fits the pantry at ``index``."""
    staples = set(DEFAULT_STAPLES) if staples is None else set(staples)
    result = MatchResult(recipe=recipe)

    for ingredient in recipe.ingredients:
        status, detail = _satisfy(ingredient, index, staples)

        if ingredient.optional:
            if status in ("missing", "partial"):
                result.optional_missing.append(ingredient)
            else:
                result.have.append(ingredient)
                if status == "sub":
                    result.substitutes.append(
                        {"ingredient": ingredient.name, "using": str(detail)}
                    )
            continue

        if status == "have":
            result.have.append(ingredient)
        elif status == "sub":
            result.have.append(ingredient)
            result.substitutes.append(
                {"ingredient": ingredient.name, "using": str(detail)}
            )
        elif status == "partial":
            result.partial.append(detail or {})
        else:
            result.missing.append(ingredient)

    return result


class RecipeLibrary:
    """An in-memory, deterministic recipe collection."""

    def __init__(self, recipes: Optional[Iterable[Recipe]] = None) -> None:
        self._recipes: Dict[str, Recipe] = {}
        for recipe in recipes or ():
            self.add(recipe)

    # ------------------------------------------------------------- construction
    @classmethod
    def bundled(cls) -> "RecipeLibrary":
        """Load the recipe set shipped inside the package."""
        return cls.from_file(BUNDLED_RECIPES)

    @classmethod
    def from_file(cls, path: str) -> "RecipeLibrary":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_json(json.load(handle))

    @classmethod
    def from_json(cls, payload: Any) -> "RecipeLibrary":
        if isinstance(payload, dict):
            raw = payload.get("recipes", [])
        else:
            raw = payload
        return cls(Recipe.from_dict(item) for item in raw)

    # ----------------------------------------------------------------- mutation
    def add(self, recipe: Recipe) -> "RecipeLibrary":
        self._recipes[recipe.id] = recipe
        return self

    def all(self) -> List[Recipe]:
        return [self._recipes[key] for key in sorted(self._recipes)]

    def get(self, recipe_id: str) -> Optional[Recipe]:
        return self._recipes.get(recipe_id)

    def __len__(self) -> int:
        return len(self._recipes)

    # -------------------------------------------------------------------- query
    def filter(
        self,
        cuisine: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        exclude_tags: Optional[Sequence[str]] = None,
        max_minutes: Optional[int] = None,
        difficulty: Optional[str] = None,
        vegetarian: bool = False,
        text: Optional[str] = None,
    ) -> List[Recipe]:
        """Filter the library. Every criterion is optional and combined with AND."""
        wanted_tags = {t.lower() for t in (tags or [])}
        banned_tags = {t.lower() for t in (exclude_tags or [])}
        needle = (text or "").strip().lower()

        out: List[Recipe] = []
        for recipe in self.all():
            recipe_tags = {t.lower() for t in recipe.tags}

            if cuisine and cuisine.lower() not in recipe.cuisine.lower():
                continue
            if wanted_tags and not wanted_tags.issubset(recipe_tags):
                continue
            if banned_tags and (recipe_tags & banned_tags):
                continue
            if max_minutes is not None and recipe.minutes > int(max_minutes):
                continue
            if difficulty and difficulty.lower() != recipe.difficulty.lower():
                continue
            if vegetarian and not ({"vegetarian", "vegan"} & recipe_tags):
                continue
            if needle:
                haystack = " ".join(
                    [
                        recipe.name,
                        recipe.cuisine,
                        " ".join(recipe.tags),
                        " ".join(i.name for i in recipe.ingredients),
                    ]
                ).lower()
                if needle not in haystack:
                    continue
            out.append(recipe)
        return out

    def match_all(
        self,
        index: PantryIndex,
        recipes: Optional[Iterable[Recipe]] = None,
        staples: Optional[Set[str]] = None,
    ) -> List[MatchResult]:
        pool = list(recipes) if recipes is not None else self.all()
        return [match_recipe(r, index, staples) for r in pool]

    def rank(
        self,
        index: PantryIndex,
        recipes: Optional[Iterable[Recipe]] = None,
        prioritize_expiring: bool = True,
        staples: Optional[Set[str]] = None,
    ) -> List[MatchResult]:
        """Deterministically order matches: cookable first, then fewest gaps.

        Every tie-break ends at the recipe id so two runs over the same inputs
        always produce the same order.
        """
        results = self.match_all(index, recipes, staples)

        def sort_key(result: MatchResult):
            expiring_used = 0
            if prioritize_expiring:
                for ing in result.have:
                    days = index.expiring_days(ing.name)
                    if days is not None and days <= 3:
                        expiring_used += 1
            return (
                0 if result.can_cook else 1,
                len(result.missing),
                len(result.partial),
                -expiring_used,
                -result.coverage,
                result.recipe.minutes,
                result.recipe.id,
            )

        return sorted(results, key=sort_key)

    def stats(self) -> Dict[str, Any]:
        recipes = self.all()
        cuisines: Dict[str, int] = {}
        tags: Dict[str, int] = {}
        for recipe in recipes:
            cuisines[recipe.cuisine] = cuisines.get(recipe.cuisine, 0) + 1
            for tag in recipe.tags:
                tags[tag] = tags.get(tag, 0) + 1
        return {
            "recipes": len(recipes),
            "cuisines": dict(sorted(cuisines.items())),
            "tags": dict(sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))),
            "ingredients_indexed": len(
                {i.canonical for r in recipes for i in r.ingredients}
            ),
            "avg_minutes": round(
                sum(r.minutes for r in recipes) / len(recipes), 1
            )
            if recipes
            else 0,
        }


def load_sample_pantry_text() -> List[str]:
    """Read the bundled sample pantry (one entry per line, ``#`` comments)."""
    if not os.path.exists(SAMPLE_PANTRY):
        return []
    lines: List[str] = []
    with open(SAMPLE_PANTRY, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(stripped)
    return lines

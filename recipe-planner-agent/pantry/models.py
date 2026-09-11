"""Typed data models for the PANTRY engine.

Every model exposes ``to_dict()`` so the CLI, the REST API and the browser
port can all serialise the exact same shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .aisles import aisle_for
from .units import (
    COUNT,
    MASS,
    VOLUME,
    canonical_name,
    humanize,
    parse_quantity,
    split_amount,
    to_base,
    unit_dimension,
)

BASE_UNIT = {MASS: "g", VOLUME: "ml", COUNT: "piece"}

# Pantry staples: treated as always on hand and never written onto a shopping
# list -- nobody sends you to the shop for salt.
DEFAULT_STAPLES = {
    "salt",
    "black pepper",
    "water",
}

_NUMBER_PREFIX = re.compile(r"^\s*(?:\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.?\d+)")


def base_unit_for(dimension: Optional[str]) -> str:
    return BASE_UNIT.get(dimension or COUNT, "piece")


def _split_base(qty: float, unit: str):
    """Return ``(base_qty, dimension, base_unit)`` for a raw quantity/unit."""
    base, dim = to_base(qty, unit)
    if base is None:
        # Unknown unit: preserve the number rather than silently dropping it.
        return qty, COUNT, "piece"
    return base, dim, base_unit_for(dim)


@dataclass
class Ingredient:
    """One line of a recipe."""

    name: str
    qty: float
    unit: str
    optional: bool = False
    substitutes: List[str] = field(default_factory=list)
    note: str = ""

    # derived in __post_init__
    canonical: str = ""
    dimension: Optional[str] = None
    base_qty: Optional[float] = None
    base_unit: str = "piece"

    def __post_init__(self) -> None:
        self.canonical = canonical_name(self.name)
        qty = float(self.qty) if self.qty is not None else 1.0
        self.base_qty, self.dimension, self.base_unit = _split_base(qty, self.unit)

    @property
    def scalable(self) -> bool:
        return self.base_qty is not None

    def scaled(self, factor: float) -> "Ingredient":
        """Return a copy with the amount multiplied by ``factor``."""
        if not self.scalable:
            return self
        return Ingredient(
            name=self.name,
            qty=(self.base_qty or 0.0) * factor,
            unit=self.base_unit,
            optional=self.optional,
            substitutes=list(self.substitutes),
            note=self.note,
        )

    @property
    def aisle(self) -> str:
        return aisle_for(self.name)

    def display(self) -> str:
        amount = humanize(self.base_qty or 0.0, self.base_unit)
        suffix = " (optional)" if self.optional else ""
        return f"{amount} {self.name}{suffix}".strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "canonical": self.canonical,
            "quantity": round(self.base_qty or 0.0, 4),
            "unit": self.base_unit,
            "display": self.display(),
            "optional": self.optional,
            "substitutes": list(self.substitutes),
            "note": self.note,
            "aisle": self.aisle,
        }

    @classmethod
    def from_raw(
        cls,
        name: str,
        qty: Optional[float] = None,
        unit: Optional[str] = None,
        optional: bool = False,
        substitutes: Optional[List[str]] = None,
        note: str = "",
    ) -> "Ingredient":
        if qty is None and unit is None:
            # Free text: split the amount off and keep the ingredient name.
            qty, unit, bare_name, _ = split_amount(name)
            name = bare_name or name
        return cls(
            name=name,
            qty=1.0 if qty is None else qty,
            unit=(unit or "piece"),
            optional=optional,
            substitutes=list(substitutes or []),
            note=note,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Ingredient":
        if "quantity" in payload and "unit" in payload:
            return cls(
                name=payload.get("name", ""),
                qty=float(payload.get("quantity", 1.0)),
                unit=payload.get("unit", "piece"),
                optional=bool(payload.get("optional", False)),
                substitutes=list(payload.get("substitutes") or []),
                note=payload.get("note", ""),
            )
        if isinstance(payload, str):
            return cls.from_raw(payload)
        return cls.from_raw(
            payload.get("name", ""),
            payload.get("qty"),
            payload.get("unit"),
            bool(payload.get("optional", False)),
            payload.get("substitutes"),
            payload.get("note", ""),
        )


@dataclass
class Recipe:
    """A single recipe from the library."""

    id: str
    name: str
    cuisine: str = "international"
    servings: int = 2
    minutes: int = 30
    difficulty: str = "easy"
    tags: List[str] = field(default_factory=list)
    ingredients: List[Ingredient] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    notes: str = ""

    def scale_to(self, servings: int) -> "Recipe":
        """Return a copy scaled so ``servings`` portions come out."""
        target = max(1, int(servings))
        factor = target / float(max(1, self.servings))
        return Recipe(
            id=self.id,
            name=self.name,
            cuisine=self.cuisine,
            servings=target,
            minutes=self.minutes,
            difficulty=self.difficulty,
            tags=list(self.tags),
            ingredients=[ing.scaled(factor) for ing in self.ingredients],
            steps=list(self.steps),
            notes=self.notes,
        )

    def required(self) -> List[Ingredient]:
        return [i for i in self.ingredients if not i.optional]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cuisine": self.cuisine,
            "servings": self.servings,
            "minutes": self.minutes,
            "difficulty": self.difficulty,
            "tags": list(self.tags),
            "ingredients": [i.to_dict() for i in self.ingredients],
            "steps": list(self.steps),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Recipe":
        raw_ingredients = payload.get("ingredients") or []
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            cuisine=payload.get("cuisine", "international"),
            servings=int(payload.get("servings", 2)),
            minutes=int(payload.get("minutes", 30)),
            difficulty=payload.get("difficulty", "easy"),
            tags=[str(t) for t in (payload.get("tags") or [])],
            ingredients=[
                Ingredient.from_dict(i) if isinstance(i, dict) else Ingredient.from_raw(str(i))
                for i in raw_ingredients
            ],
            steps=[str(s) for s in (payload.get("steps") or [])],
            notes=payload.get("notes", ""),
        )


@dataclass
class PantryItem:
    """Something the cook already has at home."""

    name: str
    qty: float
    unit: str
    # False when the user just wrote the name, e.g. "olive oil" -- meaning
    # "I have some", with no meaningful amount.
    qty_known: bool = True  # noqa: A003
    expires_in_days: Optional[int] = None

    # derived in __post_init__
    canonical: str = ""
    dimension: Optional[str] = None
    base_qty: Optional[float] = None
    base_unit: str = "piece"

    def __post_init__(self) -> None:
        self.canonical = canonical_name(self.name)
        qty = float(self.qty) if self.qty is not None else 1.0
        self.base_qty, self.dimension, self.base_unit = _split_base(qty, self.unit)

    def display(self) -> str:
        if not self.qty_known:
            return self.name
        return f"{humanize(self.base_qty or 0.0, self.base_unit)} {self.name}".strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "canonical": self.canonical,
            "quantity": round(self.base_qty or 0.0, 4),
            "unit": self.base_unit,
            "display": self.display(),
            "qty_known": self.qty_known,
            "expires_in_days": self.expires_in_days,
            "aisle": aisle_for(self.name),
        }

    @classmethod
    def from_text(cls, text: str) -> "PantryItem":
        """Parse ``"200 g spaghetti"`` / ``"6 eggs"`` / ``"olive oil"``.

        A leading number makes the amount authoritative; with no number the
        item is recorded as "present, amount unspecified".
        """
        raw = (text or "").strip()
        if not raw:
            return cls(name="", qty=1.0, unit="piece", qty_known=False)

        qty, unit, name, has_number = split_amount(raw)
        name = re.sub(r"\s+", " ", name).strip(" ,-") or raw

        return cls(
            name=name,
            qty=1.0 if qty is None else qty,
            unit=(unit or "piece"),
            qty_known=has_number,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PantryItem":
        if isinstance(payload, str):
            return cls.from_text(payload)
        if "quantity" in payload or "qty" in payload:
            return cls(
                name=payload.get("name", ""),
                qty=float(payload.get("quantity", payload.get("qty", 1.0))),
                unit=payload.get("unit", "piece"),
                qty_known=bool(payload.get("qty_known", True)),
                expires_in_days=payload.get("expires_in_days"),
            )
        return cls.from_text(payload.get("name", ""))


@dataclass
class MatchResult:
    """How well a recipe fits the current pantry."""

    recipe: Recipe
    have: List[Ingredient] = field(default_factory=list)
    missing: List[Ingredient] = field(default_factory=list)
    optional_missing: List[Ingredient] = field(default_factory=list)
    partial: List[Dict[str, Any]] = field(default_factory=list)
    substitutes: List[Dict[str, str]] = field(default_factory=list)

    @property
    def required_count(self) -> int:
        return len(self.have) + len(self.missing) + len(self.partial)

    @property
    def coverage(self) -> float:
        """0..1 fraction of required ingredients fully on hand."""
        total = self.required_count
        if total == 0:
            return 1.0
        return round(len(self.have) / total, 4)

    @property
    def can_cook(self) -> bool:
        return not self.missing and not self.partial

    @property
    def score(self) -> float:
        """Single ranking number: coverage first, then penalise gaps."""
        return round(
            (self.coverage * 100.0)
            - (len(self.missing) * 6.0)
            - (len(self.partial) * 3.0)
            - (len(self.optional_missing) * 0.5),
            2,
        )

    def to_dict(self, with_recipe: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "recipe_id": self.recipe.id,
            "name": self.recipe.name,
            "can_cook": self.can_cook,
            "coverage": self.coverage,
            "score": self.score,
            "missing_count": len(self.missing),
            "have": [i.display() for i in self.have],
            "missing": [i.display() for i in self.missing],
            "optional_missing": [i.display() for i in self.optional_missing],
            "partial": list(self.partial),
            "substitutes": list(self.substitutes),
        }
        if with_recipe:
            payload["recipe"] = self.recipe.to_dict()
        return payload


@dataclass
class ShoppingItem:
    """One aggregated line of a shopping list."""

    canonical: str
    name: str
    qty: float
    unit: str
    sources: List[str] = field(default_factory=list)

    def display(self) -> str:
        return f"{humanize(self.qty, self.unit)} {self.name}".strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical": self.canonical,
            "name": self.name,
            "quantity": round(self.qty, 4),
            "unit": self.unit,
            "display": self.display(),
            "aisle": aisle_for(self.name),
            "sources": list(self.sources),
        }

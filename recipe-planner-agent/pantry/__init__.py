"""PANTRY -- a deterministic, offline recipe & meal-planning engine.

No network calls, no ML, no API keys. Give it a pantry and it tells you what
you can actually cook, what is missing, and what to buy.

Public surface::

    from pantry import PantryEngine

    engine = PantryEngine()
    engine.load_pantry(["eggs x6", "200 g spaghetti", "4 clove garlic"])
    engine.cook_now()          # what can I make right now?
    engine.plan_week(days=5)   # a deterministic meal plan
    engine.shopping_list(days=5)
"""

from .engine import PantryEngine
from .models import Ingredient, PantryItem, Recipe
from .units import canonical_name, convert, parse_quantity, to_base

__all__ = [
    "PantryEngine",
    "Recipe",
    "Ingredient",
    "PantryItem",
    "canonical_name",
    "to_base",
    "convert",
    "parse_quantity",
]

__version__ = "1.0.0"

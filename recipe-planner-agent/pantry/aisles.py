"""Deterministic aisle classification, used to group shopping lists.

Kept in its own module so both the models and the engine can use it without a
circular import. Classification is keyword based and longest-match-first, which
keeps "sweet potato" out of the potato bin and "coconut milk" out of dairy.
"""

from __future__ import annotations

import re
from typing import Dict, List

from .units import canonical_name

AISLE_ORDER: List[str] = [
    "fruit & veg",
    "meat & fish",
    "dairy & eggs",
    "bakery",
    "pantry",
    "frozen",
    "spices & condiments",
    "other",
]

# aisle -> keywords. Order inside the dict does not matter: we always try the
# longest keyword across all aisles first.
_AISLE_KEYWORDS: Dict[str, List[str]] = {
    "fruit & veg": [
        "sweet potato", "spring onion", "bell pepper", "cherry tomato",
        "green bean", "red onion", "red cabbage", "bok choy", "butternut squash",
        "runner bean", "snap pea", "chili", "eggplant", "zucchini", "cucumber",
        "avocado", "broccoli", "cauliflower", "carrot", "celery", "spinach",
        "kale", "lettuce", "rocket", "arugula", "tomato", "potato", "onion",
        "garlic", "ginger", "lemon", "lime", "orange", "apple", "banana",
        "berry", "mango", "pineapple", "parsley", "cilantro", "basil", "mint",
        "thyme", "rosemary", "mushroom", "pepper", "squash", "cabbage", "leek",
        "corn", "pea", "asparagus", "sprout", "radish", "beetroot", "turnip",
        "fennel", "courgette", "shallot",
    ],
    "meat & fish": [
        "chicken breast", "chicken thigh", "ground beef", "beef", "pork",
        "lamb", "bacon", "sausage", "ham", "turkey", "salmon", "tuna", "cod",
        "prawn", "shrimp", "anchovy", "sardine", "mackerel", "tofu", "tempeh",
    ],
    "dairy & eggs": [
        "heavy cream", "light cream", "sour cream", "cream cheese", "greek yogurt",
        "milk", "yogurt", "butter", "cheese", "parmesan", "mozzarella", "feta",
        "cheddar", "egg", "creme fraiche", "mascarpone", "ricotta",
    ],
    "bakery": [
        "bread", "baguette", "tortilla", "pita", "bun", "roll", "bagel",
        "croissant", "spring roll wrapper", "filo", "puff pastry",
    ],
    "frozen": ["frozen pea", "frozen spinach", "ice cream", "frozen"],
    "spices & condiments": [
        "black pepper", "olive oil", "soy sauce", "sesame oil", "fish sauce",
        "vinegar", "mustard", "mayonnaise", "ketchup", "honey", "maple syrup",
        "cumin", "coriander", "paprika", "turmeric", "cinnamon", "oregano",
        "chili flake", "curry powder", "bay leaf", "nutmeg", "cardamom",
        "stock", "bouillon", "salt", "sugar", "vanilla", "yeast",
        "baking powder", "baking soda", "cornstarch", "tomato paste",
    ],
    "pantry": [
        "spaghetti", "penne", "noodle", "rice", "couscous", "quinoa", "lentil",
        "chickpea", "kidney bean", "black bean", "cannellini bean",
        "butter bean", "canned tomato", "coconut milk", "peanut butter",
        "flour", "oat", "breadcrumb", "walnut", "almond", "cashew", "raisin",
    ],
}

_RULES: List[tuple] = sorted(
    ((kw, aisle) for aisle, kws in _AISLE_KEYWORDS.items() for kw in kws),
    key=lambda pair: (-len(pair[0]), pair[0]),
)


def aisle_for(name: str) -> str:
    """Return the shopping aisle for an ingredient name.

    Falls back to ``"other"`` -- never raises, never guesses a fake category.
    """
    key = canonical_name(name)
    if not key:
        return "other"
    for keyword, aisle in _RULES:
        if re.search(r"\b" + re.escape(keyword) + r"\b", key):
            return aisle
    return "other"


def group_by_aisle(items: List[dict], name_key: str = "name") -> List[dict]:
    """Group serialised shopping items into ordered aisle buckets."""
    buckets: Dict[str, List[dict]] = {}
    for item in items:
        aisle = aisle_for(item.get(name_key, ""))
        buckets.setdefault(aisle, []).append(item)

    ordered: List[dict] = []
    for aisle in AISLE_ORDER:
        rows = buckets.pop(aisle, None)
        if rows:
            ordered.append({"aisle": aisle, "items": rows})
    # Any aisle that somehow is not in AISLE_ORDER still gets emitted.
    for aisle in sorted(buckets):
        ordered.append({"aisle": aisle, "items": buckets[aisle]})
    return ordered

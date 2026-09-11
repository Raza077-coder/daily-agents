"""Deterministic unit handling and ingredient-name canonicalisation.

Design rules
------------
* Every quantity is converted into exactly one of three base dimensions:
  ``mass`` -> grams, ``volume`` -> millilitres, ``count`` -> whole units.
* ``mass`` and ``volume`` never mix with each other or with ``count`` -- a
  recipe asking for 200 g of flour is never satisfied by 200 ml of milk.
  Comparisons across dimensions return "incompatible" rather than guessing.
* Unknown units are preserved verbatim and only compare equal to themselves.

Everything here is pure and side-effect free, so the same input always yields
the same output (a hard requirement for this agent's test suite).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

MASS = "mass"
VOLUME = "volume"
COUNT = "count"

DIMENSION_LABEL = {
    MASS: "g",
    VOLUME: "ml",
    COUNT: "unit",
}

# unit -> (dimension, factor to that dimension's base unit)
_UNITS: dict[str, Tuple[str, float]] = {
    # ---- mass (base: gram) ----
    "g": (MASS, 1.0),
    "gram": (MASS, 1.0),
    "grams": (MASS, 1.0),
    "gm": (MASS, 1.0),
    "kg": (MASS, 1000.0),
    "kilo": (MASS, 1000.0),
    "kilogram": (MASS, 1000.0),
    "kilograms": (MASS, 1000.0),
    "mg": (MASS, 0.001),
    "oz": (MASS, 28.349523125),
    "ounce": (MASS, 28.349523125),
    "ounces": (MASS, 28.349523125),
    "lb": (MASS, 453.59237),
    "lbs": (MASS, 453.59237),
    "pound": (MASS, 453.59237),
    "pounds": (MASS, 453.59237),
    # ---- volume (base: millilitre) ----
    "ml": (VOLUME, 1.0),
    "millilitre": (VOLUME, 1.0),
    "millilitres": (VOLUME, 1.0),
    "milliliter": (VOLUME, 1.0),
    "milliliters": (VOLUME, 1.0),
    "l": (VOLUME, 1000.0),
    "litre": (VOLUME, 1000.0),
    "litres": (VOLUME, 1000.0),
    "liter": (VOLUME, 1000.0),
    "liters": (VOLUME, 1000.0),
    "tsp": (VOLUME, 4.92892159375),
    "teaspoon": (VOLUME, 4.92892159375),
    "teaspoons": (VOLUME, 4.92892159375),
    "tbsp": (VOLUME, 14.78676478125),
    "tablespoon": (VOLUME, 14.78676478125),
    "tablespoons": (VOLUME, 14.78676478125),
    "cup": (VOLUME, 236.5882365),
    "cups": (VOLUME, 236.5882365),
    "floz": (VOLUME, 29.5735295625),
    "fl oz": (VOLUME, 29.5735295625),
    "fl_oz": (VOLUME, 29.5735295625),
    "pint": (VOLUME, 473.176473),
    "pints": (VOLUME, 473.176473),
    "quart": (VOLUME, 946.352946),
    "quarts": (VOLUME, 946.352946),
    # ---- count (base: one piece) ----
    "": (COUNT, 1.0),
    "piece": (COUNT, 1.0),
    "pieces": (COUNT, 1.0),
    "pc": (COUNT, 1.0),
    "pcs": (COUNT, 1.0),
    "clove": (COUNT, 1.0),
    "cloves": (COUNT, 1.0),
    "slice": (COUNT, 1.0),
    "slices": (COUNT, 1.0),
    "can": (COUNT, 1.0),
    "cans": (COUNT, 1.0),
    "tin": (COUNT, 1.0),
    "tins": (COUNT, 1.0),
    "bunch": (COUNT, 1.0),
    "bunches": (COUNT, 1.0),
    "head": (COUNT, 1.0),
    "heads": (COUNT, 1.0),
    "sprig": (COUNT, 1.0),
    "sprigs": (COUNT, 1.0),
    "stalk": (COUNT, 1.0),
    "stalks": (COUNT, 1.0),
    "stick": (COUNT, 1.0),
    "sticks": (COUNT, 1.0),
    "sheet": (COUNT, 1.0),
    "sheets": (COUNT, 1.0),
    "fillet": (COUNT, 1.0),
    "fillets": (COUNT, 1.0),
    "handful": (COUNT, 1.0),
    "handfuls": (COUNT, 1.0),
    "packet": (COUNT, 1.0),
    "packets": (COUNT, 1.0),
    "pinch": (COUNT, 1.0),
    "pinches": (COUNT, 1.0),
}

_UNIT_ALIASES = {
    "tbs": "tbsp",
    "tbsps": "tbsp",
    "tsps": "tsp",
    "gr": "g",
    "kgs": "kg",
    "mls": "ml",
    "clv": "clove",
    "clvs": "cloves",
    "ea": "piece",
    "each": "piece",
    "x": "piece",
}

# Irregular plurals that the generic rules below would mangle.
_IRREGULAR_PLURALS = {
    "chillies": "chili",
    "cookies": "cookie",
    "brownies": "brownie",
    "smoothies": "smoothie",
    "pies": "pie",
    "leaves": "leaf",
    "loaves": "loaf",
    "potatoes": "potato",
    "tomatoes": "tomato",
    "avocados": "avocado",
    "mangoes": "mango",
    "berries": "berry",
    "anchovies": "anchovy",
    "springs": "spring",
}

# Ingredient synonyms. Kept deliberately small and *safe* -- only true
# equivalences, never "close enough" flavour swaps (that is what a recipe's
# own ``substitutes`` list is for).
_INGREDIENT_ALIASES = {
    "scallion": "spring onion",
    "scallions": "spring onion",
    "green onion": "spring onion",
    "green onions": "spring onion",
    "spring onions": "spring onion",
    "garbanzo": "chickpea",
    "garbanzo bean": "chickpea",
    "garbanzo beans": "chickpea",
    "chick peas": "chickpea",
    "chickpea": "chickpea",
    "aubergine": "eggplant",
    "aubergines": "eggplant",
    "courgette": "zucchini",
    "courgettes": "zucchini",
    "coriander leaves": "cilantro",
    "fresh coriander": "cilantro",
    "capsicum": "bell pepper",
    "bell peppers": "bell pepper",
    "caster sugar": "sugar",
    "granulated sugar": "sugar",
    "white sugar": "sugar",
    "all purpose flour": "flour",
    "all-purpose flour": "flour",
    "plain flour": "flour",
    "ap flour": "flour",
    "tomatoe": "tomato",
    "roma tomato": "tomato",
    "cherry tomatoes": "cherry tomato",
    "chicken breasts": "chicken breast",
    "chicken thighs": "chicken thigh",
    "minced beef": "ground beef",
    "beef mince": "ground beef",
    "mince": "ground beef",
    "double cream": "heavy cream",
    "single cream": "light cream",
    "natural yogurt": "yogurt",
    "plain yogurt": "yogurt",
    "greek yoghurt": "greek yogurt",
    "yoghurt": "yogurt",
    "light soy sauce": "soy sauce",
    "spring water": "water",
    "extra virgin olive oil": "olive oil",
    "olive oil extra virgin": "olive oil",
    "spaghetti pasta": "spaghetti",
    "penne pasta": "penne",
    "sea salt": "salt",
    "kosher salt": "salt",
    "black peppercorns": "black pepper",
    "ground black pepper": "black pepper",
    "pepper": "black pepper",
    "clove garlic": "garlic",
    "cloves garlic": "garlic",
    "garlic clove": "garlic",
    "garlic cloves": "garlic",
    "fresh ginger": "ginger",
    "ginger root": "ginger",
    "chilli": "chili",
    "chillies": "chili",
    "chile": "chili",
    "red chilli": "chili",
    "flat leaf parsley": "parsley",
    "italian parsley": "parsley",
    "canned tomatoes": "canned tomato",
    "tinned tomatoes": "canned tomato",
    "chopped tomatoes": "canned tomato",
    "passata": "canned tomato",
    "tomato puree": "tomato paste",
    "tomato concentrate": "tomato paste",
    "kidney beans": "kidney bean",
    "black beans": "black bean",
    "cannellini beans": "cannellini bean",
    "butter beans": "butter bean",
    "red lentils": "red lentil",
    "green lentils": "green lentil",
    "lentils": "lentil",
}

# Tokens that must NOT be singularised even though they end in "s".
_NO_SINGULAR = {
    "hummus",
    "couscous",
    "asparagus",
    "molasses",
    "cress",
    "oats",
    "greens",
    "swiss",
    "watercress",
    "chips",
    "peas",
}

_QUANTITY_RE = re.compile(
    r"^\s*(?P<qty>\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.?\d+)\s*(?P<rest>.*)$"
)


def normalize_unit(unit: Optional[str]) -> str:
    """Lowercase, strip dots and alias-map a unit string."""
    if unit is None:
        return ""
    u = str(unit).strip().lower().replace(".", "")
    u = re.sub(r"\s+", " ", u)
    return _UNIT_ALIASES.get(u, u)


def unit_dimension(unit: Optional[str]) -> Optional[str]:
    """Return ``mass`` / ``volume`` / ``count``, or ``None`` if unknown."""
    entry = _UNITS.get(normalize_unit(unit))
    return entry[0] if entry else None


def factor_for(unit: Optional[str]) -> Optional[float]:
    """Multiplier that converts ``unit`` into its dimension's base unit."""
    entry = _UNITS.get(normalize_unit(unit))
    return entry[1] if entry else None


def to_base(qty: float, unit: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """Convert ``qty unit`` into ``(base_qty, dimension)``.

    Returns ``(None, None)`` when the unit is unknown -- callers must treat
    that as "not comparable" instead of falling back to a silent guess.
    """
    factor = factor_for(unit)
    if factor is None:
        return None, None
    try:
        value = float(qty) * factor
    except (TypeError, ValueError):
        return None, None
    return value, unit_dimension(unit)


def convert(qty: float, from_unit: Optional[str], to_unit: Optional[str]) -> Optional[float]:
    """Convert between two units of the same dimension.

    Returns ``None`` when the units are unknown or belong to different
    dimensions -- never a fabricated number.
    """
    base, dim_a = to_base(qty, from_unit)
    dim_b = unit_dimension(to_unit)
    if base is None or dim_b is None or dim_a != dim_b:
        return None
    target_factor = factor_for(to_unit)
    if not target_factor:
        return None
    return base / target_factor


def comparable(unit_a: Optional[str], unit_b: Optional[str]) -> bool:
    """True when two units live in the same convertible dimension."""
    dim_a = unit_dimension(unit_a)
    dim_b = unit_dimension(unit_b)
    if dim_a is None or dim_b is None:
        return normalize_unit(unit_a) == normalize_unit(unit_b)
    return dim_a == dim_b


def as_base(qty: float, unit: Optional[str]) -> float:
    """Base-unit amount, or infinity when incomparable.

    Only for *ordering*; use :func:`to_base` when the distinction between
    "zero" and "unknown" matters.
    """
    base, _ = to_base(qty, unit)
    return base if base is not None else float("inf")


def _singularize_token(token: str) -> str:
    if token in _NO_SINGULAR or len(token) <= 3:
        return token
    if token in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[token]
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("oes", "ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _alias_lookup(key: str) -> Optional[str]:
    return _INGREDIENT_ALIASES.get(key)


def canonical_name(name: str) -> str:
    """Normalise an ingredient name to a stable comparison key.

    Steps: lowercase -> strip parentheticals -> drop a leading quantity ->
    collapse whitespace -> singularise each token -> apply the alias map.

    The alias map is consulted both *before* and *after* singularisation, so
    authors can write natural keys ("cherry tomatoes") without worrying about
    the plural rules. The result is a *key*; always keep the original name for
    display.
    """
    if not name:
        return ""
    text = str(name).strip().lower()
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9\s/-]", " ", text)
    # Drop a leading quantity that leaked in from free-text input.
    text = re.sub(r"^\s*\d+\s*(?:/\s*\d+)?\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return ""

    # Alias check on the raw (un-singularised) key first.
    direct = _alias_lookup(text)
    if direct:
        return direct

    tokens = [_singularize_token(tok) for tok in text.split(" ") if tok]
    key = " ".join(tokens)
    return _alias_lookup(key) or key


def names_match(a: str, b: str) -> bool:
    """True when two ingredient names refer to the same thing.

    Matches on canonical equality, or on equal *token sets* so that
    "chicken breast" and "breast of chicken" agree. Deliberately strict: it
    will not treat "chicken" as satisfying "chicken breast".
    """
    ca, cb = canonical_name(a), canonical_name(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    return set(ca.split()) == set(cb.split())


def _parse_fraction(token: str) -> Optional[float]:
    token = token.strip()
    if not token:
        return None
    mixed = re.match(r"^(\d+)\s+(\d+)\s*/\s*(\d+)$", token)
    if mixed:
        whole, num, den = (float(x) for x in mixed.groups())
        return whole + (num / den if den else 0.0)
    frac = re.match(r"^(\d+)\s*/\s*(\d+)$", token)
    if frac:
        num, den = (float(x) for x in frac.groups())
        return num / den if den else None
    try:
        return float(token)
    except ValueError:
        return None


def split_amount(text: str) -> Tuple[Optional[float], str, str, bool]:
    """Split free text into ``(qty, unit, name, has_number)``.

    This is the single place that understands lines like ``"200 g spaghetti"``,
    ``"6 eggs"``, ``"1 1/2 cups flour"`` and ``"olive oil"``. Both the pantry
    and the quantity parser go through it, so their interpretations can never
    drift apart.

    The unit is only consumed when it is a *known* unit, which is what stops
    ``"200 g spaghetti"`` from being read as 200 of unit ``"g spaghetti"``.
    """
    if text is None:
        return None, "", "", False
    raw = str(text).strip()
    if not raw:
        return None, "", "", False

    match = _QUANTITY_RE.match(raw)
    if not match:
        # No leading number at all: the whole thing is a name.
        return 1.0, "", raw, False

    qty = _parse_fraction(match.group("qty"))
    rest = re.sub(r"^[xX*]\s*", "", match.group("rest").strip())

    unit = ""
    consumed = 0
    words = rest.split()
    for count in (2, 1):
        if len(words) >= count:
            candidate = normalize_unit(" ".join(words[:count]))
            if candidate and candidate in _UNITS:
                unit = candidate
                consumed = count
                break

    name = " ".join(words[consumed:]).strip(" ,-")
    if not name:
        # The line was only an amount, e.g. "200 g" -- keep it visible.
        name = " ".join(words).strip(" ,-") or raw

    return (1.0 if qty is None else qty), unit, name, True


def parse_quantity(text: str) -> Tuple[Optional[float], str]:
    """Parse free text like ``"2 cups"``, ``"1/2 tsp"``, ``"200g"``.

    Returns ``(qty, unit)`` with the unit set only when it is recognised.
    ``"olive oil"`` returns ``(1.0, "")`` -- a name, not a measured amount.
    """
    qty, unit, _name, _has_number = split_amount(text)
    return qty, unit


def humanize(qty: float, unit: str) -> str:
    """Render a base quantity back into friendly units (e.g. 1500 g -> 1.5 kg)."""
    unit = normalize_unit(unit)
    dim = unit_dimension(unit)
    if dim == MASS and unit == "g" and qty >= 1000:
        return f"{_trim(qty / 1000)} kg"
    if dim == VOLUME and unit == "ml" and qty >= 1000:
        return f"{_trim(qty / 1000)} L"
    return f"{_trim(qty)} {unit}".strip()


def _trim(value: float) -> str:
    if value is None:
        return "0"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")

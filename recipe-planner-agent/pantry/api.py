"""PANTRY REST API (FastAPI).

Thin, stateless wrapper over :class:`pantry.engine.PantryEngine`. Every endpoint
takes the pantry inline in the request body, so there is no server-side session
and no data retention between calls.

Run locally::

    uvicorn pantry.api:app --reload --port 8000

Deploy to Vercel via ``api/index.py`` + ``vercel.json`` (both included).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

try:  # FastAPI is only needed for the REST surface.
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "The REST API needs FastAPI + Pydantic. Install with:\n"
        "    pip install -r requirements.txt\n"
        f"(import error: {exc})"
    ) from exc

from .engine import PantryEngine, load_profile, sample_pantry
from .library import RecipeLibrary
from .planner import summarize_plan
from .shopping import build_shopping_list, render_plan_text, render_shopping_list_text
from .units import canonical_name

VERSION = "1.0.0"

app = FastAPI(
    title="PANTRY \u2014 Recipe & Meal Planner Agent",
    description=(
        "Deterministic, offline recipe matching, meal planning and shopping-list "
        "building. No API keys, no network calls, no data retained between requests."
    ),
    version=VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------- schemas
class PantryBody(BaseModel):
    pantry: List[str] = Field(
        default_factory=list,
        description="Free-text pantry lines, e.g. ['200 g spaghetti', '6 eggs', 'olive oil'].",
    )
    profile: bool = Field(
        default=False, description="Merge in the bundled household profile defaults."
    )


class CookBody(PantryBody):
    limit: int = 10
    max_minutes: Optional[int] = None
    vegetarian: bool = False
    cuisine: Optional[str] = None
    only_cookable: bool = False


class PlanBody(PantryBody):
    days: int = 5
    slots: List[str] = Field(default_factory=lambda: ["dinner"])
    max_minutes: Optional[int] = None
    vegetarian: bool = False
    cuisine: Optional[str] = None
    leftovers_only: bool = False


class ShopBody(PlanBody):
    include_optional: bool = False


class ScaleBody(BaseModel):
    servings: int = Field(..., ge=1, le=50)


# ------------------------------------------------------------------- helpers
def _engine(pantry: Sequence[str], use_profile: bool = True) -> PantryEngine:
    engine = PantryEngine(profile=load_profile() if use_profile else {})
    if pantry:
        engine.load_pantry(list(pantry))
    return engine


# -------------------------------------------------------------------- routes
@app.get("/", tags=["meta"])
def root() -> Dict[str, Any]:
    return {
        "name": "PANTRY \u2014 Recipe & Meal Planner Agent",
        "version": VERSION,
        "offline": True,
        "docs": "/docs",
        "endpoints": [
            "GET  /health",
            "GET  /routes",
            "GET  /library",
            "GET  /profile",
            "GET  /demo",
            "POST /cook",
            "POST /plan",
            "POST /shop",
            "POST /search",
            "GET  /recipe/{recipe_id}",
            "POST /recipe/{recipe_id}/scale",
        ],
    }


@app.get("/health", tags=["meta"])
def health() -> Dict[str, Any]:
    library = RecipeLibrary.bundled()
    return {
        "status": "ok",
        "version": VERSION,
        "recipes": len(library),
        "network_required": False,
    }


@app.get("/routes", tags=["meta"])
def routes() -> Dict[str, Any]:
    """Machine-readable index of the API surface."""
    return {
        "count": 11,
        "routes": [
            {"method": "GET", "path": "/health", "desc": "Liveness + recipe count"},
            {"method": "GET", "path": "/routes", "desc": "This index"},
            {"method": "GET", "path": "/library", "desc": "Recipe library stats and list"},
            {"method": "GET", "path": "/profile", "desc": "Household profile"},
            {"method": "GET", "path": "/demo", "desc": "Sample pantry tour payload"},
            {"method": "POST", "path": "/cook", "desc": "Rank recipes against a pantry"},
            {"method": "POST", "path": "/plan", "desc": "Build a multi-day meal plan"},
            {"method": "POST", "path": "/shop", "desc": "Shopping list for a plan"},
            {"method": "POST", "path": "/search", "desc": "Keyword search the library"},
            {"method": "GET", "path": "/recipe/{id}", "desc": "One recipe in full"},
            {"method": "POST", "path": "/recipe/{id}/scale", "desc": "Rescale a recipe"},
        ],
    }


@app.get("/profile", tags=["meta"])
def profile() -> Dict[str, Any]:
    return _engine([]).profile_summary()


@app.get("/library", tags=["recipes"])
def library(cuisine: Optional[str] = None, vegetarian: bool = False) -> Dict[str, Any]:
    lib = RecipeLibrary.bundled()
    recipes = lib.filter(cuisine=cuisine, vegetarian=vegetarian)
    return {
        "stats": lib.stats(),
        "count": len(recipes),
        "recipes": [
            {
                "id": r.id,
                "name": r.name,
                "cuisine": r.cuisine,
                "minutes": r.minutes,
                "servings": r.servings,
                "difficulty": r.difficulty,
                "tags": r.tags,
            }
            for r in recipes
        ],
    }


@app.get("/demo", tags=["meta"])
def demo() -> Dict[str, Any]:
    """A ready-made tour: sample pantry -> cook list -> plan -> shopping list."""
    engine = _engine(sample_pantry())
    plan = engine.plan_week(days=5, slots=("dinner",))
    shopping = engine.shopping_list(days=5, slots=("dinner",))
    return {
        "pantry": engine.pantry_list(),
        "cook_now": [m.to_dict(with_recipe=False) for m in engine.cook_now(limit=6)],
        "plan": plan,
        "plan_text": render_plan_text(plan["entries"]),
        "shopping": shopping,
        "shopping_text": render_shopping_list_text(shopping),
    }


@app.post("/cook", tags=["planning"])
def cook(body: CookBody) -> Dict[str, Any]:
    engine = _engine(body.pantry, body.profile)
    results = engine.cook_now(
        limit=body.limit,
        max_minutes=body.max_minutes,
        vegetarian=body.vegetarian,
        cuisine=body.cuisine,
        include_partial=not body.only_cookable,
    )
    return {
        "count": len(results),
        "cookable_now": sum(1 for m in results if m.can_cook),
        "pantry_size": len(engine.index),
        "results": [m.to_dict() for m in results],
    }


@app.post("/plan", tags=["planning"])
def plan(body: PlanBody) -> Dict[str, Any]:
    if body.days < 1 or body.days > 14:
        raise HTTPException(status_code=422, detail="days must be between 1 and 14")
    slots = body.slots or ["dinner"]
    invalid = [s for s in slots if s not in ("breakfast", "lunch", "dinner")]
    if invalid:
        raise HTTPException(status_code=422, detail=f"unknown slots: {invalid}")

    engine = _engine(body.pantry, body.profile)
    payload = engine.plan_week(
        days=body.days,
        slots=slots,
        max_minutes=body.max_minutes,
        vegetarian=body.vegetarian,
        cuisine=body.cuisine,
        fill_missing=not body.leftovers_only,
    )
    payload["plan_text"] = render_plan_text(payload["entries"])
    return payload


@app.post("/shop", tags=["planning"])
def shop(body: ShopBody) -> Dict[str, Any]:
    if body.days < 1 or body.days > 14:
        raise HTTPException(status_code=422, detail="days must be between 1 and 14")

    engine = _engine(body.pantry, body.profile)
    plan_payload = engine.plan_week(
        days=body.days,
        slots=body.slots or ["dinner"],
        max_minutes=body.max_minutes,
        vegetarian=body.vegetarian,
        cuisine=body.cuisine,
    )

    recipes = []
    servings: Dict[str, int] = {}
    for entry in plan_payload["entries"]:
        recipe = engine.library.get(entry["recipe_id"])
        if recipe is None:
            continue
        recipes.append(recipe)
        servings[recipe.id] = int(entry.get("servings", recipe.servings))

    payload = build_shopping_list(
        recipes,
        engine.index,
        servings=servings,
        include_optional=body.include_optional,
        staples=engine.staples,
    )
    payload["for_plan"] = summarize_plan(plan_payload["entries"])
    payload["shopping_text"] = render_shopping_list_text(payload)
    return payload


@app.post("/search", tags=["recipes"])
def search(body: Dict[str, Any]) -> Dict[str, Any]:
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="'text' is required")
    limit = body.get("limit")
    library = RecipeLibrary.bundled()
    found = library.filter(text=text)
    if limit is not None:
        found = found[: int(limit)]
    return {
        "query": text,
        "count": len(found),
        "recipes": [
            {
                "id": r.id,
                "name": r.name,
                "minutes": r.minutes,
                "cuisine": r.cuisine,
                "tags": r.tags,
            }
            for r in found
        ],
    }


@app.get("/recipe/{recipe_id}", tags=["recipes"])
def recipe(recipe_id: str, servings: Optional[int] = None) -> Dict[str, Any]:
    library = RecipeLibrary.bundled()
    found = library.get(recipe_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"recipe not found: {recipe_id}")
    if servings is not None:
        if servings < 1 or servings > 50:
            raise HTTPException(status_code=422, detail="servings must be between 1 and 50")
        found = found.scale_to(servings)
    return found.to_dict()


@app.post("/recipe/{recipe_id}/scale", tags=["recipes"])
def scale(recipe_id: str, body: ScaleBody) -> Dict[str, Any]:
    library = RecipeLibrary.bundled()
    found = library.get(recipe_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"recipe not found: {recipe_id}")
    return found.scale_to(body.servings).to_dict()


@app.post("/pantry/parse", tags=["pantry"])
def parse_pantry(body: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise free-text pantry lines without running any planning."""
    from .models import PantryItem

    lines = body.get("pantry") or []
    if not isinstance(lines, list):
        raise HTTPException(status_code=400, detail="'pantry' must be a list of strings")
    parsed = [PantryItem.from_text(str(line)) for line in lines if str(line).strip()]
    return {
        "count": len(parsed),
        "items": [item.to_dict() for item in parsed],
        "unparsed": [i.name for i in parsed if not canonical_name(i.name)],
    }

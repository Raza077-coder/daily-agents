"""Shared pytest fixtures for the PANTRY test suite."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pantry.engine import PantryEngine, sample_engine, sample_pantry  # noqa: E402
from pantry.library import PantryIndex, RecipeLibrary  # noqa: E402
from pantry.models import PantryItem  # noqa: E402


@pytest.fixture(scope="session")
def library():
    return RecipeLibrary.bundled()


@pytest.fixture
def sample_pantry_lines():
    return sample_pantry()


@pytest.fixture
def engine():
    """Engine loaded with the bundled sample pantry."""
    return sample_engine()


@pytest.fixture
def empty_engine():
    return PantryEngine()


@pytest.fixture
def stock_index():
    """A deliberately tiny, fully-known pantry for exact assertions."""
    index = PantryIndex()
    for line in [
        "400 g spaghetti",
        "6 piece egg",
        "200 ml olive oil",
        "4 clove garlic",
        "500 g canned tomato",
        "300 g rice",
    ]:
        index.add(PantryItem.from_text(line))
    return index

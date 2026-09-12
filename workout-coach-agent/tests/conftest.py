"""Shared pytest fixtures for the FORGE test suite.

Every test gets its own training file in a temp directory. FORGE's whole value
proposition is that the same inputs produce the same answer, so tests must never
share mutable state \u2014 a leak between tests would hide exactly the kind of
non-determinism this suite exists to catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.engine import ForgeEngine  # noqa: E402
from forge.storage import Store  # noqa: E402


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """A blank training file in an isolated temp directory."""
    return Store(tmp_path / "training.json")


@pytest.fixture
def engine(store: Store) -> ForgeEngine:
    """An engine with an 80 kg intermediate lifter already profiled."""
    eng = ForgeEngine(store)
    eng.init_profile(
        bodyweight_kg=80.0, experience="intermediate", goal="strength"
    )
    return eng


@pytest.fixture
def beginner_engine(store: Store) -> ForgeEngine:
    eng = ForgeEngine(store)
    eng.init_profile(bodyweight_kg=70.0, experience="beginner", goal="hypertrophy")
    return eng


@pytest.fixture
def home_gym_engine(store: Store) -> ForgeEngine:
    """Dumbbells and bodyweight only \u2014 exercises the equipment filter."""
    eng = ForgeEngine(store)
    eng.init_profile(
        bodyweight_kg=75.0,
        experience="intermediate",
        goal="hypertrophy",
        equipment=["dumbbell", "bodyweight"],
    )
    return eng

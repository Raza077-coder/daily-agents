"""Shared fixtures for the JOBFLOW test suite.

Everything here is deterministic: the reference date is pinned, and every
engine is built over a throwaway vault so no test can leak state into another.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobtracker.engine import JobFlowEngine  # noqa: E402
from jobtracker.models import Application, Event  # noqa: E402
from jobtracker.pipeline import Pipeline  # noqa: E402
from jobtracker.store import Vault  # noqa: E402

#: The single reference date every test reasons against.
TODAY = date(2026, 9, 17)
TODAY_STR = TODAY.isoformat()


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """An empty vault backed by a real file in a temp directory."""
    return Vault(path=tmp_path / "jobflow.json")


@pytest.fixture
def engine(vault: Vault) -> JobFlowEngine:
    return JobFlowEngine(vault=vault, path=vault.path)


@pytest.fixture
def pipeline(vault: Vault) -> Pipeline:
    return Pipeline(vault)


@pytest.fixture
def demo(engine: JobFlowEngine) -> JobFlowEngine:
    """The engine loaded with the built-in 90-day demo dataset."""
    engine.seed_demo(today=TODAY, force=True)
    return engine


def make_app(app_id: str = "acme-swe", **kw) -> Application:
    """Build an Application with sane defaults for focused unit tests."""
    fields = {
        "id": app_id,
        "company": "Acme",
        "role": "Engineer",
        "status": "wishlist",
        "created_on": TODAY_STR,
    }
    fields.update(kw)
    return Application(**fields)


def applied_app(app_id: str, applied_on: str = "2026-09-01", **kw) -> Application:
    """An application that was genuinely submitted on ``applied_on``."""
    fields = {
        "id": app_id,
        "company": "Acme",
        "role": "Engineer",
        "status": "applied",
        "applied_on": applied_on,
        "created_on": applied_on,
    }
    fields.update(kw)
    app = Application(**fields)
    app.events.append(
        Event(on=applied_on, kind="applied", from_status="wishlist", to_status="applied")
    )
    app.events.sort(key=lambda e: (e.on, e.kind))
    return app

"""Shared pytest fixtures and path setup for the SplitKit suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from splitkit import SplitKit, Group, Config  # noqa: E402
from splitkit.config import load_config  # noqa: E402
from splitkit.demo import build_demo_group  # noqa: E402


@pytest.fixture
def cfg() -> Config:
    """A config that ignores any config file/env on the machine."""
    return load_config(use_env=False, overrides={"persona_file": ""})


@pytest.fixture
def empty_kit(cfg: Config, tmp_path: Path) -> SplitKit:
    """A three-member group with no expenses, saved to a temp path."""
    kit = SplitKit.create(
        "Test Trip", ["Ali", "Sara", "Bilal"], path=str(tmp_path / "group.json"),
        config_path=None,
    )
    kit.config = cfg
    return kit


@pytest.fixture
def demo_kit(cfg: Config) -> SplitKit:
    """The seeded Goa Weekend group from splitkit.demo."""
    kit = SplitKit(group=build_demo_group("USD"))
    kit.config = cfg
    return kit


@pytest.fixture
def demo_group() -> Group:
    return build_demo_group("USD")

"""Shared pytest fixtures for the VAULTGUARD suite."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vaultguard.engine import VaultEngine  # noqa: E402

TEST_MASTER = "test-master-password-123"
FAST_ITERATIONS = 10_000  # keep the suite quick, still above the enforced minimum


@pytest.fixture()
def vault_path(tmp_path):
    """A path inside an isolated temp directory."""
    return str(tmp_path / "vault.json")


@pytest.fixture()
def engine(vault_path):
    """A created, unlocked vault with no entries."""
    eng = VaultEngine(vault_path, iterations=FAST_ITERATIONS)
    eng.create(TEST_MASTER)
    yield eng
    eng.lock()


@pytest.fixture()
def seeded(engine):
    """A vault with a small, deliberately mixed set of entries."""
    engine.add(
        title="GitHub",
        username="raza.dev",
        password="Xk9#mQ2vLp7$Zw4Rt8Nb",
        category="work",
        url="https://github.com",
        tags=["dev"],
        totp="JBSWY3DPEHPK3PXP",
        favorite=True,
        save=False,
    )
    engine.add(
        title="Personal Email",
        username="raza@example.com",
        password="summer2024!",
        category="email",
        save=False,
    )
    engine.add(
        title="Bank",
        username="raza-8842",
        password="Xk9#mQ2vLp7$Zw4Rt8Nb",  # deliberately reused
        category="banking",
        save=False,
    )
    engine.add(
        title="Old Forum",
        username="raza",
        password="qwerty123",
        category="other",
        save=False,
    )
    engine.save()
    return engine

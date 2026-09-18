"""Shared fixtures.

The detection tests are the ones that matter most, so they get helpers that make
assertions about *which values* were found rather than about offsets — offsets
are an implementation detail, values are the contract.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from veil import Scanner, demo  # noqa: E402
from veil.policy import Policy  # noqa: E402


@pytest.fixture
def scanner() -> Scanner:
    return Scanner()


@pytest.fixture
def vendor_email() -> str:
    return demo.DOCUMENTS["vendor_email.txt"]


@pytest.fixture
def deployment_env() -> str:
    return demo.DOCUMENTS["deployment.env"]


@pytest.fixture
def app_log() -> str:
    return demo.DOCUMENTS["app.log"]


@pytest.fixture
def all_documents():
    return dict(demo.DOCUMENTS)


def entities_found(result) -> set:
    """The set of entity types a result contains."""
    return {f.entity for f in result.findings}


@pytest.fixture
def tokenize_all_policy() -> Policy:
    """A policy that tokenizes every entity type, for round-trip tests."""
    from veil.models import ALL_ENTITIES

    policy = Policy(name="tokenize-all")
    policy.entities = list(ALL_ENTITIES)
    policy.actions = {entity: "tokenize" for entity in ALL_ENTITIES}
    return policy

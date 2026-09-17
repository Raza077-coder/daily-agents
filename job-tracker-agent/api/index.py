"""Vercel serverless entry point for the JOBFLOW API.

Vercel's Python runtime looks for an ASGI object named ``app`` in this module.
The real application lives in :mod:`api.app`; this file only re-exports it so
``vercel.json`` can point at a stable path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The engine package sits one level up from this file in the repo layout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A serverless filesystem is read-only apart from /tmp.
os.environ.setdefault("JOBFLOW_DATA_DIR", "/tmp")

from api.app import app  # noqa: E402  (path must be set up first)

__all__ = ["app"]

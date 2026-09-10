"""Serverless entrypoint for Vercel.

The platform imports this module and expects a WSGI/ASGI callable named ``app``.
``vaultguard/`` sits one directory up, so the project root goes on ``sys.path``
before the application is imported.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from vaultguard.api import app  # noqa: E402,F401  (re-exported for the runtime)

__all__ = ["app"]

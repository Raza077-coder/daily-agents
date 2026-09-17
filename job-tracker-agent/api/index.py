"""Wire entry point for the JOBFLOW REST layer on Vercel.

Vercel's Python runtime looks for an ASGI/WSGI callable named ``app`` in
``api/index.py``.  The implementation lives in ``api/app.py`` next door; this
module only re-exports it so the same application object serves both
``uvicorn api.app:app`` locally and the serverless function in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable when this file is executed as a function
# rather than as part of the package.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import app  # noqa: E402,F401

__all__ = ["app"]

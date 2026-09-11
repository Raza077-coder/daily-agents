"""Vercel's Python runtime looks for a module-level ASGI ``app``."""
from fastapi import FastAPI

from pantry.api import app as pantry_app

app: FastAPI = pantry_app

"""FastAPI backend for Work Order Planner (docs/API.md v0.1)."""

from .app import app, create_app

__all__ = ["app", "create_app"]

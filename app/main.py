"""Canonical ASGI entrypoint: uvicorn app.main:create_app --factory."""

from app.application import create_app as create_app

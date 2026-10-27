"""Request access to the application-owned Live services."""

from fastapi import Request

from app import composition


def get_services(request: Request) -> composition.LiveServices:
    return request.app.state.live

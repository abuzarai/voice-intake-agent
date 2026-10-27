"""The v2 composition root: explicit settings, per-app ownership and lifespan."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.live import router
from app.composition import LiveServices
from app.settings import Settings


def create_app(
    settings: Settings | None = None, *, services: LiveServices | None = None
) -> FastAPI:
    if services is None:
        services = LiveServices.build(settings if settings is not None else Settings())
    elif settings is not None and services.settings is not settings:
        raise ValueError("Injected services must own the supplied settings")

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            await services.shutdown()

    application = FastAPI(
        title="Insafdaar Voice Intake",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    services.bind(application)
    application.state.live = services
    application.include_router(router)

    @application.get("/health", include_in_schema=False)
    async def health():
        return {"status": "alive", "scope": "local-network-only"}

    return application

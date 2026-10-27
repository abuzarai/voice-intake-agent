"""Application-scoped Live service construction, ownership and shutdown."""

from dataclasses import dataclass, field

from app.services.live_runtime import LiveRuntime
from app.services.live_sessions import LiveSessionRegistry
from app.settings import Settings


@dataclass
class LiveServices:
    settings: Settings
    live_sessions: LiveSessionRegistry
    live_runtime: LiveRuntime
    _owner: object | None = field(default=None, init=False, repr=False)

    def bind(self, application: object) -> None:
        if self._owner is not None:
            raise ValueError("Live services already belong to an application")
        self._owner = application

    @classmethod
    def build(cls, settings: Settings, *, sessions=None, adapter_factory=None):
        registry = (
            sessions
            if sessions is not None
            else LiveSessionRegistry(
                ttl_seconds=settings.LIVE_CAPABILITY_TTL_SECONDS,
                max_sessions=settings.LIVE_MAX_SESSIONS,
                admissions_per_minute=settings.LIVE_ADMISSIONS_PER_MINUTE,
            )
        )
        runtime = LiveRuntime(registry, adapter_factory=adapter_factory)
        return cls(settings, registry, runtime)

    async def shutdown(self):
        try:
            await self.live_runtime.shutdown()
        finally:
            self.live_sessions.clear()

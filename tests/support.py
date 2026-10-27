"""Shared synthetic factories and fakes, never collected as tests or fixtures."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.live_audio_draft import FACT_FIELDS, TOOL_NAME
from app.services.live_runtime import LiveProviderConfig, LiveRuntime
from app.services.live_sessions import LiveSessionRegistry

CONFIG = LiveProviderConfig("invented-not-a-key", "explicit-fictional-model")


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def monotonic(self):
        return self.seconds

    def utc(self):
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds
        )


def registry(clock, **kwargs):
    return LiveSessionRegistry(
        ttl_seconds=kwargs.get("ttl_seconds", 300),
        max_sessions=kwargs.get("max_sessions", 16),
        admissions_per_minute=kwargs.get("admissions_per_minute", 6),
        monotonic=clock.monotonic,
        clock=clock.utc,
    )


class Connection:
    pc_id = "owned-peer"

    def __init__(self):
        self.events = {}
        self.closed = 0
        self.initialized = asyncio.Event()
        self.gate = None
        self.error = None
        self.answer = {"sdp": "fictional-answer", "type": "answer", "pc_id": self.pc_id}
        self.candidates = []

    def event_handler(self, name):
        def register(callback):
            self.events[name] = callback
            return callback

        return register

    async def initialize(self, **kwargs):
        assert kwargs == {"sdp": "fictional-offer", "type": "offer"}
        self.initialized.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error:
            raise self.error

    def get_answer(self):
        return self.answer

    async def add_ice_candidate(self, candidate):
        self.candidates.append(candidate)

    async def disconnect(self):
        self.closed += 1
        if "closed" in self.events:
            await self.events["closed"](self)


class Pipeline:
    def __init__(self):
        self.closed = 0
        self.error = None
        self.events = {}

    async def run(self):
        if self.error:
            raise self.error
        await asyncio.Event().wait()

    async def close(self):
        self.closed += 1


class Adapter:
    def __init__(self):
        self.peer = Connection()
        self.pipe = Pipeline()
        self.error = None
        self.created = 0

    def connection(self):
        self.created += 1
        return self.peer

    def pipeline(self, connection, config, **events):
        assert (
            connection is self.peer
            and config.api_key == CONFIG.api_key
            and config.model == CONFIG.model
        )
        self.pipe.events = events
        if self.error:
            raise self.error
        return self.pipe

    @staticmethod
    def candidate(candidate):
        return candidate


def setup(*, sleep=asyncio.sleep):
    clock = Clock()
    sessions = registry(clock)
    adapter = Adapter()
    runtime = LiveRuntime(
        sessions,
        adapter_factory=lambda: adapter,
        monotonic=clock.monotonic,
        sleep=sleep,
    )
    first, second = sessions.create(), sessions.create()
    return clock, sessions, adapter, runtime, first, second


async def assert_clean(runtime, adapter):
    await runtime.shutdown()
    await asyncio.sleep(0)
    assert runtime._active is None and not runtime._tasks
    assert adapter.peer.closed == 1


def bootstrap(api, **kwargs):
    _, config, _, _, client = api
    return client.post(
        "/api/v1/live/sessions",
        json=kwargs.pop("json", {"processing_consent": True}),
        headers=kwargs.pop(
            "headers",
            {
                "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value()
            },
        ),
        **kwargs,
    )


def bearer(body):
    return {"Authorization": "Bearer " + body["capability"]}


def assert_error(response, code):
    assert response.status_code == code, response.text
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()["detail"]) == {"message_en", "message_ur"}


def offer_url(issued):
    return "/api/v1/live/sessions/" + issued["session_id"] + "/webrtc/offer"


def offer(client, issued, **kwargs):
    return client.post(
        offer_url(issued),
        headers=kwargs.pop("headers", bearer(issued)),
        json=kwargs.pop("json", {"sdp": "fictional-offer", "type": "offer"}),
        **kwargs,
    )


def arguments():
    return {
        "facts": dict.fromkeys(FACT_FIELDS),
        "analysis": {
            "primary_language": "english",
            "legal_domain": "other",
            "confidence_score": 0.5,
            "key_entities": {},
            "issue_summary": "invented",
            "adr_suitable": False,
            "adr_reasoning": "unknown",
            "urgency": "low",
            "urgency_reasoning": "unknown",
        },
    }


def params(callback, data=None, **kwargs):
    return SimpleNamespace(
        function_name=TOOL_NAME,
        tool_call_id="tool-1",
        arguments=arguments() if data is None else data,
        result_callback=callback,
        **kwargs,
    )


async def accept_callback(result, *, properties):
    assert properties.run_llm is False
    await properties.on_context_updated()


async def audio_setup():
    from types import SimpleNamespace

    clock, sessions, adapter, runtime, first, second = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    call = runtime._active
    pipe = adapter.pipe
    pipe.ready = True
    pipe.gated = False

    def gate():
        pipe.gated = True

    pipe.gate_audio = gate
    call.audio_draft.result_roundtrip = lambda: asyncio.sleep(0)

    async def send():
        assert pipe.gated
        await call.audio_draft.handle_tool(
            params(accept_callback), properties_factory=SimpleNamespace
        )

    pipe.request_audio_draft = send
    return clock, sessions, adapter, runtime, first, second, call


async def request(app, path, headers, receive, method="POST"):
    events = []

    async def send(event):
        events.append(event)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("test", 80),
            "client": ("test", 1),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        },
        receive,
        send,
    )
    start, body = events[0], b"".join(e.get("body", b"") for e in events[1:])
    return start["status"], dict(start["headers"]), json.loads(body)

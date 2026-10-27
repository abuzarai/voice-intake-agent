"""Default-off synthetic memory-only Live. No state commands or finalize API."""

import asyncio
import hmac
import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)

from app.api.dependencies import get_services
from app.models.live import LiveSnapshot
from app.models.live_audio_draft import AudioDraftRequest, AudioDraftView
from app.models.live_transcript import TranscriptBatch, TranscriptRead
from app.services.live_runtime import (
    LiveCleanupPending,
    LiveProviderConfig,
    LiveRuntimeConflict,
    LiveRuntimeFailure,
    LiveRuntimeUnavailable,
)
from app.services.live_sessions import (
    LiveAdmissionFull,
    LiveAdmissionRateLimited,
    LiveSessionUnauthorized,
)
from app.services.live_transcript import encode_batch

JSON_BODY_READ_TIMEOUT_SECONDS = 5
MAX_BOOTSTRAP_BYTES = 256
MAX_SIGNALING_BYTES = 73728
NO_STORE = {"Cache-Control": "no-store"}


def _error(code: int, message_en: str, message_ur: str) -> HTTPException:
    return HTTPException(
        code,
        detail={"message_en": message_en, "message_ur": message_ur},
        headers=NO_STORE,
    )


def _cleanup_pending_error() -> HTTPException:
    return _error(
        503,
        "Live cleanup pending; capacity unavailable",
        "لائیو صفائی زیر التوا ہے؛ گنجائش دستیاب نہیں",
    )


class _SensitiveRoute(APIRoute):
    async def handle(self, scope, receive, send):
        if self.methods and scope["method"] not in self.methods:
            response = JSONResponse(
                {
                    "detail": {
                        "message_en": "Method not allowed",
                        "message_ur": "اس طریقے کی اجازت نہیں",
                    }
                },
                status_code=405,
                headers={**NO_STORE, "Allow": ", ".join(sorted(self.methods))},
            )
            await response(scope, receive, send)
            return
        await super().handle(scope, receive, send)

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request) -> Response:
            try:
                response = await handler(request)
            except Exception as exc:
                # Never interpolate request bodies/headers or unexpected exceptions
                # into logs or responses (including the app's global error logger).
                if isinstance(exc, HTTPException):
                    response = JSONResponse(
                        {"detail": exc.detail}, status_code=exc.status_code
                    )
                else:
                    response = JSONResponse(
                        {
                            "detail": {
                                "message_en": "Live request failed",
                                "message_ur": "لائیو درخواست ناکام ہوئی",
                            }
                        },
                        status_code=500,
                    )
            response.headers.update(NO_STORE)
            return response

        return protected


router = APIRouter(
    prefix="/api/v1/live/sessions", tags=["live"], route_class=_SensitiveRoute
)


class LiveBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processing_consent: StrictBool


def _gate(request: Request) -> None:
    settings = get_services(request).settings
    if not settings.LIVE_ENABLED or settings.LIVE_BOOTSTRAP_SECRET is None:
        raise _error(404, "Live admission unavailable", "لائیو داخلہ دستیاب نہیں")
    # Missing Origin is allowed for trusted server callers. A supplied Origin
    # must exactly match the configured browser allowlist; wildcard is not auth.
    origins = request.headers.getlist("origin")
    if origins and (
        len(origins) != 1
        or origins[0] in ("null", "*")
        or origins[0] not in settings.cors_origins_list
    ):
        raise _error(403, "Origin not allowed", "اس اصل کی اجازت نہیں")
    if request.url.query:
        raise _error(
            400, "Query parameters are not supported", "کوئری پیرامیٹرز کی اجازت نہیں"
        )


def _bootstrap_auth(request: Request) -> None:
    values = request.headers.getlist("x-live-bootstrap-secret")
    supplied = values[0] if len(values) == 1 else ""
    expected = get_services(request).settings.LIVE_BOOTSTRAP_SECRET.get_secret_value()
    if not 32 <= len(supplied) <= 256 or not hmac.compare_digest(
        supplied.encode(), expected.encode()
    ):
        raise _error(401, "Unauthorized Live request", "غیر مجاز لائیو درخواست")


def _capability(request: Request) -> str:
    values = request.headers.getlist("authorization")
    value = values[0] if len(values) == 1 else ""
    if len(value) != 50:  # "Bearer " plus the 43-character opaque capability.
        raise _error(401, "Unauthorized Live request", "غیر مجاز لائیو درخواست")
    parts = value.split(" ")
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or len(parts[1]) != 43
        or not parts[1].isascii()
    ):
        raise _error(401, "Unauthorized Live request", "غیر مجاز لائیو درخواست")
    return parts[1]


async def _json_body(request: Request, limit: int):
    if (
        request.headers.get("content-type", "").split(";")[0].strip().lower()
        != "application/json"
    ):
        raise _error(415, "JSON body required", "JSON درخواست ضروری ہے")
    body = bytearray()
    deadline = asyncio.timeout(JSON_BODY_READ_TIMEOUT_SECONDS)
    try:
        async with deadline:
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise _error(
                        413, "Live request too large", "لائیو درخواست بہت بڑی ہے"
                    )
                body.extend(chunk)
    except TimeoutError:
        if not deadline.expired():
            raise
        raise _error(
            408, "Live request body timed out", "لائیو درخواست کا وقت ختم ہو گیا"
        ) from None
    try:
        return json.loads(body)
    except ValueError:
        raise _error(422, "Invalid Live request", "غلط لائیو درخواست") from None


async def _bootstrap_body(request: Request) -> LiveBootstrap:
    body = await _json_body(request, MAX_BOOTSTRAP_BYTES)
    try:
        payload = LiveBootstrap.model_validate(body)
    except ValueError:
        raise _error(422, "Invalid Live bootstrap", "غلط لائیو داخلہ درخواست") from None
    if payload.processing_consent is not True:
        raise _error(
            422,
            "Voice processing consent required",
            "آڈیو اور معلومات کی پروسیسنگ کی اجازت ضروری ہے",
        )
    return payload


@router.post("", status_code=201)
async def create_live_session(request: Request):
    _gate(request)
    _bootstrap_auth(request)
    await _bootstrap_body(request)
    if get_services(request).live_runtime.admission_blocked:
        raise _cleanup_pending_error()
    try:
        issued = get_services(request).live_sessions.create()
    except LiveAdmissionRateLimited:
        raise _error(
            429, "Live admission rate exceeded", "لائیو داخلے کی شرح کی حد پوری ہے"
        ) from None
    except LiveAdmissionFull:
        raise _error(
            503, "Live admission capacity reached", "لائیو داخلے کی گنجائش پوری ہے"
        ) from None
    base = f"/api/v1/live/sessions/{issued.session_id}"
    return {
        "session_id": issued.session_id,
        "created_at": issued.created_at,
        "expires_at": issued.expires_at,
        "capability": issued.capability,
        "token_type": "Bearer",
        "state_url": f"{base}/state",
        "stop_url": f"{base}/stop",
    }


def _access(request: Request, session_id: str, *, stop: bool) -> LiveSnapshot:
    _gate(request)
    capability = _capability(request)
    try:
        sessions = get_services(request).live_sessions
        if stop:
            return sessions.stop(session_id, capability)
        return sessions.snapshot(session_id, capability)
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None


@router.get("/{session_id}/state", response_model=LiveSnapshot)
async def get_live_state(request: Request, session_id: str):
    snapshot = _access(request, session_id, stop=False)
    try:
        await get_services(request).live_runtime.drain_session(session_id)
    except LiveCleanupPending:
        raise _cleanup_pending_error() from None
    return snapshot


@router.post("/{session_id}/stop", response_model=LiveSnapshot)
async def stop_live_session(request: Request, session_id: str):
    snapshot = _access(request, session_id, stop=True)
    try:
        await get_services(request).live_runtime.drain_session(session_id)
    except LiveCleanupPending:
        raise _cleanup_pending_error() from None
    return snapshot


SignalText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=65536)
]
PeerId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]


class LiveOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: SignalText
    type: Literal["offer"]
    # Initial offer ONLY: no client pc_id, restart_pc or request_data accepted.


class LiveCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: Annotated[str, StringConstraints(strict=True, max_length=2048)]
    sdp_mid: Annotated[str, StringConstraints(strict=True, max_length=32)]
    sdp_mline_index: Annotated[StrictInt, Field(ge=0, le=15)]


class LivePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pc_id: PeerId
    candidates: Annotated[list[LiveCandidate], Field(min_length=1, max_length=32)]


async def _signaling(request, session_id, *, patch):
    _gate(request)
    capability = _capability(request)
    services = get_services(request)
    try:
        # Authenticate before reading SDP/candidates or importing optional SDKs.
        services.live_sessions.snapshot(session_id, capability)
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None
    body = await _json_body(request, MAX_SIGNALING_BYTES)
    try:
        payload = (LivePatch if patch else LiveOffer).model_validate(body)
        if not patch and len(payload.sdp.encode()) > 65536:
            raise ValueError()
    except ValueError:
        raise _error(422, "Invalid Live signaling", "غلط لائیو سگنلنگ") from None
    try:
        if patch:
            await services.live_runtime.patch(
                session_id, capability, payload.pc_id, payload.candidates
            )
            return {"status": "accepted"}
        config = LiveProviderConfig.from_settings(services.settings)
        return await services.live_runtime.offer(
            session_id, capability, payload.sdp, config
        )
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None
    except LiveRuntimeUnavailable:
        raise _error(
            503,
            "Live configuration or Python 3.12 dependencies unavailable",
            "لائیو ترتیب یا Python 3.12 پیکیجز دستیاب نہیں",
        ) from None
    except LiveRuntimeConflict:
        raise _error(
            409, "Live call unavailable or peer mismatch", "لائیو کال دستیاب نہیں"
        ) from None
    except LiveRuntimeFailure:
        raise _error(502, "Live call failed", "لائیو کال ناکام ہوئی") from None


@router.post("/{session_id}/webrtc/offer")
async def offer_live_call(request: Request, session_id: str):
    return await _signaling(request, session_id, patch=False)


@router.patch("/{session_id}/webrtc/offer")
async def patch_live_call(request: Request, session_id: str):
    return await _signaling(request, session_id, patch=True)


def _audio_draft_access(request, session_id):
    _access(request, session_id, stop=False)
    try:
        LiveProviderConfig.from_settings(get_services(request).settings)
    except LiveRuntimeUnavailable:
        raise _error(
            503, "Live configuration unavailable", "لائیو ترتیب دستیاب نہیں"
        ) from None
    return _capability(request)


@router.get("/{session_id}/audio-draft", response_model=AudioDraftView)
async def get_live_audio_draft(request: Request, session_id: str):
    capability = _audio_draft_access(request, session_id)
    try:
        return get_services(request).live_runtime.audio_draft_view(
            session_id, capability
        )
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None
    except LiveRuntimeConflict:
        raise _error(
            409, "Live audio draft unavailable", "لائیو آڈیو مسودہ دستیاب نہیں"
        ) from None


@router.post("/{session_id}/audio-draft", response_model=AudioDraftView)
async def request_live_audio_draft(request: Request, session_id: str):
    capability = _audio_draft_access(request, session_id)
    body = await _json_body(request, 256)
    try:
        AudioDraftRequest.model_validate(body)
    except ValueError:
        raise _error(
            422,
            "Explicit unproven-tail acknowledgment required",
            "غیر ثابت آڈیو تکمیل کی تصدیق ضروری ہے",
        ) from None
    try:
        return await get_services(request).live_runtime.prepare_audio_draft(
            session_id, capability
        )
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None
    except LiveRuntimeConflict:
        raise _error(
            409,
            "Live audio draft unavailable or attempt used; use GET",
            "لائیو آڈیو مسودہ دستیاب نہیں یا کوشش استعمال ہو چکی ہے",
        ) from None
    except LiveRuntimeFailure:
        raise _error(
            502, "Live audio draft preparation failed", "لائیو آڈیو مسودہ تیار نہیں ہوا"
        ) from None


@router.post("/{session_id}/transcript/read", response_model=TranscriptBatch)
async def read_live_transcript(request: Request, session_id: str):
    _gate(request)
    capability = _capability(request)
    sessions = get_services(request).live_sessions
    try:
        sessions.transcript(session_id, capability)
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None
    body = await _json_body(request, 256)
    try:
        payload = TranscriptRead.model_validate(body)
    except ValueError:
        raise _error(422, "Invalid Live request", "غلط لائیو درخواست") from None
    try:
        journal = sessions.transcript(session_id, capability)
        batch = journal.read(payload.after_sequence)
    except LiveSessionUnauthorized:
        raise _error(
            401, "Unauthorized Live request", "غیر مجاز لائیو درخواست"
        ) from None
    except ValueError:
        raise _error(422, "Invalid Live request", "غلط لائیو درخواست") from None
    return Response(
        encode_batch(batch), media_type="application/json", headers=NO_STORE
    )

"""WebSocket handler for realtime audio streaming."""

import json
import asyncio
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from app.models import (
    AudioMessage,
    TranscriptMessage,
    EndInterviewMessage,
    ResultsMessage,
    ErrorMessage,
    StatusMessage,
    MessageType,
    SessionStatus,
    WebhookPayload,
)
from app.services.session_service import session_manager
from app.services.stt_service import stt_service
from app.services.gemini_service import gemini_service
from app.services.storage_service import storage_service
from app.services.webhook_service import webhook_service
from app.utils import get_logger, base64_to_pcm, check_audio_quality
from app.config import settings

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/listen-only", tags=["websocket-listen-only"])


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        """Accept new WebSocket connection."""
        await websocket.accept()
        self.active_connections[session_id] = websocket
        logger.info("WebSocket connected", "WebSocket منسلک ہوا", session_id=session_id)

    def disconnect(self, session_id: str):
        """Remove WebSocket connection."""
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            logger.info(
                "WebSocket disconnected", "WebSocket منقطع ہوا", session_id=session_id
            )

    async def send_message(self, session_id: str, message: dict):
        """Send message to client."""
        if session_id in self.active_connections:
            await self.active_connections[session_id].send_json(message)

    async def send_transcript(
        self,
        session_id: str,
        text: str,
        is_final: bool,
        language: Optional[str] = None,
        confidence: Optional[float] = None,
    ):
        """Send transcript update to client."""
        message = TranscriptMessage(
            type=MessageType.TRANSCRIPT,
            text=text,
            is_final=is_final,
            language=language,
            confidence=confidence,
        )
        await self.send_message(session_id, message.model_dump(mode="json"))

    async def send_error(
        self,
        session_id: str,
        message_en: str,
        message_ur: str,
        code: Optional[str] = None,
    ):
        """Send error message to client."""
        message = ErrorMessage(
            type=MessageType.ERROR,
            message_en=message_en,
            message_ur=message_ur,
            code=code,
        )
        await self.send_message(session_id, message.model_dump(mode="json"))

    async def send_status(self, session_id: str, message_en: str, message_ur: str):
        """Send status update to client."""
        message = StatusMessage(
            type=MessageType.STATUS, message_en=message_en, message_ur=message_ur
        )
        await self.send_message(session_id, message.model_dump(mode="json"))


manager = ConnectionManager()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for audio streaming.

    Protocol:
    - Client sends AudioMessage with base64-encoded PCM audio
    - Server responds with TranscriptMessage for live updates
    - Client sends EndInterviewMessage when done
    - Server responds with ResultsMessage containing analysis
    """
    # Verify session exists
    session = session_manager.get_session(session_id)
    if not session:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Invalid session ID"
        )
        return

    # Check session not expired
    if session["status"] == SessionStatus.EXPIRED:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Session expired"
        )
        return

    # Accept connection
    logger.info(
        "Accepting listen-only WebSocket connection",
        "WebSocket کنکشن قبول کیا جا رہا ہے",
        session_id=session_id,
    )
    await manager.connect(session_id, websocket)
    session_manager.update_session_status(session_id, SessionStatus.IN_PROGRESS)

    # Send welcome message
    await manager.send_status(
        session_id,
        "Interview started. Please speak clearly.",
        "انٹرویو شروع ہو گیا۔ براہ کرم صاف بولیں۔",
    )

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            message_type = (message.get("type") or "").lower()
            logger.info(
                "Received listen-only message",
                "لِسن اونلی پیغام موصول ہوا",
                session_id=session_id,
                message_type=message_type,
                payload_keys=list(message.keys()),
            )

            if message_type == MessageType.AUDIO:
                # Process audio chunk
                await handle_audio_chunk(session_id, message)

            elif message_type == MessageType.END_INTERVIEW:
                # Interview complete, process results
                await handle_interview_end(session_id)
                break

            else:
                logger.warning(
                    f"Unknown message type: {message_type}",
                    f"نامعلوم پیغام کی قسم: {message_type}",
                    session_id=session_id,
                )

    except WebSocketDisconnect:
        logger.info("Client disconnected", "کلائنٹ منقطع ہوا", session_id=session_id)
    except Exception as e:
        logger.error(
            f"WebSocket error: {str(e)}",
            f"WebSocket خرابی: {str(e)}",
            session_id=session_id,
        )
        await manager.send_error(
            session_id,
            f"An error occurred: {str(e)}",
            f"ایک خرابی پیش آئی: {str(e)}",
            code="WEBSOCKET_ERROR",
        )
    finally:
        manager.disconnect(session_id)


async def handle_audio_chunk(session_id: str, message: dict):
    """Process incoming audio chunk.

    Args:
        session_id: Session identifier
        message: Audio message from client
    """
    try:
        # Decode base64 audio
        audio_base64 = message.get("audio", "")
        audio_bytes = base64_to_pcm(audio_base64)
        logger.info(
            "Received audio chunk",
            "آڈیو chunk موصول ہوا",
            session_id=session_id,
            base64_len=len(audio_base64),
            bytes_len=len(audio_bytes),
        )

        # Check audio quality
        quality = check_audio_quality(audio_bytes)
        if quality["is_silent"]:
            logger.warning(
                "Silent audio detected", "خاموش آڈیو ملی", session_id=session_id
            )

        # Store audio chunk
        session_manager.add_audio_chunk(session_id, audio_bytes)

        # Send to STT (simplified - actual implementation needs streaming)
        # For MVP, we'll transcribe complete audio at the end
        # Real-time transcription not implemented in MVP

    except Exception as e:
        logger.error(
            f"Error processing audio chunk: {str(e)}",
            f"آڈیو chunk پروسیس کرنے میں خرابی: {str(e)}",
            session_id=session_id,
        )
        await manager.send_error(
            session_id,
            "Failed to process audio",
            "آڈیو پروسیس کرنے میں ناکامی",
            code="AUDIO_PROCESSING_ERROR",
        )


async def handle_interview_end(session_id: str):
    """Process interview completion.

    Transcribes complete audio, analyzes with Gemini, uploads audio, sends webhook.

    Args:
        session_id: Session identifier
    """
    try:
        # Update status
        await manager.send_status(
            session_id, "Processing interview...", "انٹرویو پر کارروائی ہو رہی ہے..."
        )

        # Get complete audio
        complete_audio_chunks = session_manager._audio_chunks.get(session_id, [])
        # Use the last received blob (full webm) to avoid invalid concatenation
        complete_audio = complete_audio_chunks[-1] if complete_audio_chunks else b""
        logger.info(
            "Processing interview completion",
            "انٹرویو مکمل ہونے کی کارروائی",
            session_id=session_id,
            audio_bytes=len(complete_audio),
        )

        if not complete_audio or len(complete_audio) < 1000:
            await manager.send_error(
                session_id,
                "Interview too short or no audio received",
                "انٹرویو بہت چھوٹا ہے یا کوئی آڈیو نہیں ملی",
                code="INSUFFICIENT_AUDIO",
            )
            return

        # Transcribe audio
        await manager.send_status(
            session_id, "Transcribing audio...", "آڈیو کو transcribe کیا جا رہا ہے..."
        )
        transcription = stt_service.recognize_audio(complete_audio)

        if not transcription or not transcription.get("text"):
            await manager.send_error(
                session_id,
                "Failed to transcribe audio",
                "آڈیو transcribe کرنے میں ناکامی",
                code="TRANSCRIPTION_ERROR",
            )
            session_manager.update_session_status(session_id, SessionStatus.FAILED)
            return

        transcript_text = transcription["text"]
        session_manager.add_transcript(session_id, transcript_text, is_final=True)

        # Send transcript to client
        await manager.send_transcript(
            session_id,
            transcript_text,
            is_final=True,
            language=transcription.get("language"),
            confidence=transcription.get("confidence"),
        )

        # Analyze with Gemini
        await manager.send_status(
            session_id, "Analyzing legal issue...", "قانونی مسئلے کا تجزیہ کر رہی ہے..."
        )

        session = session_manager.get_session(session_id) or {}
        preferred_language = session.get("metadata", {}).get("language")
        analysis = await gemini_service.analyze_transcript(
            transcript_text,
            session_id,
            preferred_language=preferred_language,
        )

        if not analysis:
            await manager.send_error(
                session_id,
                "Failed to analyze transcript",
                "transcript کا تجزیہ کرنے میں ناکامی",
                code="ANALYSIS_ERROR",
            )
            session_manager.update_session_status(session_id, SessionStatus.FAILED)
            return

        session_manager.store_analysis(session_id, analysis)

        # Upload audio to Cloud Storage
        audio_url = await storage_service.upload_audio(session_id, complete_audio)

        if audio_url:
            session = session_manager.get_session(session_id)
            if session:
                session["audio_url"] = audio_url

        # Mark session complete
        session_manager.update_session_status(session_id, SessionStatus.COMPLETED)

        # Send results to client
        result_message = ResultsMessage(
            type=MessageType.RESULTS,
            session_id=session_id,
            transcript=transcript_text,
            analysis=analysis,
        )
        await manager.send_message(session_id, result_message.model_dump(mode="json"))

        # Send webhook to Express backend
        session = session_manager.get_session(session_id)
        if session:
            webhook_payload = WebhookPayload(
                session_id=session_id,
                client_id=session.get("client_id"),
                transcript=transcript_text,
                analysis=analysis,
                audio_url=audio_url,
                audio_duration_seconds=session_manager.get_audio_duration(session_id),
                completed_at=session.get("completed_at"),
                metadata=session.get("metadata", {}),
            )

            # Send webhook (non-blocking)
            asyncio.create_task(webhook_service.send_results(webhook_payload))

        logger.info(
            "Interview processing complete",
            "انٹرویو کی کارروائی مکمل",
            session_id=session_id,
            legal_domain=analysis.legal_domain.value,
        )

    except Exception as e:
        logger.error(
            f"Error processing interview end: {str(e)}",
            f"انٹرویو ختم کرنے میں خرابی: {str(e)}",
            session_id=session_id,
        )
        await manager.send_error(
            session_id,
            f"Failed to process interview: {str(e)}",
            f"انٹرویو پر کارروائی میں ناکامی: {str(e)}",
            code="PROCESSING_ERROR",
        )
        session_manager.update_session_status(session_id, SessionStatus.FAILED)

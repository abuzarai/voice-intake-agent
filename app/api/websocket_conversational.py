"""Conversational WebSocket handler for interactive voice interviews - FIXED VERSION."""

import json
import asyncio
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.models import (
    TranscriptMessage,
    AgentSpeechMessage,
    ConversationStatusMessage,
    ResultsMessage,
    ErrorMessage,
    MessageType,
    SessionStatus,
    WebhookPayload,
)
from app.services.session_service import session_manager
from app.services.stt_service import stt_service
from app.services.gemini_service import gemini_service
from app.services.storage_service import storage_service
from app.services.webhook_service import webhook_service
from app.services.tts_service import tts_service
from app.services.conversation_service import conversation_manager
from app.utils import get_logger, base64_to_pcm

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["websocket"])


class ConversationalConnectionManager:
    """Manage conversational WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.user_speaking: dict[str, bool] = {}  # Track if user is speaking

    async def connect(self, session_id: str, websocket: WebSocket):
        """Accept new WebSocket connection."""
        await websocket.accept()
        self.active_connections[session_id] = websocket
        self.user_speaking[session_id] = False

        logger.info("WebSocket connected", "WebSocket منسلک ہوا", session_id=session_id)

    def disconnect(self, session_id: str):
        """Remove WebSocket connection."""
        if session_id in self.active_connections:
            del self.active_connections[session_id]
        if session_id in self.user_speaking:
            del self.user_speaking[session_id]

        logger.info(
            "WebSocket disconnected", "WebSocket منقطع ہوا", session_id=session_id
        )

    async def send_message(self, session_id: str, message: dict):
        """Send message to client."""
        if session_id in self.active_connections:
            await self.active_connections[session_id].send_json(message)

    async def send_agent_speech(
        self, session_id: str, text: str, language: str, sequence: int = 0
    ):
        """
        Send agent's speech to client.

        Args:
            session_id: Session identifier
            text: What agent is saying
            language: Language code
            sequence: Message sequence number
        """
        try:
            # Generate speech audio
            audio_base64 = await tts_service.synthesize_to_base64(text, language)

            # Send to client
            message = AgentSpeechMessage(
                audio=audio_base64, text=text, language=language, sequence=sequence
            )
            await self.send_message(session_id, message.model_dump(mode="json"))

            logger.info(
                f"Sent agent speech: {text[:50]}...",
                f"ایجنٹ کی آواز بھیجی: {text[:50]}...",
                session_id=session_id,
            )

        except Exception as e:
            logger.error(
                f"Failed to send agent speech: {str(e)}",
                f"ایجنٹ کی آواز بھیجنے میں ناکامی: {str(e)}",
                session_id=session_id,
            )

    async def send_conversation_status(
        self,
        session_id: str,
        agent_state: str,
        can_speak: bool,
        message_en: str,
        message_ur: str,
    ):
        """Send conversation status update."""
        message = ConversationStatusMessage(
            agent_state=agent_state,
            can_speak=can_speak,
            message_en=message_en,
            message_ur=message_ur,
        )
        await self.send_message(session_id, message.model_dump(mode="json"))

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


manager = ConversationalConnectionManager()
turn_audio_buffers: dict[str, list[bytes]] = {}


@router.websocket("/ws/{session_id}")
async def conversational_websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Conversational WebSocket endpoint for interactive voice interviews.

    Flow:
    1. Agent asks greeting question (speaks)
    2. User answers (speaks, STT transcribes)
    3. Agent processes answer, asks next question
    4. Repeat until interview complete
    5. Agent provides summary and results
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
        "Accepting conversational WebSocket connection",
        "بات چیت والا WebSocket کنکشن قبول کیا جا رہا ہے",
        session_id=session_id,
    )
    await manager.connect(session_id, websocket)
    session_manager.update_session_status(session_id, SessionStatus.IN_PROGRESS)

    # Initialize turn audio buffer for this session
    turn_audio_buffers[session_id] = []

    try:
        # Start interview - agent speaks first
        await start_conversational_interview(session_id)

        # Main conversation loop
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            message_type = (message.get("type") or "").lower()
            logger.info(
                "Received WebSocket message",
                "WebSocket پیغام موصول ہوا",
                session_id=session_id,
                message_type=message_type,
                payload_keys=list(message.keys()),
            )

            if message_type == "audio":
                # User is speaking - process audio
                await handle_user_speech(session_id, message)

            elif message_type == "user_finished":
                # User finished speaking - process answer and get next question
                await process_user_answer_and_respond(session_id)

            elif message_type in ("end_interview", "end-interview"):
                # User manually ended interview
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
        # Clean up turn audio buffer
        if session_id in turn_audio_buffers:
            del turn_audio_buffers[session_id]


async def start_conversational_interview(session_id: str):
    """
    Start the conversational interview.

    Agent speaks the greeting question first.
    """
    try:
        # Get session to read language preference from metadata
        session = session_manager.get_session(session_id)
        metadata = session.get("metadata", {}) if session else {}
        selected_lang = metadata.get("language", "Urdu")  # Default to Urdu

        # Map language selection to language codes
        if selected_lang.lower() == "english":
            language = "en"
            language_code = "en-US"
        else:
            language = "ur"
            language_code = "ur-PK"

        logger.info(
            f"Starting interview in {selected_lang} ({language_code})",
            f"انٹرویو {selected_lang} ({language_code}) میں شروع ہو رہا ہے",
            session_id=session_id,
            selected_language=selected_lang,
        )

        # Get first question from conversation manager
        first_question = conversation_manager.start_interview(session_id, language)

        # Agent is speaking
        await manager.send_conversation_status(
            session_id,
            agent_state="speaking",
            can_speak=False,
            message_en="Agent is speaking...",
            message_ur="ایجنٹ بول رہا ہے...",
        )

        # Send agent's speech
        await manager.send_agent_speech(
            session_id, text=first_question, language=language_code, sequence=0
        )
        logger.info(
            "Agent spoke greeting",
            "ایجنٹ نے خیرمقدم کہا",
            session_id=session_id,
            chars=len(first_question),
            language=language_code,
        )

        # Wait for TTS to finish (rough estimate: 1 sec per 10 words)
        words = len(first_question.split())
        wait_time = max(2, words / 10)
        await asyncio.sleep(wait_time)

        # Agent finished speaking, user can now speak
        await manager.send_conversation_status(
            session_id,
            agent_state="listening",
            can_speak=True,
            message_en="You can speak now.",
            message_ur="اب آپ بول سکتے ہیں۔",
        )

        logger.info(
            "Interview started with first question",
            "پہلے سوال کے ساتھ انٹرویو شروع ہوا",
            session_id=session_id,
        )

    except Exception as e:
        logger.error(
            f"Error starting interview: {str(e)}",
            f"انٹرویو شروع کرنے میں خرابی: {str(e)}",
            session_id=session_id,
        )
        # Send error to client instead of silently disconnecting
        await manager.send_error(
            session_id,
            f"Error starting interview: {str(e)}",
            f"انٹرویو شروع کرنے میں خرابی: {str(e)}",
            code="INTERVIEW_START_ERROR",
        )
        # Still allow user to start by sending listening status
        await manager.send_conversation_status(
            session_id,
            agent_state="listening",
            can_speak=True,
            message_en="Please speak now. (Error occurred, but you can continue)",
            message_ur="براہ کرم اب بولیں۔ (خرابی ہوئی لیکن آپ جاری رکھ سکتے ہیں)",
        )


async def handle_user_speech(session_id: str, message: dict):
    """
    Handle user's speech input.

    Stores audio chunk for later transcription.
    """
    try:
        # Decode audio
        audio_base64 = message.get("audio", "")
        if not audio_base64:
            logger.warning(
                "Received empty audio message",
                "خالی آڈیو پیغام موصول ہوا",
                session_id=session_id,
            )
            return

        audio_bytes = base64_to_pcm(audio_base64)

        logger.info(
            f"[DEBUG] Received audio message: base64_len={len(audio_base64)}, decoded_bytes={len(audio_bytes)}",
            f"[DEBUG] آڈیو موصول ہوئی: base64_len={len(audio_base64)}, decoded_bytes={len(audio_bytes)}",
            session_id=session_id,
        )

        # Store audio chunk for full-session archive
        session_manager.add_audio_chunk(session_id, audio_bytes)

        # Store audio chunk for current turn - CRITICAL FIX
        if session_id not in turn_audio_buffers:
            turn_audio_buffers[session_id] = []
        turn_audio_buffers[session_id].append(audio_bytes)

        logger.info(
            f"[DEBUG] Turn audio buffer now has {len(turn_audio_buffers[session_id])} chunks, total {sum(len(c) for c in turn_audio_buffers[session_id])} bytes",
            f"[DEBUG] ٹرن آڈیو بفر میں اب {len(turn_audio_buffers[session_id])} چنکس ہیں",
            session_id=session_id,
        )

    except Exception as e:
        logger.error(
            f"Error handling user speech: {str(e)}",
            f"صارف کی آواز ہینڈل کرنے میں خرابی: {str(e)}",
            session_id=session_id,
            exc_info=True,
        )


async def process_user_answer_and_respond(session_id: str):
    """
    Process user's complete answer and generate agent's next question.

    This is called when user finishes speaking (turn complete).
    """
    try:
        # Get session language from metadata
        session = session_manager.get_session(session_id)
        metadata = session.get("metadata", {}) if session else {}
        selected_lang = metadata.get("language", "Urdu")  # Default to Urdu

        # Map language selection to language codes
        if selected_lang.lower() == "english":
            language = "en"
            language_code = "en-US"
            stt_primary = "en-US"
            stt_fallback = "ur-PK"
        else:
            language = "ur"
            language_code = "ur-PK"
            stt_primary = "ur-PK"
            stt_fallback = "en-US"

        logger.info(
            f"Processing user answer in {selected_lang} ({language_code})",
            f"صارف کا جواب {selected_lang} ({language_code}) میں پروسیس ہو رہا ہے",
            session_id=session_id,
        )

        # Agent is thinking
        await manager.send_conversation_status(
            session_id,
            agent_state="thinking",
            can_speak=False,
            message_en="Processing your answer...",
            message_ur="آپ کے جواب پر کارروائی ہو رہی ہے...",
        )

        # Get user's audio for this turn - CRITICAL FIX: Concatenate all chunks
        user_audio_chunks = turn_audio_buffers.get(session_id, [])

        logger.info(
            f"[DEBUG] Processing user answer. Buffer has {len(user_audio_chunks)} chunks",
            f"[DEBUG] صارف کے جواب پر کارروائی۔ بفر میں {len(user_audio_chunks)} چنکس ہیں",
            session_id=session_id,
        )

        if not user_audio_chunks:
            logger.error(
                "[DEBUG] CRITICAL: Turn audio buffer is EMPTY!",
                "[DEBUG] CRITICAL: ٹرن آڈیو بفر خالی ہے!",
                session_id=session_id,
            )
            # Try to get from session manager as fallback
            user_audio = session_manager.get_all_audio(session_id)
            if not user_audio or len(user_audio) < 100:
                logger.error(
                    f"[DEBUG] Session audio also empty or too small: {len(user_audio) if user_audio else 0} bytes",
                    f"[DEBUG] سیشن آڈیو بھی خالی یا بہت چھوٹی: {len(user_audio) if user_audio else 0} بائٹس",
                    session_id=session_id,
                )
                await send_retry_message(session_id)
                return
        else:
            # Concatenate all chunks for this turn
            user_audio = b"".join(user_audio_chunks)
            logger.info(
                f"[DEBUG] Concatenated {len(user_audio_chunks)} chunks into {len(user_audio)} bytes for STT",
                f"[DEBUG] {len(user_audio_chunks)} چنکس کو {len(user_audio)} بائٹس میں جوڑا گیا",
                session_id=session_id,
            )

        # Log audio size before STT
        logger.info(
            f"[DEBUG] Sending {len(user_audio)} bytes to STT",
            f"[DEBUG] STT کو {len(user_audio)} بائٹس بھیجے جا رہے ہیں",
            session_id=session_id,
        )

        # Non-streaming recognition with language-specific config
        transcription = stt_service.recognize_audio(
            user_audio, primary_language=stt_primary, fallback_language=stt_fallback
        )

        # Log STT response
        logger.info(
            f"[DEBUG] STT response: {transcription}",
            f"[DEBUG] STT جواب: {transcription}",
            session_id=session_id,
        )

        if not transcription or not transcription.get("text"):
            logger.warning(
                "[DEBUG] STT returned empty or no transcription",
                "[DEBUG] STT نے خالی یا کوئی transcription واپس نہیں کیا",
                session_id=session_id,
                transcription_response=transcription,
                audio_bytes=len(user_audio),
            )
            await send_retry_message(session_id)
            return

        user_answer = transcription["text"]
        logger.info(
            "User answer transcribed",
            "صارف کا جواب transcribe کیا گیا",
            session_id=session_id,
            chars=len(user_answer),
            language=transcription.get("language"),
            confidence=transcription.get("confidence"),
            text_preview=user_answer[:100],
        )

        # Add to conversation history with correct language
        conversation_manager.add_user_response(session_id, user_answer, language)

        # Send transcript to client
        await manager.send_transcript(
            session_id,
            user_answer,
            is_final=True,
            language=transcription.get("language"),
            confidence=transcription.get("confidence"),
        )

        # Get next question from conversation manager (uses Gemini)
        next_response = await conversation_manager.get_next_question(
            session_id, user_answer
        )
        logger.info(
            "Next question generated",
            "اگلا سوال تیار کیا گیا",
            session_id=session_id,
            question_chars=len(next_response.next_question),
            interview_complete=next_response.interview_complete,
        )

        # Check if interview is complete
        if next_response.interview_complete:
            await handle_interview_end(session_id)
            return

        # Agent speaks next question
        await manager.send_conversation_status(
            session_id,
            agent_state="speaking",
            can_speak=False,
            message_en="Agent is speaking...",
            message_ur="ایجنٹ بول رہا ہے...",
        )

        await manager.send_agent_speech(
            session_id,
            text=next_response.next_question,
            language=next_response.language,
            sequence=conversation_manager._states[session_id].questions_asked,
        )
        logger.info(
            "Agent asked next question",
            "ایجنٹ نے اگلا سوال پوچھا",
            session_id=session_id,
            chars=len(next_response.next_question),
            language=next_response.language,
        )

        # Wait for TTS
        words = len(next_response.next_question.split())
        wait_time = max(2, words / 10)
        await asyncio.sleep(wait_time)

        # User's turn
        await manager.send_conversation_status(
            session_id,
            agent_state="listening",
            can_speak=True,
            message_en="You can speak now.",
            message_ur="اب آپ بول سکتے ہیں۔",
        )

        # Clear turn audio buffer for next turn
        turn_audio_buffers[session_id] = []
        logger.info(
            "[DEBUG] Cleared turn audio buffer for next turn",
            "[DEBUG] اگلے ٹرن کے لیے ٹرن آڈیو بفر صاف کیا گیا",
            session_id=session_id,
        )

    except Exception as e:
        logger.error(
            f"Error processing answer: {str(e)}",
            f"جواب پر کارروائی میں خرابی: {str(e)}",
            session_id=session_id,
            exc_info=True,
        )
        await send_retry_message(session_id)


async def send_retry_message(session_id: str):
    """Send error message and prompt user to try again."""
    # Speak the error to user (not just send text)
    error_msg_ur = "معذرت، میں آپ کی بات سمجھ نہیں سکا۔ براہ کرم دوبارہ بولیں۔"
    error_msg_en = "Sorry, I couldn't understand. Please try again."  # noqa: F841

    logger.info(
        "[DEBUG] Sending retry message to user",
        "[DEBUG] صارف کو دوبارہ کوشش کا پیغام بھیجا جا رہا ہے",
        session_id=session_id,
    )

    await manager.send_conversation_status(
        session_id,
        agent_state="speaking",
        can_speak=False,
        message_en="Agent is speaking...",
        message_ur="ایجنٹ بول رہا ہے...",
    )

    # Speak the error
    await manager.send_agent_speech(
        session_id,
        text=error_msg_ur,
        language="ur-PK",
        sequence=-1,  # Error message
    )

    # Wait for TTS
    await asyncio.sleep(3)

    # Clear audio buffer for retry
    turn_audio_buffers[session_id] = []

    # Return to listening state
    await manager.send_conversation_status(
        session_id,
        agent_state="listening",
        can_speak=True,
        message_en="You can speak now.",
        message_ur="اب آپ بول سکتے ہیں۔",
    )


async def handle_interview_end(session_id: str):
    """
    Complete the interview and provide final results.
    """
    try:
        # Import question bank to get closing statement
        from app.services.question_bank import get_question

        # Get session to read language preference from metadata
        session = session_manager.get_session(session_id)
        metadata = session.get("metadata", {}) if session else {}
        selected_lang = metadata.get("language", "Urdu")  # Default to Urdu

        # Map language selection to language codes
        if selected_lang.lower() == "english":
            language = "en"
            language_code = "en-US"
        else:
            language = "ur"
            language_code = "ur-PK"

        # Agent speaks closing statement
        await manager.send_conversation_status(
            session_id,
            agent_state="speaking",
            can_speak=False,
            message_en="Agent is speaking closing statement...",
            message_ur="ایجنٹ اختتامی بیان دے رہا ہے...",
        )

        # Get and speak closing statement in the correct language
        closing_statement = get_question("closure", language)
        await manager.send_agent_speech(
            session_id,
            text=closing_statement,
            language=language_code,
            sequence=999,  # Final sequence number
        )
        logger.info(
            "Agent spoke closing statement",
            "ایجنٹ نے اختتامی بیان دیا",
            session_id=session_id,
            chars=len(closing_statement),
        )

        # Wait for TTS to finish
        words = len(closing_statement.split())
        wait_time = max(3, words / 8)  # Slightly longer wait for closing
        await asyncio.sleep(wait_time)

        # Now show completion status
        await manager.send_conversation_status(
            session_id,
            agent_state="complete",
            can_speak=False,
            message_en="Interview complete. Preparing results...",
            message_ur="انٹرویو مکمل۔ نتائج تیار ہو رہے ہیں...",
        )

        # Get full transcript
        conversation_history = conversation_manager.get_conversation_history(session_id)
        user_responses = [
            turn.message for turn in conversation_history if turn.role == "user"
        ]
        full_transcript = " ".join(user_responses)

        # Analyze with Gemini
        session = session_manager.get_session(session_id) or {}
        preferred_language = session.get("metadata", {}).get("language")
        analysis = await gemini_service.analyze_transcript(
            full_transcript,
            session_id,
            preferred_language=preferred_language,
        )

        if analysis:
            session_manager.store_analysis(session_id, analysis)
            logger.info(
                "Final analysis ready",
                "حتمی تجزیہ تیار",
                session_id=session_id,
                legal_domain=analysis.legal_domain.value,
                urgency=analysis.urgency.value,
                confidence=analysis.confidence_score,
            )

        # Upload audio
        complete_audio = session_manager.get_all_audio(session_id)
        logger.info(
            "Preparing final conversational results",
            "حتمی بات چیت کے نتائج تیار کیے جا رہے ہیں",
            session_id=session_id,
            audio_bytes=len(complete_audio),
            transcript_chars=len(full_transcript),
        )
        audio_url = await storage_service.upload_audio(session_id, complete_audio)

        if audio_url:
            session = session_manager.get_session(session_id)
            if session:
                session["audio_url"] = audio_url

        # Mark complete
        session_manager.update_session_status(session_id, SessionStatus.COMPLETED)

        # Send results
        result_message = ResultsMessage(
            type=MessageType.RESULTS,
            session_id=session_id,
            transcript=full_transcript,
            analysis=analysis,
        )
        await manager.send_message(session_id, result_message.model_dump(mode="json"))

        # Send webhook
        session = session_manager.get_session(session_id)
        if session:
            webhook_payload = WebhookPayload(
                session_id=session_id,
                client_id=session.get("client_id"),
                transcript=full_transcript,
                analysis=analysis,
                audio_url=audio_url,
                audio_duration_seconds=session_manager.get_audio_duration(session_id),
                completed_at=session.get("completed_at"),
                metadata=session.get("metadata", {}),
            )

            asyncio.create_task(webhook_service.send_results(webhook_payload))

        logger.info(
            "Conversational interview completed",
            "بات چیت آمیز انٹرویو مکمل ہوا",
            session_id=session_id,
        )

    except Exception as e:
        logger.error(
            f"Error completing interview: {str(e)}",
            f"انٹرویو مکمل کرنے میں خرابی: {str(e)}",
            session_id=session_id,
        )
        session_manager.update_session_status(session_id, SessionStatus.FAILED)

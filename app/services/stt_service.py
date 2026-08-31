"""Speech-to-Text service using Gemini audio transcription.

Replaces faster-whisper (poor Urdu accuracy) and Google Cloud Speech.
Gemini 2.5 Flash transcribes Urdu/English mixed audio near-verbatim
(measured ~0.16 WER on legal Urdu vs 0.73 for whisper-small).

Public surface preserved:
    stt_service.recognize_audio(audio_bytes, primary_language, fallback_language)
        -> Optional[dict] with keys: text, confidence, language
"""

import logging
import subprocess
import tempfile
import os
from typing import Optional

from google import genai

from app.config import settings
from app.utils import get_logger
from app.utils.retry import gemini_call_with_retry

logger = get_logger(__name__)

TRANSCRIBE_PROMPT = (
    "Transcribe this audio verbatim into plain text. "
    "It is a legal intake interview and may mix Urdu and English. "
    "Write Urdu speech in URDU SCRIPT (اردو رسم الخط), NOT Devanagari. "
    "Write English speech in Latin script. "
    "Keep the original language(s); do NOT translate, summarize, or add punctuation. "
    "Preserve names, numbers, and case references exactly. "
    "Output ONLY the transcription."
)


class SpeechToTextService:
    """Gemini audio transcription service."""

    def __init__(self):
        self._client = None
        self.model = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

    def _get_client(self):
        """Lazy client init; fails loudly if no API key configured."""
        if self._client is None:
            if not settings.GEMINI_API_KEY:
                raise RuntimeError(
                    "Missing Gemini credentials: set GEMINI_API_KEY "
                    "(required for STT transcription)."
                )
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    @staticmethod
    def _to_mp3_16k(audio_bytes: bytes) -> Optional[str]:
        """Transcode browser audio (webm/opus etc.) to 16kHz mono mp3.

        Uses ffmpeg (present in the Docker image). MP3 keeps payloads small
        (Gemini inline-data limit is 20MB) and is a supported inline mime.
        Returns a temp file path.
        """
        tmp_in = tmp_out = None
        ok = False
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
                fin.write(audio_bytes)
                tmp_in = fin.name
            tmp_out = tmp_in.replace(".webm", ".mp3")

            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", tmp_in,
                    "-ar", "16000", "-ac", "1",
                    "-c:a", "libmp3lame", "-b:a", "64k",
                    tmp_out,
                ],
                capture_output=True,
                timeout=90,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg error: {result.stderr.decode()[:300]}")
                return None
            ok = True
            return tmp_out
        except FileNotFoundError:
            logger.error("ffmpeg not found. Please install ffmpeg.")
            return None
        except Exception as e:
            logger.error(f"Audio conversion error: {str(e)}")
            return None
        finally:
            # Never leak temp files on failure: the caller only sees a path
            # when conversion succeeded (and cleans it up itself).
            if not ok:
                for p in (tmp_in, tmp_out):
                    if p:
                        try:
                            os.remove(p)
                        except OSError:
                            pass

    def recognize_audio(
        self,
        audio_bytes: bytes,
        primary_language: str = "ur-PK",
        fallback_language: str = "en-US",
    ) -> Optional[dict]:
        """Transcribe complete audio bytes (webm/wav/mp3/...) via Gemini.

        Args:
            audio_bytes: Raw audio as produced by the browser MediaRecorder.
            primary_language: Preferred language code (ur-PK, en-US).
            fallback_language: Secondary language code.

        Returns:
            {"text": str, "confidence": Optional[float], "language": str} or None.
        """
        try:
            logger.info(
                f"STT received audio: {len(audio_bytes)} bytes, primary: {primary_language}",
                f"STT کو آڈیو ملی: {len(audio_bytes)} بائٹس",
            )
            if not audio_bytes or len(audio_bytes) < 100:
                logger.warning(
                    f"Audio data too small: {len(audio_bytes) if audio_bytes else 0} bytes"
                )
                return None

            mp3_path = self._to_mp3_16k(audio_bytes)
            if not mp3_path:
                return None

            try:
                with open(mp3_path, "rb") as f:
                    audio_data = f.read()

                client = self._get_client()
                # Language guidance in the transcription prompt (params were
                # previously gathered but never used).
                prompt = f"{TRANSCRIBE_PROMPT}\nAudio language: {primary_language}; fallback: {fallback_language}."
                # Bounded retry on 429/5xx (daily caps still surface as errors).
                response = gemini_call_with_retry(
                    lambda: client.models.generate_content(
                        model=self.model,
                        contents=genai.types.Content(
                            parts=[
                                genai.types.Part(
                                    inline_data=genai.types.Blob(
                                        mime_type="audio/mpeg", data=audio_data
                                    )
                                ),
                                genai.types.Part(text=prompt),
                            ]
                        ),
                    )
                )

                text = (response.text or "").strip()
                if not text:
                    logger.warning(
                        "Gemini STT returned empty transcription",
                        "Gemini STT نے خالی ٹرانسکرپشن واپس کی",
                        primary_language=primary_language,
                    )
                    return None

                logger.info(
                    f"STT recognized: '{text[:50]}...'",
                    f"STT نے پہچانا: '{text[:50]}...'",
                )
                # Gemini offers no numeric confidence; language defaults to the
                # interview's primary language (bilingual output is preserved
                # by the model itself).
                return {
                    "text": text,
                    "confidence": None,
                    "language": primary_language.split("-")[0],  # "ur-pk" -> "ur"
                }

            finally:
                for p in (mp3_path, mp3_path.replace(".mp3", ".webm")):
                    if os.path.exists(p):
                        os.remove(p)

        except Exception as e:
            logger.error(
                f"STT recognition error: {str(e)}",
                f"STT پہچان میں خرابی: {str(e)}",
            )
            return None


# Global STT service instance
stt_service = SpeechToTextService()
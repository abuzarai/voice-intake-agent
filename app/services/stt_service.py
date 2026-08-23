"""Local Speech-to-Text service using faster-whisper (CTranslate2, CPU).

Replaces Google Cloud Speech-to-Text. Public surface preserved:
    stt_service.recognize_audio(audio_bytes, primary_language, fallback_language)
        -> Optional[dict] with keys: text, confidence, language
"""

import asyncio
import math
import os
import tempfile
from typing import Optional

from app.config import settings
from app.utils import get_logger

logger = get_logger(__name__)

# Map the language codes used across the app (BCP-47 style) to whisper codes.
_WHISPER_LANG = {
    "ur": "ur",
    "ur-pk": "ur",
    "ur-in": "ur",
    "en": "en",
    "en-us": "en",
}


def _to_whisper_lang(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return _WHISPER_LANG.get(code.strip().lower())


class SpeechToTextService:
    """Faster-whisper transcription service with lazy model loading."""

    def __init__(self):
        self._model = None
        self.model_size = settings.STT_MODEL_SIZE
        self.compute_type = settings.STT_COMPUTE_TYPE

    def _get_model(self):
        """Lazy-load the whisper model on first use (keeps startup fast)."""
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                f"Loading faster-whisper model '{self.model_size}' ({self.compute_type})...",
                f"faster-whisper ماڈل '{self.model_size}' لوڈ ہو رہا ہے...",
            )
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type=self.compute_type,
            )
            logger.info("Whisper model loaded", "ماڈل لوڈ ہو گیا")
        return self._model

    @staticmethod
    def _to_wav_16k(audio_bytes: bytes) -> Optional[str]:
        """Transcode arbitrary browser audio (webm/opus etc.) to 16kHz mono WAV.

        Uses ffmpeg (present in the Docker image). Returns a temp file path.
        """
        import subprocess

        tmp_in = tmp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
                fin.write(audio_bytes)
                tmp_in = fin.name
            tmp_out = tmp_in.replace(".webm", ".wav")

            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", tmp_in,
                    "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    tmp_out,
                ],
                capture_output=True,
                timeout=60,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg error: {result.stderr.decode()[:300]}")
                return None
            return tmp_out
        except FileNotFoundError:
            logger.error("ffmpeg not found. Please install ffmpeg.")
            return None
        except Exception as e:
            logger.error(f"Audio conversion error: {str(e)}")
            return None

    def _transcribe_sync(
        self,
        wav_path: str,
        primary_language: Optional[str],
        fallback_language: Optional[str],
    ) -> Optional[dict]:
        """Blocking transcription; executed in a worker thread."""
        model = self._get_model()

        # Prefer the caller's primary language when we understand it;
        # otherwise let whisper auto-detect (handles bilingual turns).
        lang = _to_whisper_lang(primary_language) or _to_whisper_lang(fallback_language)

        segments_iter, info = model.transcribe(
            wav_path,
            language=lang,          # None -> auto-detect
            beam_size=5,
            vad_filter=True,        # skip silence; fewer hallucinations
        )

        parts: list[str] = []
        logprobs: list[float] = []
        for seg in segments_iter:
            if seg.text and seg.text.strip():
                parts.append(seg.text.strip())
                logprobs.append(seg.avg_logprob)

        text = " ".join(parts).strip()
        if not text:
            logger.warning(
                "No speech recognized in audio",
                "آڈیو میں کوئی تقریر نہیں ملی",
                primary_language=primary_language,
            )
            return None

        # exp(avg_logprob) maps [-inf, 0] -> (0, 1]; a reasonable confidence proxy.
        confidence = (
            round(min(1.0, max(0.0, math.exp(sum(logprobs) / len(logprobs)))), 2)
            if logprobs
            else 0.0
        )
        detected = info.language or lang or "ur"

        logger.info(
            f"STT recognized ({detected}, conf={confidence}): '{text[:50]}...'",
            f"STT نے پہچانا ({detected}): '{text[:50]}...'",
        )
        return {
            "text": text,
            "confidence": confidence,
            "language": detected,
        }

    def recognize_audio(
        self,
        audio_bytes: bytes,
        primary_language: str = "ur-PK",
        fallback_language: str = "en-US",
    ) -> Optional[dict]:
        """Recognize speech from complete audio bytes (webm/wav/mp3/...).

        Args:
            audio_bytes: Raw audio as produced by the browser MediaRecorder.
            primary_language: Preferred language code (e.g. ur-PK, en-US).
            fallback_language: Secondary language code.

        Returns:
            {"text": str, "confidence": float, "language": str} or None.
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

            wav_path = self._to_wav_16k(audio_bytes)
            if not wav_path:
                return None

            try:
                # Blocking transcription (same behavior as before: handlers call this
                # synchronously; offloading it from the event loop is a separate task).
                return self._transcribe_sync(wav_path, primary_language, fallback_language)
            finally:
                if os.path.exists(wav_path):
                    os.remove(wav_path)

        except Exception as e:
            logger.error(f"STT recognition error: {str(e)}")
            return None


# Global STT service instance
stt_service = SpeechToTextService()

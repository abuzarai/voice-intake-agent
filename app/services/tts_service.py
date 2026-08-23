"""Text-to-Speech service using edge-tts (Microsoft neural voices, free).

Replaces Google Cloud Text-to-Speech. Public surface preserved:
    tts_service.synthesize_speech(text, language, speaking_rate) -> bytes (mp3)
    tts_service.synthesize_to_base64(text, language) -> str
"""

import asyncio
import base64
from typing import Dict

import edge_tts
from app.utils import get_logger

logger = get_logger(__name__)


class TTSService:
    """Text-to-Speech service using Microsoft Edge neural voices."""

    # Voice configurations (edge-tts names).
    # Urdu: native Pakistani voices exist (ur-PK). Previously we used the
    # female ur-IN-Wavenet-A, so Uzma (female, PK) stays closest to old behavior.
    voices: Dict[str, dict] = {
        "ur-IN": {"name": "ur-PK-UzmaNeural", "gender": "Female"},
        "ur-PK": {"name": "ur-PK-UzmaNeural", "gender": "Female"},
        "ur": {"name": "ur-PK-UzmaNeural", "gender": "Female"},
        "en-US": {"name": "en-US-AndrewNeural", "gender": "Male"},
        "en": {"name": "en-US-AndrewNeural", "gender": "Male"},
    }

    def _resolve_voice(self, language: str) -> str:
        cfg = self.voices.get(language) or self.voices.get("ur-PK")
        return cfg["name"]

    async def synthesize_speech(
        self,
        text: str,
        language: str = "ur-PK",
        speaking_rate: float = 1.0,
    ) -> bytes:
        """Convert text to speech audio.

        Args:
            text: Text to synthesize
            language: Language code (ur-PK or en-US)
            speaking_rate: Speed (0.5-2.0, default 1.0)

        Returns:
            Audio bytes (mp3)
        """
        try:
            voice = self._resolve_voice(language)

            # edge-tts rate format: percentage string like "+10%" / "-5%"
            pct = round((speaking_rate - 1.0) * 100)
            rate_str = f"{pct:+d}%"

            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate_str)

            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])

            audio = b"".join(chunks)
            if not audio:
                raise RuntimeError(f"edge-tts returned no audio for voice={voice}")

            logger.info(
                f"Synthesized speech: {len(text)} chars",
                f"آواز بنائی: {len(text)} حروف",
                language=language,
                voice=voice,
                audio_size=len(audio),
            )
            return audio

        except Exception as e:
            logger.error(
                f"TTS synthesis error: {str(e)}",
                f"TTS خرابی: {str(e)}",
                text=text[:50],
            )
            raise

    async def synthesize_to_base64(
        self,
        text: str,
        language: str = "ur-PK",
    ) -> str:
        """Synthesize speech and encode to base64 for WebSocket."""
        audio_bytes = await self.synthesize_speech(text, language)
        return base64.b64encode(audio_bytes).decode("utf-8")


# Global TTS service instance
tts_service = TTSService()

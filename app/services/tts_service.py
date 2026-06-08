"""Google Cloud Text-to-Speech service."""

import base64
from google.cloud import texttospeech
from app.utils import get_logger

logger = get_logger(__name__)


class TTSService:
    """Text-to-Speech service using Google Cloud."""
    
    def __init__(self):
        """Initialize TTS client."""
        self.client = texttospeech.TextToSpeechClient()
        
        # Voice configurations
        # NOTE: Google TTS has Urdu voices as ur-IN (India), not ur-PK (Pakistan)
        self.voices = {
            "ur-IN": {
                "language_code": "ur-IN",
                "name": "ur-IN-Wavenet-A",  # Female voice
                "ssml_gender": texttospeech.SsmlVoiceGender.FEMALE
            },
            "ur-PK": {  # Fallback - use ur-IN voices for Pakistani Urdu
                "language_code": "ur-IN",
                "name": "ur-IN-Wavenet-A",
                "ssml_gender": texttospeech.SsmlVoiceGender.FEMALE
            },
            "ur": {  # Generic Urdu fallback
                "language_code": "ur-IN",
                "name": "ur-IN-Wavenet-A",
                "ssml_gender": texttospeech.SsmlVoiceGender.FEMALE
            },
            "en-US": {
                "language_code": "en-US",
                "name": "en-US-Wavenet-D",  # Male voice
                "ssml_gender": texttospeech.SsmlVoiceGender.MALE
            },
            "en": {  # Generic English fallback
                "language_code": "en-US",
                "name": "en-US-Wavenet-D",
                "ssml_gender": texttospeech.SsmlVoiceGender.MALE
            }
        }
    
    async def synthesize_speech(
        self,
        text: str,
        language: str = "ur-PK",
        speaking_rate: float = 1.0
    ) -> bytes:
        """
        Convert text to speech audio.
        
        Args:
            text: Text to synthesize
            language: Language code (ur-PK or en-US)
            speaking_rate: Speed (0.5-2.0, default 1.0)
            
        Returns:
            Audio bytes (LINEAR16, 16kHz, mono)
        """
        try:
            # Get voice configuration
            voice_config = self.voices.get(language, self.voices["ur-PK"])
            
            # Set up synthesis input
            synthesis_input = texttospeech.SynthesisInput(text=text)
            
            # Configure voice
            voice = texttospeech.VoiceSelectionParams(
                language_code=voice_config["language_code"],
                name=voice_config["name"],
                ssml_gender=voice_config["ssml_gender"]
            )
            
            # Configure audio
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=speaking_rate,
                pitch=0.0,
                volume_gain_db=0.0
            )

            
            # Perform synthesis (run in thread pool since it's a blocking call)
            import asyncio
            response = await asyncio.to_thread(
                self.client.synthesize_speech,
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )
            
            logger.info(
                f"Synthesized speech: {len(text)} chars",
                f"آواز بنائی: {len(text)} حروف",
                language=language,
                audio_size=len(response.audio_content)
            )
            
            return response.audio_content
            
        except Exception as e:
            logger.error(
                f"TTS synthesis error: {str(e)}",
                f"TTS خرابی: {str(e)}",
                text=text[:50]
            )
            raise
    
    async def synthesize_to_base64(
        self,
        text: str,
        language: str = "ur-PK"
    ) -> str:
        """
        Synthesize speech and encode to base64 for WebSocket.
        
        Args:
            text: Text to synthesize
            language: Language code
            
        Returns:
            Base64 encoded audio
        """
        audio_bytes = await self.synthesize_speech(text, language)
        return base64.b64encode(audio_bytes).decode('utf-8')


# Global TTS service instance
tts_service = TTSService()

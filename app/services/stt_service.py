"""Google Cloud Speech-to-Text integration service."""

from typing import AsyncGenerator, Optional
from google.cloud import speech
from google.api_core import exceptions as gcp_exceptions
from app.utils import get_logger

logger = get_logger(__name__)


class SpeechToTextService:
    """Google Cloud Speech-to-Text streaming service."""
    
    def __init__(self):
        self.client = speech.SpeechClient()
        self.config = self._create_config()
    
    def _create_config(self) -> speech.RecognitionConfig:
        """Create STT configuration for bilingual recognition.
        
        Returns:
            Recognition configuration
        """
        # Use WEBM_OPUS to match browser MediaRecorder output (audio/webm; codecs=opus)
        # IMPORTANT: sample_rate_hertz is REQUIRED for WEBM_OPUS encoding
        # Browser MediaRecorder defaults to 48kHz for Opus codec
        # NOTE: Do NOT use 'latest_long' or 'use_enhanced' - they are NOT supported for ur-PK
        return speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,  # Required for WEBM_OPUS - browser default
            language_code='ur-PK',  # Primary: Urdu (Pakistan)
            alternative_language_codes=['en-US'],  # Fallback: English
            enable_automatic_punctuation=True,
            # Using default model - 'latest_long' and 'use_enhanced' are NOT supported for Urdu
        )
    
    async def streaming_recognize(
        self, 
        audio_generator: AsyncGenerator[bytes, None],
        session_id: str
    ) -> AsyncGenerator[dict, None]:
        """
        Minimal streaming wrapper: buffers incoming chunks, then sends one
        streaming request to GCP STT and yields interim/final results.
        This keeps the API shape async generator -> async generator, so we can
        drop it in without changing callers.
        """
        streaming_config = speech.StreamingRecognitionConfig(
            config=self.config,
            interim_results=True
        )

        # Collect chunks from the async generator
        buffered: list[bytes] = []
        async for chunk in audio_generator:
            if chunk:
                buffered.append(chunk)

        if not buffered:
            return

        def request_iter():
            # send config first
            yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
            for chunk in buffered:
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        try:
            logger.info(
                "Starting buffered streaming STT",
                "Buffered streaming STT شروع",
                session_id=session_id,
                chunks=len(buffered),
                total_bytes=sum(len(c) for c in buffered)
            )

            responses = self.client.streaming_recognize(requests=request_iter())
            for response in responses:
                for result in response.results:
                    alt = result.alternatives[0]
                    yield {
                        "text": alt.transcript,
                        "is_final": result.is_final,
                        "language": getattr(result, "language_code", "ur-PK"),
                        "confidence": alt.confidence
                    }
        except gcp_exceptions.GoogleAPIError as e:
            logger.error(
                f"GCP STT API error: {str(e)}",
                f"GCP STT API خرابی: {str(e)}",
                session_id=session_id
            )
            yield {"text": "", "is_final": False, "error": str(e)}
        except Exception as e:
            logger.error(
                f"STT streaming error: {str(e)}",
                f"STT سٹریمنگ خرابی: {str(e)}",
                session_id=session_id
            )
            yield {"text": "", "is_final": False, "error": str(e)}
    
    def recognize_audio(self, audio_bytes: bytes, primary_language: str = "ur-PK", fallback_language: str = "en-US") -> Optional[dict]:
        """Recognize audio from complete audio bytes (non-streaming).
        
        Args:
            audio_bytes: Audio bytes (can be webm, wav, or PCM)
            primary_language: Primary language code for recognition (default: ur-PK)
            fallback_language: Fallback language code (default: en-US)
            
        Returns:
            Recognition result dictionary
        """
        try:
            logger.info(
                f"STT received audio: {len(audio_bytes)} bytes, primary: {primary_language}",
                f"STT کو آڈیو ملی: {len(audio_bytes)} بائٹس، بنیادی: {primary_language}"
            )
            
            # Check if we have any audio data
            if not audio_bytes or len(audio_bytes) < 100:
                logger.warning(f"Audio data too small: {len(audio_bytes) if audio_bytes else 0} bytes")
                return None
            
            # Create config with specified language
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
                sample_rate_hertz=48000,
                language_code=primary_language,
                alternative_language_codes=[fallback_language],
                enable_automatic_punctuation=True,
            )
            
            # Send raw WEBM_OPUS audio directly (matches MediaRecorder output)
            audio = speech.RecognitionAudio(content=audio_bytes)
            
            response = self.client.recognize(
                config=config,
                audio=audio
            )
            
            if not response.results:
                logger.warning(
                    "No speech recognized in audio",
                    audio_bytes=len(audio_bytes),
                    primary_language=primary_language,
                    fallback_language=fallback_language
                )
                return None
            
            # Get the best result
            result = response.results[0]
            alternative = result.alternatives[0]
            
            logger.info(
                f"STT recognized: '{alternative.transcript[:50]}...' (confidence: {alternative.confidence:.2f})",
                f"STT نے پہچانا: '{alternative.transcript[:50]}...'",
            )
            
            return {
                "text": alternative.transcript,
                "confidence": alternative.confidence,
                "language": result.language_code if hasattr(result, 'language_code') else 'ur-PK'
            }
            
        except gcp_exceptions.GoogleAPIError as e:
            logger.error(
                f"GCP STT recognition error: {str(e)}",
                f"GCP STT پہچان میں خرابی: {str(e)}"
            )
            return None
        except Exception as e:
            logger.error(
                f"STT recognition error: {str(e)}",
                f"STT پہچان میں خرابی: {str(e)}"
            )
            return None
    
    def _convert_audio_to_linear16(self, audio_bytes: bytes) -> Optional[bytes]:
        """Convert audio bytes to LINEAR16 PCM format required by Google STT.
        
        Uses ffmpeg subprocess for Python 3.13 compatibility.
        
        Args:
            audio_bytes: Raw audio bytes from browser (webm format)
            
        Returns:
            LINEAR16 PCM audio bytes at 16kHz mono
        """
        import subprocess
        import tempfile
        import os
        
        try:
            # Write input to temp file
            with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
                f.write(audio_bytes)
                input_path = f.name
            
            output_path = input_path.replace('.webm', '.raw')
            
            try:
                # Use ffmpeg to convert to LINEAR16 PCM
                result = subprocess.run([
                    'ffmpeg', '-y',
                    '-i', input_path,
                    '-ar', '16000',      # 16kHz sample rate
                    '-ac', '1',          # Mono
                    '-f', 's16le',       # 16-bit little-endian PCM
                    output_path
                ], capture_output=True, timeout=30)
                
                if result.returncode != 0:
                    logger.error(f"ffmpeg error: {result.stderr.decode()}")
                    return None
                
                # Read converted audio
                with open(output_path, 'rb') as f:
                    audio_data = f.read()
                
                logger.info(
                    f"Converted audio: {len(audio_bytes)} bytes -> {len(audio_data)} bytes LINEAR16",
                    f"آڈیو تبدیل کیا: {len(audio_bytes)} -> {len(audio_data)} بائٹس"
                )
                
                return audio_data
                
            finally:
                # Cleanup temp files
                if os.path.exists(input_path):
                    os.remove(input_path)
                if os.path.exists(output_path):
                    os.remove(output_path)
                    
        except FileNotFoundError:
            logger.error(
                "ffmpeg not found. Please install ffmpeg.",
                "ffmpeg نہیں ملا۔ براہ کرم ffmpeg انسٹال کریں۔"
            )
            return None
        except Exception as e:
            logger.error(
                f"Audio conversion error: {str(e)}",
                f"آڈیو تبدیلی میں خرابی: {str(e)}"
            )
            return None


# Global STT service instance
stt_service = SpeechToTextService()

"""A user-side TTS that only declares the audio rate.

Every scripted turn names a recording, so this service never synthesizes
anything. It exists because the harness derives the user audio rate from the
speech config, and a zero rate makes its send loop emit empty frames.
"""

from collections.abc import AsyncGenerator

from pipecat.frames.frames import Frame
from pipecat.services.tts_service import TTSService


class SilentUserTTS(TTSService):
    """Never speaks; carries the sample rate the harness needs."""

    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        """Refuse to synthesize: the corpus is recordings, not text."""
        raise NotImplementedError("recorded turns are played, never synthesized")
        yield

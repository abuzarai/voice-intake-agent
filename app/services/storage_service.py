"""Local filesystem storage service for interview audio.

Replaces Google Cloud Storage. Public surface preserved:
    await storage_service.upload_audio(session_id, audio_bytes) -> Optional[str]

Audio is written under settings.audio_dir with a simple time-based cleanup
replacing the old GCS lifecycle rule.
"""

import asyncio
import os
import time
from typing import Optional

from app.config import settings
from app.utils import get_logger

logger = get_logger(__name__)


class StorageService:
    """Local filesystem storage for interview audio files."""

    def __init__(self):
        self.base_dir = settings.audio_dir
        self._ensure_dir_exists()

    def _ensure_dir_exists(self):
        try:
            os.makedirs(os.path.join(self.base_dir, "interviews"), exist_ok=True)
            logger.info(
                f"Audio storage ready at {self.base_dir}",
                f"آڈیو اسٹوریج تیار ہے: {self.base_dir}",
                directory=self.base_dir,
            )
        except OSError as e:
            logger.error(
                f"Failed to create audio directory: {str(e)}",
                f"آڈیو ڈائریکٹری بنانے میں ناکامی: {str(e)}",
            )

    async def upload_audio(self, session_id: str, audio_bytes: bytes) -> Optional[str]:
        """Store interview audio on local disk.

        Args:
            session_id: Session identifier
            audio_bytes: Complete audio as bytes

        Returns:
            Path of the stored file, or None on error.
        """
        try:
            target_dir = os.path.join(self.base_dir, "interviews")
            os.makedirs(target_dir, exist_ok=True)

            file_path = os.path.join(target_dir, f"{session_id}.wav")

            def _write():
                with open(file_path, "wb") as f:
                    f.write(audio_bytes)

            await asyncio.to_thread(_write)

            logger.info(
                f"Stored audio for session {session_id}",
                f"سیشن {session_id} کی آڈیو محفوظ کی گئی",
                session_id=session_id,
                size_bytes=len(audio_bytes),
                path=file_path,
            )
            return file_path

        except OSError as e:
            logger.error(
                f"Audio storage error: {str(e)}",
                f"آڈیو محفوظ کرنے میں خرابی: {str(e)}",
                session_id=session_id,
            )
            return None
        except Exception as e:
            logger.error(
                f"Audio upload error: {str(e)}",
                f"آڈیو اپلوڈ میں خرابی: {str(e)}",
                session_id=session_id,
            )
            return None

    def cleanup_older_than(self, days: int = None) -> int:
        """Delete stored audio older than `days` (default: AUDIO_RETENTION_DAYS).

        Replaces the GCS bucket lifecycle rule. Call from cron/housekeeping.
        Returns the number of files removed.
        """
        days = days if days is not None else settings.AUDIO_RETENTION_DAYS
        cutoff = time.time() - days * 86400
        removed = 0

        for root, _dirs, files in os.walk(self.base_dir):
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError:
                    continue

        if removed:
            logger.info(
                f"Cleaned {removed} audio files older than {days} days",
                f"{days} دن سے پرانی {removed} آڈیو فائلیں ہٹا دیں",
            )
        return removed


# Global storage service instance
storage_service = StorageService()

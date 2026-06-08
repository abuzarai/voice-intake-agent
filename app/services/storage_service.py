"""Google Cloud Storage service for audio file management."""

import asyncio
from datetime import timedelta
from typing import Optional
from google.cloud import storage
from google.api_core import exceptions as gcp_exceptions
from app.config import settings
from app.utils import get_logger

logger = get_logger(__name__)


class StorageService:
    """Google Cloud Storage service for audio files."""
    
    def __init__(self):
        """Initialize GCS client."""
        try:
            self.client = storage.Client(project=settings.GCP_PROJECT_ID)
            self.bucket_name = settings.storage_bucket_name
            self._ensure_bucket_exists()
        except Exception as e:
            logger.error(
                f"Failed to initialize GCS client: {str(e)}",
                f"GCS کلائنٹ شروع کرنے میں ناکامی: {str(e)}"
            )
            self.client = None
    
    def _ensure_bucket_exists(self):
        """Create bucket if it doesn't exist and set lifecycle policy."""
        if not self.client:
            return
        
        try:
            bucket = self.client.bucket(self.bucket_name)
            
            if not bucket.exists():
                # Create bucket
                bucket = self.client.create_bucket(
                    self.bucket_name,
                    location="US"  # Change to your preferred region
                )
                logger.info(
                    f"Created GCS bucket: {self.bucket_name}",
                    f"GCS بکٹ بنایا گیا: {self.bucket_name}"
                )
                
                # Set lifecycle policy for auto-deletion
                bucket.add_lifecycle_delete_rule(
                    age=settings.AUDIO_RETENTION_DAYS
                )
                bucket.patch()
                
                logger.info(
                    f"Set {settings.AUDIO_RETENTION_DAYS}-day retention policy",
                    f"{settings.AUDIO_RETENTION_DAYS} دن کی retention پالیسی سیٹ کی"
                )
        
        except gcp_exceptions.GoogleAPIError as e:
            logger.error(
                f"Error creating/configuring bucket: {str(e)}",
                f"بکٹ بنانے میں خرابی: {str(e)}"
            )
    
    async def upload_audio(self, session_id: str, audio_bytes: bytes) -> Optional[str]:
        """Upload audio file to Cloud Storage.
        
        Args:
            session_id: Session identifier
            audio_bytes: Complete audio as bytes
            
        Returns:
            Public URL of uploaded file or None on error
        """
        if not self.client:
            logger.error(
                "GCS client not initialized",
                "GCS کلائنٹ شروع نہیں ہوا"
            )
            return None
        
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob_name = f"interviews/{session_id}.wav"
            blob = bucket.blob(blob_name)
            
            # Upload with metadata
            blob.metadata = {
                "session_id": session_id,
                "content_type": "audio/wav"
            }
            
            await asyncio.to_thread(
                blob.upload_from_string,
                audio_bytes,
                content_type="audio/wav"
            )
            
            logger.info(
                f"Uploaded audio for session {session_id}",
                f"سیشن {session_id} کی آڈیو اپلوڈ کی گئی",
                session_id=session_id,
                size_bytes=len(audio_bytes)
            )
            
            # Generate signed URL (valid for 7 days)
            url = blob.generate_signed_url(
                expiration=timedelta(days=settings.AUDIO_RETENTION_DAYS),
                method="GET"
            )
            
            return url
            
        except gcp_exceptions.GoogleAPIError as e:
            logger.error(
                f"GCS upload error: {str(e)}",
                f"GCS اپلوڈ میں خرابی: {str(e)}",
                session_id=session_id
            )
            return None
        except Exception as e:
            logger.error(
                f"Audio upload error: {str(e)}",
                f"آڈیو اپلوڈ میں خرابی: {str(e)}",
                session_id=session_id
            )
            return None


# Global storage service instance
storage_service = StorageService()

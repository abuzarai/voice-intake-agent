"""Webhook service for sending results to Express backend."""

import asyncio
from typing import Optional
import httpx
from app.config import settings
from app.models import WebhookPayload
from app.utils import get_logger

logger = get_logger(__name__)


class WebhookService:
    """Service for sending interview results to Express backend."""
    
    def __init__(self):
        self.url = settings.EXPRESS_WEBHOOK_URL
        self.secret = settings.EXPRESS_WEBHOOK_SECRET
        self.max_retries = 3
        self.retry_delay = 2.0  # seconds
    
    async def send_results(self, payload: WebhookPayload) -> bool:
        """Send interview results to Express backend webhook.
        
        Args:
            payload: Webhook payload with results
            
        Returns:
            True if successful, False otherwise
        """
        if not self.url:
            logger.warning(
                "Webhook URL not configured, skipping webhook",
                "Webhook URL ترتیب نہیں دیا گیا",
                session_id=payload.session_id
            )
            return False
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "VoiceInterviewAgent/0.1"
        }
        
        # Add signature header if secret is configured
        if self.secret:
            headers["X-Webhook-Secret"] = self.secret
        
        # Convert Pydantic model to dict
        data = payload.model_dump(mode='json')
        
        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        self.url,
                        json=data,
                        headers=headers
                    )
                    
                    if response.status_code == 200:
                        logger.info(
                            f"Webhook sent successfully (attempt {attempt})",
                            f"Webhook کامیابی سے بھیجا گیا (کوشش {attempt})",
                            session_id=payload.session_id
                        )
                        return True
                    else:
                        logger.warning(
                            f"Webhook returned status {response.status_code} (attempt {attempt})",
                            f"Webhook نے status {response.status_code} واپس دیا (کوشش {attempt})",
                            session_id=payload.session_id
                        )
                        
            except httpx.TimeoutException:
                logger.warning(
                    f"Webhook timeout (attempt {attempt})",
                    f"Webhook ٹائم آؤٹ (کوشش {attempt})",
                    session_id=payload.session_id
                )
            except httpx.RequestError as e:
                logger.warning(
                    f"Webhook request error: {str(e)} (attempt {attempt})",
                    f"Webhook درخواست میں خرابی: {str(e)} (کوشش {attempt})",
                    session_id=payload.session_id
                )
            except Exception as e:
                logger.error(
                    f"Unexpected webhook error: {str(e)} (attempt {attempt})",
                    f"غیر متوقع webhook خرابی: {str(e)} (کوشش {attempt})",
                    session_id=payload.session_id
                )
            
            # Wait before retrying (exponential backoff)
            if attempt < self.max_retries:
                wait_time = self.retry_delay * (2 ** (attempt - 1))
                await asyncio.sleep(wait_time)
        
        logger.error(
            f"Webhook failed after {self.max_retries} attempts",
            f"{self.max_retries} کوششوں کے بعد webhook ناکام",
            session_id=payload.session_id
        )
        return False


# Global webhook service instance
webhook_service = WebhookService()

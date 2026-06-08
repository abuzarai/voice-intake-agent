"""REST API endpoints for session management."""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, status
from app.models import (
    SessionCreate,
    SessionResponse,
    InterviewResult,
)
from app.services.session_service import session_manager
from app.utils import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["sessions"])


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, session_req: SessionCreate):
    """Create a new interview session.
    
    Returns WebSocket URL for audio streaming.
    """
    try:
        logger.info(
            "Creating new session",
            "نیا سیشن بنایا جا رہا ہے",
            client_id=session_req.client_id,
            metadata_present=bool(session_req.metadata)
        )

        session_data = session_manager.create_session(
            client_id=session_req.client_id,
            metadata=session_req.metadata
        )
        
        # Construct WebSocket URL
        base = str(request.base_url).rstrip("/")
        ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_base}/api/v1/ws/{session_data['session_id']}"
        
        return SessionResponse(
            session_id=session_data["session_id"],
            ws_url=ws_url,
            created_at=session_data["created_at"],
            expires_at=session_data["expires_at"],
            status=session_data["status"]
        )
        
        logger.info(
            "Session created successfully",
            "سیشن کامیابی سے بنایا گیا",
            session_id=session_data["session_id"],
            ws_url=ws_url,
            expires_at=session_data["expires_at"].isoformat()
        )
        
    except Exception as e:
        logger.error(
            f"Failed to create session: {str(e)}",
            f"سیشن بنانے میں ناکامی: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message_en": "Failed to create interview session",
                "message_ur": "انٹرویو سیشن بنانے میں ناکامی"
            }
        )


@router.get("/sessions/{session_id}", response_model=InterviewResult)
async def get_session(session_id: str):
    """Get interview session results.
    
    Returns complete transcript and analysis if available.
    """
    try:
        logger.info(
            "Fetching session result",
            "سیشن کے نتائج حاصل کیے جا رہے ہیں",
            session_id=session_id
        )

        result = session_manager.get_result(session_id)
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "message_en": "Session not found",
                    "message_ur": "سیشن نہیں ملا"
                }
            )
        
        logger.info(
            "Session result retrieved",
            "سیشن کے نتائج حاصل ہو گئے",
            session_id=session_id,
            status=str(result.status)
        )

        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get session: {str(e)}",
            f"سیشن حاصل کرنے میں ناکامی: {str(e)}",
            session_id=session_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message_en": "Failed to retrieve session",
                "message_ur": "سیشن حاصل کرنے میں ناکامی"
            }
        )


@router.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {
        "status": "healthy",
        "service": "voice-interview-agent",
        "version": "0.1.0",
        "timestamp": datetime.utcnow().isoformat()
    }

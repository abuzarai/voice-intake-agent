"""Session management service for interview sessions."""

import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional
from app.config import settings
from app.models import SessionStatus, InterviewResult, LegalAnalysis
from app.utils import get_logger

logger = get_logger(__name__)


class SessionManager:
    """Manages interview session lifecycle and state."""
    
    def __init__(self):
        # In-memory session store (for MVP - use Redis/DB in production)
        self._sessions: Dict[str, Dict] = {}
        self._transcripts: Dict[str, list] = {}
        self._audio_chunks: Dict[str, list] = {}
    
    def create_session(self, client_id: Optional[str] = None, 
                      metadata: Optional[Dict] = None) -> Dict:
        """Create a new interview session.
        
        Args:
            client_id: Optional client ID from external system
            metadata: Additional metadata
            
        Returns:
            Session details including ID and WebSocket URL
        """
        session_id = str(uuid.uuid4())
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=settings.SESSION_TIMEOUT_MINUTES)
        
        session_data = {
            "session_id": session_id,
            "client_id": client_id,
            "status": SessionStatus.PENDING,
            "created_at": now,
            "expires_at": expires_at,
            "metadata": metadata or {},
            "audio_duration": 0.0,
        }
        
        self._sessions[session_id] = session_data
        self._transcripts[session_id] = []
        self._audio_chunks[session_id] = []
        
        logger.info(
            "Session created",
            "سیشن بنایا گیا",
            session_id=session_id,
            client_id=client_id
        )
        
        return session_data
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session details by ID.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session data or None if not found
        """
        session = self._sessions.get(session_id)
        
        # Check if expired
        if session and session["expires_at"] < datetime.utcnow():
            session["status"] = SessionStatus.EXPIRED
            logger.warning(
                "Session expired",
                "سیشن ختم ہو گیا",
                session_id=session_id
            )
        
        return session
    
    def update_session_status(self, session_id: str, status: SessionStatus):
        """Update session status.
        
        Args:
            session_id: Session identifier
            status: New status
        """
        if session_id in self._sessions:
            previous_status = self._sessions[session_id].get("status")
            self._sessions[session_id]["status"] = status
            if status == SessionStatus.COMPLETED:
                self._sessions[session_id]["completed_at"] = datetime.utcnow()
            
            logger.info(
                "Session status updated",
                "سیشن کی حیثیت اپ ڈیٹ کی گئی",
                session_id=session_id,
                previous_status=previous_status,
                new_status=status
            )
    
    def add_transcript(self, session_id: str, text: str, is_final: bool = False):
        """Add transcript segment to session.
        
        Args:
            session_id: Session identifier
            text: Transcript text
            is_final: Whether this is a final transcript
        """
        if session_id in self._transcripts:
            self._transcripts[session_id].append({
                "text": text,
                "is_final": is_final,
                "timestamp": datetime.utcnow()
            })
    
    def get_full_transcript(self, session_id: str) -> str:
        """Get complete transcript for a session.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Full transcript as string
        """
        if session_id not in self._transcripts:
            return ""
        
        # Concatenate all final transcripts
        final_transcripts = [
            t["text"] for t in self._transcripts[session_id] 
            if t.get("is_final", False)
        ]
        return " ".join(final_transcripts)
    
    def add_audio_chunk(self, session_id: str, audio_bytes: bytes):
        """Store audio chunk for session.
        
        Args:
            session_id: Session identifier
            audio_bytes: Compressed audio bytes (webm/opus from browser)
        """
        if session_id in self._audio_chunks:
            # Guardrail using compressed size (~32kbps ≈ 4kB/s)
            max_bytes = settings.MAX_AUDIO_DURATION_SECONDS * 4000
            current_bytes = sum(len(c) for c in self._audio_chunks[session_id])
            if current_bytes + len(audio_bytes) > max_bytes:
                logger.warning(
                    "Audio size limit reached; chunk dropped",
                    "آڈیو سائز کی حد پوری ہو گئی؛ chunk ڈراپ کیا گیا",
                    session_id=session_id
                )
                return
            self._audio_chunks[session_id].append(audio_bytes)
    
    def get_audio_duration(self, session_id: str) -> float:
        """Estimate total audio duration for session (compressed webm/opus).
        
        Uses a heuristic of ~32 kbps (4 kB/s) for speech audio recorded via MediaRecorder.
        """
        if session_id not in self._audio_chunks:
            return 0.0
        
        total_bytes = sum(len(chunk) for chunk in self._audio_chunks[session_id])
        # 4 kB per second heuristic
        return total_bytes / 4000.0
    
    def get_all_audio(self, session_id: str) -> bytes:
        """Get all audio chunks concatenated.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Complete audio as bytes (compressed webm/opus)
        """
        if session_id not in self._audio_chunks:
            return b""
        return b"".join(self._audio_chunks[session_id])
    
    def store_analysis(self, session_id: str, analysis: LegalAnalysis):
        """Store AI analysis results for session.
        
        Args:
            session_id: Session identifier
            analysis: Legal analysis results
        """
        if session_id in self._sessions:
            self._sessions[session_id]["analysis"] = analysis
    
    def get_result(self, session_id: str) -> Optional[InterviewResult]:
        """Get complete interview result.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Interview result or None
        """
        session = self.get_session(session_id)
        if not session:
            return None
        
        # Return InterviewResult with all available data
        return InterviewResult(
            session_id=session_id,
            status=session["status"],
            transcript=self.get_full_transcript(session_id),
            analysis=session.get("analysis"),
            audio_duration_seconds=self.get_audio_duration(session_id),
            audio_url=session.get("audio_url"),
            created_at=session["created_at"],
            completed_at=session.get("completed_at"),
            client_id=session.get("client_id"),
            metadata=session.get("metadata", {})
        )
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions (called by cron job)."""
        now = datetime.utcnow()
        expired = [
            sid for sid, session in self._sessions.items()
            if session["expires_at"] < now
        ]
        
        for session_id in expired:
            del self._sessions[session_id]
            del self._transcripts[session_id]
            del self._audio_chunks[session_id]
            
        if expired:
            logger.info(
                f"Cleaned up {len(expired)} expired sessions",
                f"{len(expired)} منقطع سیشن صاف کیے گئے"
            )


# Global session manager instance
session_manager = SessionManager()

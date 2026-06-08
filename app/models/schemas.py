"""Pydantic schemas for request/response models."""

from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, field_validator
from .enums import LegalDomain, Language, SessionStatus, Urgency, MessageType


# ============================================================================
# Session Management Schemas
# ============================================================================


class SessionCreate(BaseModel):
    """Request model for creating a new interview session."""

    client_id: Optional[str] = Field(
        None, description="Optional client ID from your system"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )


class SessionResponse(BaseModel):
    """Response model with session details and WebSocket URL."""

    session_id: str = Field(..., description="Unique session identifier")
    ws_url: str = Field(..., description="WebSocket URL for audio streaming")
    created_at: datetime = Field(..., description="Session creation timestamp")
    expires_at: datetime = Field(..., description="Session expiration timestamp")
    status: SessionStatus = Field(
        default=SessionStatus.PENDING, description="Current session status"
    )


# ============================================================================
# Legal Analysis Schemas
# ============================================================================


class KeyEntities(BaseModel):
    """Extracted entities from the interview."""

    parties: List[str] = Field(
        default_factory=list, description="Names of people involved"
    )
    locations: List[str] = Field(
        default_factory=list, description="Addresses, cities, properties"
    )
    dates: List[str] = Field(default_factory=list, description="Time references")
    amounts: List[str] = Field(
        default_factory=list, description="Monetary values with context"
    )


class LegalAnalysis(BaseModel):
    """Structured output from Gemini classification."""

    primary_language: Language = Field(..., description="Detected primary language")
    legal_domain: LegalDomain = Field(..., description="Classified legal domain")
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Classification confidence"
    )
    key_entities: KeyEntities = Field(..., description="Extracted entities")
    issue_summary: str = Field(..., description="Brief summary in English")
    case_title_en: str = Field(
        "", description="Short AI-generated case title in English"
    )
    case_title_ur: Optional[str] = Field(
        None, description="Short AI-generated case title in Urdu"
    )
    adr_suitable: bool = Field(..., description="Whether ADR is suitable")
    adr_reasoning: str = Field(..., description="Reasoning for ADR suitability")
    urgency: Urgency = Field(..., description="Case urgency level")
    urgency_reasoning: str = Field(..., description="Reasoning for urgency level")


class InterviewResult(BaseModel):
    """Complete interview result with transcript and analysis."""

    session_id: str = Field(..., description="Session identifier")
    status: SessionStatus = Field(..., description="Session status")
    transcript: str = Field(..., description="Full bilingual transcript")
    analysis: Optional[LegalAnalysis] = Field(None, description="AI analysis results")
    audio_duration_seconds: Optional[float] = Field(
        None, description="Total audio duration"
    )
    audio_url: Optional[str] = Field(
        None, description="Cloud Storage URL for audio replay"
    )
    created_at: datetime = Field(..., description="Session creation time")
    completed_at: Optional[datetime] = Field(
        None, description="Session completion time"
    )
    client_id: Optional[str] = Field(None, description="Associated client ID")
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )


# ============================================================================
# WebSocket Message Schemas
# ============================================================================


class AudioMessage(BaseModel):
    """WebSocket message for audio chunks from client."""

    type: MessageType = MessageType.AUDIO
    audio: str = Field(
        ..., description="Base64-encoded audio/webm (Opus) data from the browser"
    )
    sequence: Optional[int] = Field(None, description="Sequence number for ordering")


class TranscriptMessage(BaseModel):
    """WebSocket message for live transcript to client."""

    type: MessageType = MessageType.TRANSCRIPT
    text: str = Field(..., description="Transcribed text")
    is_final: bool = Field(..., description="Whether this is a final transcript")
    language: Optional[str] = Field(None, description="Detected language code")
    confidence: Optional[float] = Field(None, description="Transcription confidence")


class EndInterviewMessage(BaseModel):
    """WebSocket message to signal interview end."""

    type: MessageType = MessageType.END_INTERVIEW


class ResultsMessage(BaseModel):
    """WebSocket message with final results."""

    type: MessageType = MessageType.RESULTS
    session_id: str = Field(..., description="Session identifier")
    transcript: str = Field(..., description="Full transcript")
    analysis: Optional[LegalAnalysis] = Field(
        None, description="AI analysis (may be null if analysis failed)"
    )


class ErrorMessage(BaseModel):
    """WebSocket error message (bilingual)."""

    type: MessageType = MessageType.ERROR
    message_en: str = Field(..., description="Error message in English")
    message_ur: str = Field(..., description="Error message in Urdu")
    code: Optional[str] = Field(None, description="Error code")


class StatusMessage(BaseModel):
    """WebSocket status update message (bilingual)."""

    type: MessageType = MessageType.STATUS
    message_en: str = Field(..., description="Status message in English")
    message_ur: str = Field(..., description="Status message in Urdu")


# Union type for all WebSocket messages
WebSocketMessage = (
    AudioMessage
    | TranscriptMessage
    | EndInterviewMessage
    | ResultsMessage
    | ErrorMessage
    | StatusMessage
)


# ============================================================================
# Webhook Payload Schema
# ============================================================================


class WebhookPayload(BaseModel):
    """Payload sent to Express backend webhook."""

    session_id: str = Field(..., description="Session identifier")
    client_id: Optional[str] = Field(None, description="Associated client ID")
    transcript: str = Field(..., description="Full interview transcript")
    analysis: Optional[LegalAnalysis] = Field(
        None, description="AI analysis results (nullable on failure)"
    )
    audio_url: Optional[str] = Field(None, description="Cloud Storage URL for audio")
    audio_duration_seconds: float = Field(..., description="Total audio duration")
    completed_at: datetime = Field(..., description="Completion timestamp")
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )

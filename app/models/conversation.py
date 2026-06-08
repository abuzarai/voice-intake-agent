"""Conversation-specific data models."""

from datetime import datetime
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """A single turn in the conversation."""
    role: Literal["agent", "user"]
    message: str
    language: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    audio_duration: Optional[float] = None


class InterviewState(BaseModel):
    """Current state of the interview conversation."""
    session_id: str
    current_question: Optional[str] = None
    conversation_history: List[ConversationTurn] = Field(default_factory=list)
    extracted_info: Dict[str, Any] = Field(default_factory=dict)
    questions_asked: int = 0
    is_complete: bool = False
    primary_language: str = "ur-PK"  # Default to Urdu
    

class AgentSpeechMessage(BaseModel):
    """Agent speech message sent to client."""
    type: Literal["agent_speech"] = "agent_speech"
    audio: str  # base64 encoded audio
    text: str   # what agent is saying
    language: str
    sequence: int = 0


class ConversationStatusMessage(BaseModel):
    """Conversation status update."""
    type: Literal["conversation_status"] = "conversation_status"
    agent_state: Literal["speaking", "listening", "thinking", "complete"]
    can_speak: bool  # true if user can speak now
    message_en: str
    message_ur: str


class QuestionResponse(BaseModel):
    """Response from conversation controller."""
    next_question: str
    language: str
    interview_complete: bool
    extracted_info: Dict[str, Any]
    confidence: float = 0.0

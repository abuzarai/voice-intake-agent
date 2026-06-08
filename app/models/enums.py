"""Enumerations for legal domains, languages, and status codes."""

from enum import Enum


class LegalDomain(str, Enum):
    """Legal practice areas for classification."""
    FAMILY_LAW = "family_law"
    PROPERTY_LAW = "property_law"
    CRIMINAL_LAW = "criminal_law"
    CIVIL_LAW = "civil_law"
    LABOR_LAW = "labor_law"
    CORPORATE_LAW = "corporate_law"
    OTHER = "other"


class Language(str, Enum):
    """Supported languages."""
    URDU = "urdu"
    ENGLISH = "english"
    MIXED = "mixed"


class SessionStatus(str, Enum):
    """Interview session status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


class Urgency(str, Enum):
    """Case urgency level."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MessageType(str, Enum):
    """WebSocket message types."""
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    END_INTERVIEW = "end_interview"
    RESULTS = "results"
    ERROR = "error"
    STATUS = "status"

"""Canonical intake domain types, independent of HTTP, media and legacy sessions."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


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


class Urgency(str, Enum):
    """Case urgency level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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

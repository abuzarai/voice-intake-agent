"""Google Gemini integration for legal classification and entity extraction."""

import asyncio
import json
import os
import re
from typing import Optional
from google import genai
from app.config import settings
from app.models import LegalAnalysis, Language, LegalDomain, Urgency, KeyEntities
from app.utils import get_logger

logger = get_logger(__name__)


# Legal classification prompt template
LEGAL_CLASSIFICATION_PROMPT = """You are a legal intake specialist for a Pakistani law firm.

Analyze this client interview transcript and extract structured information.

TRANSCRIPT (may contain Urdu/English mixed):
{transcript}

INSTRUCTIONS:
- Identify the primary legal domain from: family_law, property_law, criminal_law, civil_law, labor_law, corporate_law, other
- Extract key entities (people, places, dates, amounts)
- Assess if Alternative Dispute Resolution (ADR) is suitable
- Rate urgency based on mentioned timelines and severity
- Provide 2-3 sentence summary in English
- Create a short case title suitable for dashboard display (5-10 words, neutral tone)
- Title language policy:
  - If preferred title mode is 'english_only': provide only English title, leave Urdu title empty
  - If preferred title mode is 'bilingual_urdu': provide both English and Urdu titles

PREFERRED TITLE MODE:
{title_mode}

Return ONLY valid JSON with this exact structure:
{{
  "primary_language": "urdu" | "english" | "mixed",
  "legal_domain": "family_law" | "property_law" | "criminal_law" | "civil_law" | "labor_law" | "corporate_law" | "other",
  "confidence_score": 0.0-1.0,
  "key_entities": {{
    "parties": ["list of people mentioned"],
    "locations": ["addresses, cities, properties"],
    "dates": ["time references"],
    "amounts": ["monetary values with context"]
  }},
  "issue_summary": "Brief summary in English",
  "case_title_en": "Short neutral case title in English",
  "case_title_ur": "Short neutral case title in Urdu or empty string if english_only",
  "adr_suitable": true | false,
  "adr_reasoning": "Why ADR is or isn't suitable",
  "urgency": "low" | "medium" | "high",
  "urgency_reasoning": "Why this urgency level"
}}
"""


class GeminiService:
    """Gemini Flash service for legal classification."""

    def __init__(self):
        """Initialize Gemini client.

        Preferred: Gemini API key (AI Studio) — no project or billing needed.
        Fallback: Vertex AI via ADC + GCP_PROJECT_ID.
        """
        try:
            if settings.GEMINI_API_KEY:
                self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
                self.model = settings.GEMINI_MODEL
                logger.info(
                    "Gemini client initialized with API key",
                    "Gemini کلائنٹ API کیز کے ساتھ شروع ہوا",
                )
            elif settings.GCP_PROJECT_ID:
                self.client = genai.Client(
                    vertexai=True,
                    project=settings.GCP_PROJECT_ID,
                    location="us-central1",
                )
                self.model = settings.GEMINI_MODEL
                logger.info(
                    "Gemini client initialized with Vertex AI",
                    "Gemini کلائنٹ Vertex AI کے ساتھ شروع ہوا",
                )
            else:
                raise RuntimeError(
                    "Missing Gemini credentials: set GEMINI_API_KEY (or GCP_PROJECT_ID for Vertex AI)"
                )
        except Exception as e:
            logger.error(
                f"Failed to initialize Gemini client: {str(e)}",
                f"Gemini کلائنٹ شروع کرنے میں ناکامی: {str(e)}",
            )
            self.client = None
            self.model = None

    async def analyze_transcript(
        self, transcript: str, session_id: str, preferred_language: Optional[str] = None
    ) -> Optional[LegalAnalysis]:
        """Analyze interview transcript and extract structured information.

        Args:
            transcript: Full interview transcript
            session_id: Session identifier for logging

        Returns:
            Structured legal analysis or None on error
        """
        if not self.model:
            logger.error(
                "Gemini model not initialized",
                "Gemini ماڈل شروع نہیں ہوا",
                session_id=session_id,
            )
            return None

        if not transcript or len(transcript.strip()) < 10:
            logger.warning(
                "Transcript too short for analysis",
                "ٹرانسکرپٹ تجزیہ کے لیے بہت چھوٹا ہے",
                session_id=session_id,
            )
            return None

        try:
            preferred = str(preferred_language or "").strip().lower()
            title_mode = "bilingual_urdu" if preferred == "urdu" else "english_only"

            # Generate prompt
            prompt = LEGAL_CLASSIFICATION_PROMPT.format(
                transcript=transcript,
                title_mode=title_mode,
            )

            logger.info(
                "Sending transcript to Gemini for analysis",
                "تجزیہ کے لیے Gemini کو ٹرانسکرپٹ بھیج رہے ہیں",
                session_id=session_id,
                transcript_length=len(transcript),
            )

            # Generate response using Vertex AI
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=0.2  # Low temperature for more consistent output
                ),
            )

            # Parse JSON response (extract from markdown if needed)
            raw_text = response.text if response.text else ""
            json_text = self._extract_json_from_text(raw_text)
            result = json.loads(json_text)

            # Convert to Pydantic model
            analysis = LegalAnalysis(
                primary_language=Language(result["primary_language"]),
                legal_domain=LegalDomain(result["legal_domain"]),
                confidence_score=float(result["confidence_score"]),
                key_entities=KeyEntities(**result["key_entities"]),
                issue_summary=result["issue_summary"],
                case_title_en=result.get("case_title_en", ""),
                case_title_ur=result.get("case_title_ur") or None,
                adr_suitable=result["adr_suitable"],
                adr_reasoning=result["adr_reasoning"],
                urgency=Urgency(result["urgency"]),
                urgency_reasoning=result["urgency_reasoning"],
            )

            logger.info(
                f"Analysis complete: {analysis.legal_domain.value} (confidence: {analysis.confidence_score:.2f})",
                f"تجزیہ مکمل: {analysis.legal_domain.value}",
                session_id=session_id,
            )

            return analysis

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse Gemini JSON response: {str(e)}",
                f"Gemini JSON جواب parse نہیں ہو سکا: {str(e)}",
                session_id=session_id,
            )
            return None
        except Exception as e:
            logger.error(
                f"Gemini analysis error: {str(e)}",
                f"Gemini تجزیہ میں خرابی: {str(e)}",
                session_id=session_id,
            )
            return None

    def _extract_json_from_text(self, text: str) -> str:
        """
        Extract JSON from text that might be wrapped in markdown code blocks.

        Args:
            text: Raw response text from Gemini

        Returns:
            Extracted JSON string
        """
        if not text:
            return "{}"

        # Try to extract from markdown code blocks (```json ... ``` or ``` ... ```)
        json_block_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_block_pattern, text)
        if matches:
            return matches[0].strip()

        # If no code blocks, try to find JSON object directly
        # Look for content between first { and last }
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return text[first_brace : last_brace + 1]

        # Return original text as fallback
        return text.strip()

    async def generate_json_response(self, prompt: str) -> dict:
        """
        Generate JSON response from Gemini for conversation control.

        Args:
            prompt: Conversation controller prompt

        Returns:
            Parsed JSON response
        """
        if not self.model:
            return {
                "extracted_info": {},
                "needs_clarification": False,
                "next_question_key": "issue_description",
                "interview_complete": False,
                "confidence": 0.5,
            }

        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(temperature=0.3),
            )

            # Log raw response for debugging
            raw_text = response.text if response.text else ""
            logger.info(
                f"Gemini raw response (first 200 chars): {raw_text[:200]}",
                f"Gemini خام جواب: {raw_text[:100]}",
            )

            # Extract JSON from potential markdown formatting
            json_text = self._extract_json_from_text(raw_text)

            return json.loads(json_text)

        except Exception as e:
            logger.error(
                f"Gemini JSON generation error: {str(e)}",
                f"Gemini JSON خرابی: {str(e)}",
            )
            # Return safe fallback
            return {
                "extracted_info": {},
                "needs_clarification": False,
                "next_question_key": "issue_description",
                "interview_complete": False,
                "confidence": 0.5,
            }


# Global Gemini service instance
gemini_service = GeminiService()

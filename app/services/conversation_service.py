"""Conversation management service for conducting interviews."""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime

from app.models.conversation import (
    InterviewState,
    ConversationTurn,
    QuestionResponse
)
from app.models import Language
from app.services.gemini_service import gemini_service
from app.services.question_bank import get_question, get_all_keys
from app.utils import get_logger

logger = get_logger(__name__)


class ConversationManager:
    """Manages conversational interview flow."""
    
    # Predefined question sequence for structured interviews
    QUESTION_SEQUENCE = [
        "greeting",
        "issue_description",
        "timeline_start",
        "parties_involved",
        "location",
        "financial_impact",
        "previous_action",
        "urgency",
        "documentation",
        "desired_outcome"
    ]
    
    MAX_QUESTIONS = 20  # Increased to allow more thorough interviews
    
    def __init__(self):
        """Initialize conversation manager."""
        self._states: Dict[str, InterviewState] = {}
    
    def start_interview(
        self,
        session_id: str,
        language: str = "ur"
    ) -> str:
        """
        Start a new interview conversation.
        
        Args:
            session_id: Session identifier
            language: Primary language (ur or en)
            
        Returns:
            First question
        """
        # Create new interview state
        state = InterviewState(
            session_id=session_id,
            primary_language=f"{language}-PK" if language == "ur" else "en-US"
        )
        self._states[session_id] = state
        
        # Get greeting question
        greeting = get_question("greeting", language)
        state.current_question = greeting
        state.conversation_history.append(
            ConversationTurn(
                role="agent",
                message=greeting,
                language=language
            )
        )
        
        logger.info(
            f"Interview started for session {session_id}",
            f"سیشن {session_id} کے لیے انٹرویو شروع ہوا",
            language=language,
            first_question=greeting
        )
        
        return greeting
    
    def add_user_response(
        self,
        session_id: str,
        response: str,
        language: str = "ur"
    ):
        """
        Record user's response.
        
        Args:
            session_id: Session identifier
            response: User's answer
            language: Detected language
        """
        if session_id not in self._states:
            raise ValueError(f"No interview state for session {session_id}")
        
        state = self._states[session_id]
        state.conversation_history.append(
            ConversationTurn(
                role="user",
                message=response,
                language=language
            )
        )
        logger.info(
            "Recorded user response",
            "صارف کا جواب محفوظ کیا گیا",
            session_id=session_id,
            chars=len(response),
            language=language
        )
    
    async def get_next_question(
        self,
        session_id: str,
        user_answer: str
    ) -> QuestionResponse:
        """
        Determine next question based on user's answer.
        
        Uses AI to:
        1. Analyze user's answer
        2. Extract relevant information
        3. Determine if clarification needed
        4. Generate next question or conclude
        
        Args:
            session_id: Session identifier
            user_answer: User's latest answer
            
        Returns:
            QuestionResponse with next question or completion signal
        """
        if session_id not in self._states:
            raise ValueError(f"No interview state for session {session_id}")
        
        state = self._states[session_id]
        state.questions_asked += 1
        
        # Check if we've hit max questions
        if state.questions_asked >= self.MAX_QUESTIONS:
            return await self._conclude_interview(session_id)
        
        # Build conversation context for Gemini
        conversation_context = self._build_context(state)
        
        # Use Gemini to determine next step
        prompt = f"""You are an AI legal intake assistant conducting an interview in Pakistan.

Current Interview Status:
- Questions asked: {state.questions_asked}/{self.MAX_QUESTIONS}
- Information gathered so far: {state.extracted_info}

Conversation history (last 5 turns):
{conversation_context}

User's latest answer: "{user_answer}"

Your tasks:
1. Extract any important information from the user's answer (names, dates, amounts, locations, etc.)
2. Determine if the answer is complete or needs clarification
3. Decide the next question to ask OR conclude if enough information collected

INFORMATION REQUIRED (must collect MOST of these before completing):
- Client name ✓ (required)
- Legal issue type (property, family, criminal, etc.) ✓ (required)
- Timeline (when it started)
- Parties involved (other party names)
- Location of issue
- Financial amount (if any)
- Urgency level
- Desired outcome

IMPORTANT: Only set interview_complete=true when you have gathered AT LEAST:
1. Client's name
2. Type of legal issue
3. Basic description of the problem
4. At least 2 more key details (timeline, parties, location, amount, etc.)

Respond in JSON format:
{{
    "extracted_info": {{"key": "value", ...}},
    "needs_clarification": true/false,
    "next_question_key": "question_key_from_bank" or "custom",
    "custom_question_en": "Question in English",
    "custom_question_ur": "سوال اردو میں",
    "interview_complete": true/false,
    "confidence": 0.0-1.0
}}

Available question keys: greeting, issue_description, timeline_start, parties_involved, location, financial_impact, previous_action, urgency, documentation, desired_outcome, closure

If interview_complete is true, provide a closing statement instead of next_question.
"""
        
        try:
            # Get AI response
            ai_response = await gemini_service.generate_json_response(prompt)
            
            # Update extracted info
            if "extracted_info" in ai_response:
                state.extracted_info.update(ai_response["extracted_info"])
                logger.info(
                    "Updated extracted info",
                    "نکالی گئی معلومات اپ ڈیٹ کی گئیں",
                    session_id=session_id,
                    keys=list(state.extracted_info.keys())
                )
            
            # Determine if interview is complete
            interview_should_complete = ai_response.get("interview_complete", False)
            
            # Validate that we have minimum required info before completing
            if interview_should_complete:
                required_info_count = len(state.extracted_info)
                has_name = any(k in state.extracted_info for k in ["name", "client_name", "user_name", "نام"])
                has_issue = any(k in state.extracted_info for k in ["issue", "legal_issue", "issue_type", "مسئلہ", "قانونی مسئلہ"])
                
                # Only complete if we have at least 3 pieces of info including name and issue type
                if required_info_count < 3 or not (has_name or has_issue):
                    logger.warning(
                        f"AI suggested completion but insufficient info gathered: {required_info_count} fields",
                        f"AI نے مکمل کرنے کا مشورہ دیا لیکن کافی معلومات نہیں: {required_info_count} فیلڈز",
                        session_id=session_id,
                        extracted_keys=list(state.extracted_info.keys())
                    )
                    interview_should_complete = False
            
            if interview_should_complete:
                logger.info(
                    f"Interview completing with {len(state.extracted_info)} fields extracted",
                    f"انٹرویو {len(state.extracted_info)} فیلڈز کے ساتھ مکمل ہو رہا ہے",
                    session_id=session_id,
                    extracted_keys=list(state.extracted_info.keys())
                )
                return await self._conclude_interview(session_id)
            
            # Get next question
            next_question_key = ai_response.get("next_question_key")
            language = "ur" if "PK" in state.primary_language else "en"
            
            if next_question_key and next_question_key in get_all_keys():
                # Use predefined question
                next_question = get_question(next_question_key, language)
            elif ai_response.get("custom_question_ur") or ai_response.get("custom_question_en"):
                # Use AI-generated custom question
                next_question = ai_response.get(f"custom_question_{language}", "")
            else:
                # Fallback: use next in sequence
                next_index = min(state.questions_asked, len(self.QUESTION_SEQUENCE) - 1)
                next_key = self.QUESTION_SEQUENCE[next_index]
                next_question = get_question(next_key, language)
            
            # Store current question
            state.current_question = next_question
            state.conversation_history.append(
                ConversationTurn(
                    role="agent",
                    message=next_question,
                    language=language
                )
            )
            
            return QuestionResponse(
                next_question=next_question,
                language=state.primary_language,
                interview_complete=False,
                extracted_info=state.extracted_info,
                confidence=ai_response.get("confidence", 0.8)
            )
            
        except Exception as e:
            logger.error(
                f"Error getting next question: {str(e)}",
                f"اگلا سوال حاصل کرنے میں خرابی: {str(e)}",
                session_id=session_id
            )
            # Fallback to predefined sequence
            return await self._get_fallback_question(session_id)
    
    async def _conclude_interview(self, session_id: str) -> QuestionResponse:
        """Conclude the interview with closing statement."""
        state = self._states[session_id]
        state.is_complete = True
        
        language = "ur" if "PK" in state.primary_language else "en"
        closing = get_question("closure", language)
        
        state.conversation_history.append(
            ConversationTurn(
                role="agent",
                message=closing,
                language=language
            )
        )
        
        logger.info(
            f"Interview completed for session {session_id}",
            f"سیشن {session_id} کے لیے انٹرویو مکمل ہوا",
            questions_asked=state.questions_asked
        )
        
        return QuestionResponse(
            next_question=closing,
            language=state.primary_language,
            interview_complete=True,
            extracted_info=state.extracted_info,
            confidence=1.0
        )
    
    async def _get_fallback_question(self, session_id: str) -> QuestionResponse:
        """Get next question from predefined sequence (fallback)."""
        state = self._states[session_id]
        language = "ur" if "PK" in state.primary_language else "en"
        
        next_index = min(state.questions_asked, len(self.QUESTION_SEQUENCE) - 1)
        next_key = self.QUESTION_SEQUENCE[next_index]
        next_question = get_question(next_key, language)
        
        state.current_question = next_question
        
        return QuestionResponse(
            next_question=next_question,
            language=state.primary_language,
            interview_complete=False,
            extracted_info=state.extracted_info,
            confidence=0.5
        )
    
    def _build_context(self, state: InterviewState) -> str:
        """Build conversation context string for AI."""
        last_turns = state.conversation_history[-5:]  # Last 5 turns
        context_lines = []
        
        for turn in last_turns:
            role = "Agent" if turn.role == "agent" else "User"
            context_lines.append(f"{role}: {turn.message}")
        
        return "\n".join(context_lines)
    
    def get_conversation_history(self, session_id: str) -> List[ConversationTurn]:
        """Get full conversation history."""
        if session_id not in self._states:
            return []
        return self._states[session_id].conversation_history
    
    def get_extracted_info(self, session_id:str) -> Dict:
        """Get all extracted information."""
        if session_id not in self._states:
            return {}
        return self._states[session_id].extracted_info
    
    def is_interview_complete(self, session_id: str) -> bool:
        """Check if interview is complete."""
        if session_id not in self._states:
            return False
        return self._states[session_id].is_complete


# Global conversation manager instance
conversation_manager = ConversationManager()

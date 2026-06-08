"""Bilingual question bank for legal interviews."""

QUESTION_BANK = {
    "greeting": {
        "en": "Hello, I'm your legal intake assistant. What is your name?",
        "ur": "السلام علیکم، میں آپ کا قانونی مدد کار ہوں۔ آپ کا نام کیا ہے؟"
    },
    
    "issue_description": {
        "en": "Please describe your legal issue in detail.",
        "ur": "براہ کرم اپنے قانونی مسئلے کی تفصیل بتائیں۔"
    },
    
    "timeline_start": {
        "en": "When did this issue start?",
        "ur": "یہ مسئلہ کب شروع ہوا؟"
    },
    
    "parties_involved": {
        "en": "Who is involved in this matter besides you?",
        "ur": "آپ کے علاوہ اس معاملے میں کون کون شامل ہے؟"
    },
    
    "location": {
        "en": "Where did this incident occur?",
        "ur": "یہ واقعہ کہاں ہوا؟"
    },
    
    "financial_impact": {
        "en": "Is there any financial amount involved? If yes, how much?",
        "ur": "کیا اس میں کوئی مالی رقم شامل ہے؟ اگر ہاں، تو کتنی؟"
    },
    
    "previous_action": {
        "en": "Have you taken any legal action before regarding this matter?",
        "ur": "کیا آپ نے اس معاملے میں پہلے کوئی قانونی کارروائی کی ہے؟"
    },
    
    "urgency": {
        "en": "Is this matter urgent? Do you have any upcoming deadlines?",
        "ur": "کیا یہ معاملہ فوری ہے؟ کیا آپ کی کوئی آنے والی ڈیڈ لائن ہے؟"
    },
    
    "documentation": {
        "en": "Do you have any documents or evidence related to this case?",
        "ur": "کیا آپ کے پاس اس کیس سے متعلق کوئی دستاویزات یا ثبوت ہیں؟"
    },
    
    "witnesses": {
        "en": "Were there any witnesses to this incident?",
        "ur": "کیا اس واقعے کے کوئی گواہ تھے؟"
    },
    
    "desired_outcome": {
        "en": "What outcome are you hoping to achieve?",
        "ur": "آپ کیا نتیجہ حاصل کرنا چاہتے ہیں؟"
    },
    
    "police_involved": {
        "en": "Have you filed a police report or FIR?",
        "ur": "کیا آپ نے پولیس رپورٹ یا ایف آئی آر درج کروائی ہے؟"
    },
    
    "mediation_attempted": {
        "en": "Have you tried to resolve this through mediation or negotiation?",
        "ur": "کیا آپ نے ثالثی یا گفت و شنید کے ذریعے اسے حل کرنے کی کوشش کی ہے؟"
    },
    
    "closure": {
        "en": "Thank you for providing all this information. I have everything I need. I will connect you with a suitable lawyer shortly.",
        "ur": "یہ تمام معلومات فراہم کرنے کا شکریہ۔ میرے پاس سب کچھ ہے۔ میں جلد ہی آپ کو ایک مناسب وکیل سے ملاؤں گا۔"
    },
    
    "clarification_amount": {
        "en": "Could you please clarify the exact amount involved?",
        "ur": "کیا آپ براہ کرم شامل صحیح رقم واضح کر سکتے ہیں؟"
    },
    
    "clarification_date": {
        "en": "Could you provide a more specific date or timeframe?",
        "ur": "کیا آپ مزید مخصوص تاریخ یا وقت فراہم کر سکتے ہیں؟"
    },
    
    "clarification_person": {
        "en": "Could you please provide more details about this person?",
        "ur": "کیا آپ اس شخص کے بارے میں مزید تفصیلات فراہم کر سکتے ہیں؟"
    },
    
    "acknowledgment_understanding": {
        "en": "I understand. Please continue.",
        "ur": "سمجھ گیا۔ براہ کرم جاری رکھیں۔"
    },
    
    "acknowledgment_noted": {
        "en": "Noted. What else can you tell me?",
        "ur": "لکھ لیا۔ آپ مجھے اور کیا بتا سکتے ہیں؟"
    }
}


def get_question(key: str, language: str = "ur") -> str:
    """
    Get a question from the bank.
    
    Args:
        key: Question key
        language: Language code (en or ur)
        
    Returns:
        Question text
    """
    question = QUESTION_BANK.get(key, {})
    return question.get(language, question.get("en", ""))


def get_all_keys() -> list:
    """Get all available question keys."""
    return list(QUESTION_BANK.keys())

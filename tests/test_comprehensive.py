"""Comprehensive test suite for Voice Interview Agent."""

import requests
import json
import asyncio
import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = "http://127.0.0.1:8000"

def print_header(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def print_test(name, passed, details=""):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {name}")
    if details:
        print(f"       {details}")

# =============================================================================
# REST API TESTS
# =============================================================================

def test_rest_api():
    """Test all REST API endpoints."""
    print_header("REST API TESTS")
    results = []
    
    # Test 1: Health Check
    try:
        r = requests.get(f"{BASE_URL}/api/v1/health")
        passed = r.status_code == 200 and r.json().get("status") == "healthy"
        print_test("Health Check", passed, f"Status: {r.json().get('status')}")
        results.append(passed)
    except Exception as e:
        print_test("Health Check", False, str(e))
        results.append(False)
    
    # Test 2: Root Endpoint
    try:
        r = requests.get(f"{BASE_URL}/")
        passed = r.status_code == 200 and "Voice Interview Agent" in r.json().get("service", "")
        print_test("Root Endpoint", passed, f"Service: {r.json().get('service')}")
        results.append(passed)
    except Exception as e:
        print_test("Root Endpoint", False, str(e))
        results.append(False)
    
    # Test 3: Create Session
    session_id = None
    try:
        payload = {"client_id": "test_client", "metadata": {"test": True}}
        r = requests.post(f"{BASE_URL}/api/v1/sessions", json=payload)
        passed = r.status_code == 201 and "session_id" in r.json()
        session_id = r.json().get("session_id")
        print_test("Create Session", passed, f"Session ID: {session_id[:8]}...")
        results.append(passed)
    except Exception as e:
        print_test("Create Session", False, str(e))
        results.append(False)
    
    # Test 4: Get Session
    if session_id:
        try:
            r = requests.get(f"{BASE_URL}/api/v1/sessions/{session_id}")
            passed = r.status_code == 200 and r.json().get("status") == "pending"
            print_test("Get Session", passed, f"Status: {r.json().get('status')}")
            results.append(passed)
        except Exception as e:
            print_test("Get Session", False, str(e))
            results.append(False)
    
    # Test 5: API Documentation
    try:
        r = requests.get(f"{BASE_URL}/docs")
        passed = r.status_code == 200
        print_test("API Documentation", passed, "OpenAPI docs accessible")
        results.append(passed)
    except Exception as e:
        print_test("API Documentation", False, str(e))
        results.append(False)
    
    return results, session_id


# =============================================================================
# SERVICE TESTS
# =============================================================================

def test_services():
    """Test core services."""
    print_header("SERVICE TESTS")
    results = []
    
    # Test 1: Session Manager
    try:
        from app.services.session_service import session_manager
        session = session_manager.create_session("test", {"test": True})
        passed = session.get("session_id") is not None
        print_test("Session Manager", passed, f"Created session: {session['session_id'][:8]}...")
        results.append(passed)
    except Exception as e:
        print_test("Session Manager", False, str(e))
        results.append(False)
    
    # Test 2: Question Bank
    try:
        from app.services.question_bank import get_question, get_all_keys
        greeting_ur = get_question("greeting", "ur")
        greeting_en = get_question("greeting", "en")
        keys = get_all_keys()
        passed = len(greeting_ur) > 0 and len(greeting_en) > 0 and len(keys) > 10
        print_test("Question Bank", passed, f"{len(keys)} questions available")
        results.append(passed)
    except Exception as e:
        print_test("Question Bank", False, str(e))
        results.append(False)
    
    # Test 3: Conversation Manager
    try:
        from app.services.conversation_service import conversation_manager
        test_session = "test_conv_123"
        first_q = conversation_manager.start_interview(test_session, "ur")
        passed = len(first_q) > 0
        print_test("Conversation Manager", passed, f"First question: {first_q[:30]}...")
        results.append(passed)
    except Exception as e:
        print_test("Conversation Manager", False, str(e))
        results.append(False)
    
    # Test 4: TTS Service (structure check, not actual API call)
    try:
        from app.services.tts_service import tts_service
        passed = hasattr(tts_service, 'synthesize_speech') and hasattr(tts_service, 'voices')
        urdu_voice = tts_service.voices.get("ur-PK", {}).get("name")
        print_test("TTS Service Structure", passed, f"Urdu voice: {urdu_voice}")
        results.append(passed)
    except Exception as e:
        print_test("TTS Service Structure", False, str(e))
        results.append(False)
    
    # Test 5: Gemini Service (structure check)
    try:
        from app.services.gemini_service import gemini_service
        passed = hasattr(gemini_service, 'analyze_transcript') and hasattr(gemini_service, 'generate_json_response')
        model_status = "Initialized" if gemini_service.model else "Not initialized (missing API key)"
        print_test("Gemini Service Structure", passed, model_status)
        results.append(passed)
    except Exception as e:
        print_test("Gemini Service Structure", False, str(e))
        results.append(False)
    
    # Test 6: STT Service (structure check)
    try:
        from app.services.stt_service import stt_service
        passed = hasattr(stt_service, 'recognize_audio')
        print_test("STT Service Structure", passed, "recognize_audio method available")
        results.append(passed)
    except Exception as e:
        print_test("STT Service Structure", False, str(e))
        results.append(False)
    
    # Test 7: Storage Service (structure check)
    try:
        from app.services.storage_service import storage_service
        passed = hasattr(storage_service, 'upload_audio')
        print_test("Storage Service Structure", passed, "upload_audio method available")
        results.append(passed)
    except Exception as e:
        print_test("Storage Service Structure", False, str(e))
        results.append(False)
    
    return results


# =============================================================================
# MODEL TESTS
# =============================================================================

def test_models():
    """Test data models."""
    print_header("MODEL TESTS")
    results = []
    
    # Test 1: Conversation Models
    try:
        from app.models import ConversationTurn, InterviewState, AgentSpeechMessage
        turn = ConversationTurn(role="agent", message="Hello", language="en")
        state = InterviewState(session_id="test123")
        speech = AgentSpeechMessage(audio="base64data", text="Hello", language="en-US")
        passed = turn.role == "agent" and state.session_id == "test123"
        print_test("Conversation Models", passed, "All models validated")
        results.append(passed)
    except Exception as e:
        print_test("Conversation Models", False, str(e))
        results.append(False)
    
    # Test 2: Session Models
    try:
        from app.models import SessionCreate, SessionResponse
        create = SessionCreate(client_id="test")
        passed = create.client_id == "test"
        print_test("Session Models", passed, "SessionCreate validated")
        results.append(passed)
    except Exception as e:
        print_test("Session Models", False, str(e))
        results.append(False)
    
    # Test 3: Enums
    try:
        from app.models import LegalDomain, Language, SessionStatus, Urgency
        passed = LegalDomain.PROPERTY_LAW.value == "property_law"
        print_test("Enum Models", passed, f"LegalDomain.PROPERTY_LAW = {LegalDomain.PROPERTY_LAW.value}")
        results.append(passed)
    except Exception as e:
        print_test("Enum Models", False, str(e))
        results.append(False)
    
    return results


# =============================================================================
# WEBSOCKET ENDPOINT TESTS
# =============================================================================

def test_websocket_endpoints():
    """Test WebSocket endpoint availability."""
    print_header("WEBSOCKET ENDPOINT TESTS")
    results = []
    
    # Create a test session first
    try:
        r = requests.post(f"{BASE_URL}/api/v1/sessions", json={"client_id": "ws_test"})
        session_id = r.json().get("session_id")
    except:
        session_id = "test-session-id"
    
    # Test 1: Conversational WebSocket URL
    try:
        r = requests.post(f"{BASE_URL}/api/v1/sessions", json={"client_id": "ws_test2"})
        ws_url = r.json().get("ws_url", "")
        passed = "/api/v1/ws/" in ws_url
        print_test("Conversational WS URL", passed, f"URL: {ws_url}")
        results.append(passed)
    except Exception as e:
        print_test("Conversational WS URL", False, str(e))
        results.append(False)
    
    # Test 2: Listen-Only WebSocket (verify route exists via docs)
    try:
        r = requests.get(f"{BASE_URL}/openapi.json")
        openapi = r.json()
        paths = openapi.get("paths", {})
        listen_only_exists = any("listen-only" in path for path in paths.keys())
        conversational_exists = any("/ws/{session_id}" in path and "listen-only" not in path for path in paths.keys())
        passed = listen_only_exists
        print_test("Listen-Only WS Route", passed, "Route available in OpenAPI spec")
        results.append(passed)
        
        passed = conversational_exists
        print_test("Conversational WS Route", passed, "Route available in OpenAPI spec")
        results.append(passed)
    except Exception as e:
        print_test("WebSocket Routes", False, str(e))
        results.append(False)
    
    return results


# =============================================================================
# GCP SERVICE TESTS
# =============================================================================

def test_gcp_services():
    """Test GCP service connectivity (requires credentials)."""
    print_header("GCP SERVICE TESTS")
    results = []
    
    # Test 1: GCP Credentials
    try:
        from google.auth import default
        credentials, project = default()
        passed = credentials is not None
        print_test("GCP Credentials (ADC)", passed, f"Project: {project or 'Not set'}")
        results.append(passed)
    except Exception as e:
        print_test("GCP Credentials (ADC)", False, str(e))
        results.append(False)
    
    # Test 2: Speech-to-Text API
    try:
        from google.cloud import speech
        client = speech.SpeechClient()
        passed = client is not None
        print_test("Speech-to-Text API", passed, "Client initialized")
        results.append(passed)
    except Exception as e:
        print_test("Speech-to-Text API", False, str(e))
        results.append(False)
    
    # Test 3: Text-to-Speech API
    try:
        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        # List available voices to verify API access
        voices = client.list_voices(language_code="ur-PK")
        urdu_voices = [v.name for v in voices.voices if "ur" in v.name.lower()]
        passed = len(urdu_voices) > 0
        print_test("Text-to-Speech API", passed, f"Urdu voices: {len(urdu_voices)} available")
        results.append(passed)
    except Exception as e:
        print_test("Text-to-Speech API", False, str(e))
        results.append(False)
    
    # Test 4: Cloud Storage
    try:
        from google.cloud import storage
        client = storage.Client()
        passed = client is not None
        print_test("Cloud Storage API", passed, "Client initialized")
        results.append(passed)
    except Exception as e:
        print_test("Cloud Storage API", False, str(e))
        results.append(False)
    
    # Test 5: Gemini API
    try:
        import google.generativeai as genai
        from app.config import settings
        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.0-flash-exp')
            passed = model is not None
            print_test("Gemini API", passed, "Model initialized")
        else:
            print_test("Gemini API", False, "API key not set in .env")
            passed = False
        results.append(passed)
    except Exception as e:
        print_test("Gemini API", False, str(e))
        results.append(False)
    
    return results


# =============================================================================
# MAIN TEST RUNNER
# =============================================================================

def run_all_tests():
    """Run all test suites."""
    print("\n" + "=" * 60)
    print("  VOICE INTERVIEW AGENT - COMPREHENSIVE TEST SUITE")
    print("=" * 60)
    
    all_results = []
    
    # REST API Tests
    rest_results, session_id = test_rest_api()
    all_results.extend(rest_results)
    
    # Service Tests
    service_results = test_services()
    all_results.extend(service_results)
    
    # Model Tests
    model_results = test_models()
    all_results.extend(model_results)
    
    # WebSocket Tests
    ws_results = test_websocket_endpoints()
    all_results.extend(ws_results)
    
    # GCP Tests
    gcp_results = test_gcp_services()
    all_results.extend(gcp_results)
    
    # Summary
    print_header("TEST SUMMARY")
    passed = sum(all_results)
    total = len(all_results)
    percentage = (passed / total * 100) if total > 0 else 0
    
    print(f"\nTotal: {passed}/{total} tests passed ({percentage:.1f}%)")
    
    if passed == total:
        print("\n[SUCCESS] All tests passed! Ready for deployment.")
        return True
    else:
        failed = total - passed
        print(f"\n[WARNING] {failed} test(s) failed. Review errors above.")
        return False


if __name__ == "__main__":
    try:
        success = run_all_tests()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTests interrupted.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {str(e)}")
        sys.exit(1)

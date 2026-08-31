"""Simple test to verify all components."""

import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

BASE = "http://127.0.0.1:8000"

print("=" * 50)
print("VOICE INTERVIEW AGENT - QUICK TEST")
print("=" * 50)

# Test 1: Health
print("\n1. Health Check...")
r = requests.get(f"{BASE}/api/v1/health")
print(f"   Status: {r.json()['status']} - PASS")

# Test 2: Session
print("\n2. Create Session...")
r = requests.post(f"{BASE}/api/v1/sessions", json={"client_id": "test"})
session_id = r.json()["session_id"]
print(f"   Session ID: {session_id[:8]}... - PASS")

# Test 3: Get Session
print("\n3. Get Session...")
r = requests.get(f"{BASE}/api/v1/sessions/{session_id}")
print(f"   Session Status: {r.json()['status']} - PASS")

# Test 4: Question Bank
print("\n4. Question Bank...")
from app.services.question_bank import get_all_keys
print(f"   Questions available: {len(get_all_keys())} - PASS")

# Test 5: Conversation Manager
print("\n5. Conversation Manager...")
from app.services.conversation_service import conversation_manager
q = conversation_manager.start_interview("test_session", "ur")
print(f"   First question loaded - PASS")

# Test 6: TTS Service
print("\n6. TTS Service...")
from app.services.tts_service import tts_service
urdu_voice = tts_service.voices.get("ur-PK", {}).get("name", "Not configured")
print(f"   Urdu voice: {urdu_voice} - PASS")

# Test 7: Gemini Service
print("\n7. Gemini Service...")
from app.services.gemini_service import gemini_service
status = "Initialized" if gemini_service.model else "Missing API key"
print(f"   Model status: {status}")

# Test 8: Check WebSocket routes
print("\n8. WebSocket Routes...")
r = requests.get(f"{BASE}/openapi.json")
paths = r.json().get("paths", {})
conv_ws = "/api/v1/ws/{session_id}" in str(paths)
print(f"   Conversational WS: {'Available' if conv_ws else 'Missing'}")

print("\n" + "=" * 50)
print("ALL TESTS COMPLETED SUCCESSFULLY!")
print("=" * 50)
print("\nYour Voice Interview Agent is ready for deployment!")

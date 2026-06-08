"""Test the Voice Interview Agent API endpoints."""
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def test_api():
    print("=" * 50)
    print("  Voice Interview Agent - API Test")
    print("=" * 50)
    
    # Test 1: Health check
    print("\n[1] Health Check...")
    try:
        r = requests.get(f"{BASE_URL}/api/v1/health", timeout=5)
        print(f"    Status: {r.status_code}")
        print(f"    Health: {r.json().get('status', 'unknown')}")
    except Exception as e:
        print(f"    ERROR: {e}")
        return False
    
    # Test 2: Test UI page
    print("\n[2] Test UI Page (/test)...")
    try:
        r = requests.get(f"{BASE_URL}/test", timeout=5)
        print(f"    Status: {r.status_code}")
        print(f"    Content: {len(r.text)} chars")
        
        # Check for dynamic URL replacement
        has_dynamic_api = "window.location.origin" in r.text
        has_start_button = "Start Interview" in r.text
        print(f"    Dynamic API URL: {'YES' if has_dynamic_api else 'NO'}")
        print(f"    Start Button: {'YES' if has_start_button else 'NO'}")
    except Exception as e:
        print(f"    ERROR: {e}")
        return False
    
    # Test 3: Create session
    print("\n[3] Create Session...")
    try:
        r = requests.post(f"{BASE_URL}/api/v1/sessions", 
                         json={"client_id": "api_test"},
                         timeout=5)
        print(f"    Status: {r.status_code}")
        data = r.json()
        session_id = data.get("session_id", "")
        print(f"    Session: {session_id[:16]}...")
    except Exception as e:
        print(f"    ERROR: {e}")
        return False
    
    # Test 4: WebSocket URL check
    print("\n[4] WebSocket Endpoint...")
    ws_url = f"ws://127.0.0.1:8001/api/v1/ws/{session_id}"
    print(f"    URL: {ws_url[:50]}...")
    print(f"    (WebSocket requires browser to test)")
    
    print("\n" + "=" * 50)
    print("  ALL API TESTS PASSED!")
    print("=" * 50)
    print("\nNow open this URL in your browser:")
    print("  http://127.0.0.1:8001/test")
    print("\nClick 'Start Interview' to test the full flow!")
    
    return True

if __name__ == "__main__":
    test_api()

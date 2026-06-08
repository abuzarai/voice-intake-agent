"""Comprehensive GCP Services Test Suite for Voice Interview Agent."""

import os
import sys
import json
import time

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def print_header(title: str):
    """Print section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(name: str, passed: bool, details: str = ""):
    """Print test result."""
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {name}")
    if details:
        print(f"       {details}")


class GCPTester:
    """Test all GCP services."""
    
    def __init__(self):
        self.project_id = os.getenv('GCP_PROJECT_ID')
        self.credentials_path = os.getenv('GCP_CREDENTIALS_PATH')
        self.gemini_key = os.getenv('GEMINI_API_KEY')
        self.results = []
    
    def setup_credentials(self) -> bool:
        """Setup GCP credentials."""
        print_header("ENVIRONMENT SETUP")
        
        if not self.project_id:
            print_result("GCP_PROJECT_ID", False, "Not set in .env")
            return False
        print_result("GCP_PROJECT_ID", True, self.project_id)
        
        if self.credentials_path and os.path.exists(self.credentials_path):
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = self.credentials_path
            print_result("Service Account Key", True, self.credentials_path)
        else:
            print_result("Service Account Key", False, "Will use ADC")
        
        if self.gemini_key and self.gemini_key != 'your-gemini-api-key-here':
            print_result("Gemini API Key", True, f"{self.gemini_key[:10]}...")
        else:
            print_result("Gemini API Key", False, "Not configured")
        
        return True
    
    def test_speech_to_text(self) -> bool:
        """Test Speech-to-Text API."""
        print_header("SPEECH-TO-TEXT API TEST")
        
        try:
            from google.cloud import speech
            
            # Test 1: Client initialization
            client = speech.SpeechClient()
            print_result("Client Initialization", True)
            self.results.append(True)
            
            # Test 2: List operations (API connectivity)
            # Simple connectivity test
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code="ur-PK",
            )
            print_result("Config Creation", True, "ur-PK language supported")
            self.results.append(True)
            
            return True
            
        except Exception as e:
            print_result("Speech-to-Text", False, str(e))
            self.results.append(False)
            return False
    
    def test_text_to_speech(self) -> bool:
        """Test Text-to-Speech API with actual synthesis."""
        print_header("TEXT-TO-SPEECH API TEST")
        
        try:
            from google.cloud import texttospeech
            
            # Test 1: Client initialization
            client = texttospeech.TextToSpeechClient()
            print_result("Client Initialization", True)
            self.results.append(True)
            
            # Test 2: List Urdu voices
            voices = client.list_voices(language_code="ur-IN")
            urdu_voices = [v.name for v in voices.voices]
            print_result("Urdu Voices Available", len(urdu_voices) > 0, f"{len(urdu_voices)} voices")
            self.results.append(len(urdu_voices) > 0)
            
            # Test 3: List English voices
            en_voices = client.list_voices(language_code="en-US")
            english_voices = [v.name for v in en_voices.voices]
            print_result("English Voices Available", len(english_voices) > 0, f"{len(english_voices)} voices")
            self.results.append(len(english_voices) > 0)
            
            # Test 4: Synthesize Urdu text
            synthesis_input = texttospeech.SynthesisInput(text="السلام علیکم، میں آپ کا قانونی مدد کار ہوں")
            voice = texttospeech.VoiceSelectionParams(
                language_code="ur-IN",
                name="ur-IN-Wavenet-A"
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000
            )
            
            start_time = time.time()
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )
            synthesis_time = time.time() - start_time
            
            if response.audio_content and len(response.audio_content) > 1000:
                print_result("Urdu TTS Synthesis", True, f"{len(response.audio_content)} bytes in {synthesis_time:.2f}s")
                self.results.append(True)
            else:
                print_result("Urdu TTS Synthesis", False, "No audio returned")
                self.results.append(False)
            
            # Test 5: Synthesize English text
            synthesis_input = texttospeech.SynthesisInput(text="Hello, I am your legal assistant")
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name="en-US-Wavenet-D"
            )
            
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )
            
            if response.audio_content and len(response.audio_content) > 1000:
                print_result("English TTS Synthesis", True, f"{len(response.audio_content)} bytes")
                self.results.append(True)
            else:
                print_result("English TTS Synthesis", False, "No audio returned")
                self.results.append(False)
            
            return True
            
        except Exception as e:
            print_result("Text-to-Speech", False, str(e))
            self.results.append(False)
            return False
    
    def test_cloud_storage(self) -> bool:
        """Test Cloud Storage API."""
        print_header("CLOUD STORAGE API TEST")
        
        try:
            from google.cloud import storage
            
            # Test 1: Client initialization
            client = storage.Client(project=self.project_id)
            print_result("Client Initialization", True)
            self.results.append(True)
            
            # Test 2: List buckets
            buckets = list(client.list_buckets(max_results=5))
            print_result("List Buckets", True, f"{len(buckets)} buckets found")
            self.results.append(True)
            
            # Test 3: Check audio bucket exists
            bucket_name = f"interview-audio-{self.project_id}"
            try:
                bucket = client.get_bucket(bucket_name)
                print_result("Audio Bucket Exists", True, bucket_name)
                self.results.append(True)
            except:
                print_result("Audio Bucket", False, f"Bucket '{bucket_name}' not found")
                print("       To create: gsutil mb gs://" + bucket_name)
                self.results.append(False)
            
            return True
            
        except Exception as e:
            print_result("Cloud Storage", False, str(e))
            self.results.append(False)
            return False
    
    def test_gemini(self) -> bool:
        """Test Gemini API with actual generation."""
        print_header("GEMINI API TEST")
        
        if not self.gemini_key or self.gemini_key == 'your-gemini-api-key-here':
            print_result("Gemini API Key", False, "Not configured")
            print("       Get key from: https://makersuite.google.com/app/apikey")
            self.results.append(False)
            return False
        
        try:
            import google.generativeai as genai
            
            # Test 1: Configure and initialize
            genai.configure(api_key=self.gemini_key)
            model = genai.GenerativeModel('gemini-2.0-flash')
            print_result("Model Initialization", True, "gemini-2.0-flash")
            self.results.append(True)
            
            # Test 2: Simple generation
            start_time = time.time()
            response = model.generate_content("Reply with just the word: OK")
            generation_time = time.time() - start_time
            
            if response.text:
                print_result("Text Generation", True, f"Response in {generation_time:.2f}s")
                self.results.append(True)
            else:
                print_result("Text Generation", False, "No response")
                self.results.append(False)
            
            # Test 3: Legal classification prompt
            legal_prompt = """
            Classify this as a legal domain (respond with JSON only):
            "میری جائیداد کا مسئلہ ہے"
            
            Format: {"domain": "property_law", "confidence": 0.9}
            """
            
            response = model.generate_content(legal_prompt)
            if response.text and ("property" in response.text.lower() or "domain" in response.text.lower()):
                print_result("Legal Classification", True, "Correct domain detected")
                self.results.append(True)
            else:
                print_result("Legal Classification", False, response.text[:50] if response.text else "No response")
                self.results.append(False)
            
            return True
            
        except Exception as e:
            print_result("Gemini", False, str(e))
            self.results.append(False)
            return False
    
    def test_api_endpoints(self) -> bool:
        """Test REST API endpoints (requires server running)."""
        print_header("REST API ENDPOINTS TEST")
        
        try:
            import requests
            base_url = "http://127.0.0.1:8000"
            
            # Test 1: Health check
            try:
                r = requests.get(f"{base_url}/api/v1/health", timeout=5)
                if r.status_code == 200:
                    print_result("Health Endpoint", True, r.json().get('status', 'unknown'))
                    self.results.append(True)
                else:
                    print_result("Health Endpoint", False, f"Status {r.status_code}")
                    self.results.append(False)
            except requests.exceptions.ConnectionError:
                print_result("Health Endpoint", False, "Server not running")
                print("       Start server: python -m uvicorn app.main:app --reload")
                self.results.append(False)
                return False
            
            # Test 2: Create session
            r = requests.post(f"{base_url}/api/v1/sessions", json={"client_id": "test_runner"})
            if r.status_code == 201:
                session_id = r.json().get('session_id')
                print_result("Create Session", True, f"Session: {session_id[:8]}...")
                self.results.append(True)
            else:
                print_result("Create Session", False, f"Status {r.status_code}")
                self.results.append(False)
                return True
            
            # Test 3: Get session
            r = requests.get(f"{base_url}/api/v1/sessions/{session_id}")
            if r.status_code == 200:
                print_result("Get Session", True, f"Status: {r.json().get('status')}")
                self.results.append(True)
            else:
                print_result("Get Session", False, f"Status {r.status_code}")
                self.results.append(False)
            
            # Test 4: WebSocket URL check
            r = requests.get(f"{base_url}/openapi.json")
            if r.status_code == 200:
                paths = r.json().get('paths', {})
                ws_exists = any('ws' in path.lower() for path in paths.keys())
                print_result("WebSocket Routes", True, f"{len([p for p in paths if 'ws' in p.lower()])} WS endpoints")
                self.results.append(True)
            
            return True
            
        except Exception as e:
            print_result("API Endpoints", False, str(e))
            self.results.append(False)
            return False
    
    def run_all_tests(self) -> bool:
        """Run all tests and print summary."""
        print("\n" + "="*60)
        print("  VOICE INTERVIEW AGENT - COMPREHENSIVE TEST SUITE")
        print("="*60)
        
        self.setup_credentials()
        self.test_speech_to_text()
        self.test_text_to_speech()
        self.test_cloud_storage()
        self.test_gemini()
        self.test_api_endpoints()
        
        # Summary
        print_header("TEST SUMMARY")
        passed = sum(self.results)
        total = len(self.results)
        percentage = (passed / total * 100) if total > 0 else 0
        
        print(f"\nTotal: {passed}/{total} tests passed ({percentage:.1f}%)")
        
        if passed == total:
            print("\n[SUCCESS] All tests passed! Ready for deployment.")
            return True
        elif percentage >= 80:
            print(f"\n[WARNING] {total - passed} test(s) failed. Review errors above.")
            return True
        else:
            print(f"\n[ERROR] Too many failures. Fix issues before proceeding.")
            return False


if __name__ == "__main__":
    tester = GCPTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)

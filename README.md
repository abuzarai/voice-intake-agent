# Voice Interview Agent

> **Final Year Project — AI Microservice** · Part of the [Insafdaar](https://github.com/abuzarai/insafdaar-webapp) legal case management platform.  
> A cost-optimized, GCP-native service for conducting bilingual (Urdu/English) legal intake interviews with real-time transcription and AI classification.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![WebSockets](https://img.shields.io/badge/WebSockets-Coversational-010101?logo=socket.io&logoColor=white)](https://websockets.readthedocs.io/)
[![GCP STT](https://img.shields.io/badge/GCP-Speech--to--Text-4285F4?logo=google&logoColor=white)](https://cloud.google.com/speech-to-text)

---

## 📖 What Is This?

This microservice conducts structured legal intake interviews over WebSocket audio. It guides clients through a bilingual (Urdu/English) conversation — asking questions, transcribing responses via GCP Speech-to-Text, classifying the legal domain and urgency using Gemini 2.5 Flash via Vertex AI, and forwarding the structured result to the Express backend via webhook.

It powers the **Voice Intake** feature inside the main Insafdaar webapp — clients can speak their case details naturally instead of filling long forms.

---

## 🏗️ Architecture

```
Browser (Microphone)
     │
     ▼  WebSocket (wss://)
┌──────────────────────────────────────────────────────────────────┐
│                   VOICE INTERVIEW AGENT (:8000)                   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │            CONVERSATIONAL WEBSOCKET (primary)             │    │
│  │                                                          │    │
│  │  1. Agent speaks greeting (TTS Urdu/English)              │    │
│  │  2. Client responds with audio chunks                     │    │
│  │  3. GCP Speech-to-Text transcribes (ur-PK / en-US)       │    │
│  │  4. Conversation Manager (Gemini-driven) determines       │    │
│  │     next question or signals completion                   │    │
│  │  5. Agent speaks next question via TTS                    │    │
│  │  6. Repeat until all info gathered                        │    │
│  │  7. Gemini produces LegalAnalysis (domain, urgency,       │    │
│  │     entities, summary)                                    │    │
│  │  8. Audio uploaded to GCS (7-day retention)               │    │
│  │  9. Webhook POST to Express backend                       │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │            LISTEN-ONLY WEBSOCKET (legacy)                 │    │
│  │   Client records full audio → sends → receives results    │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │            REST API                                       │    │
│  │   POST /api/v1/sessions       → Create session           │    │
│  │   GET  /api/v1/sessions/{id}  → Get results              │    │
│  │   GET  /api/v1/health         → Health check             │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
     │                    │                    │
     ▼                    ▼                    ▼
┌────────┐        ┌────────────┐        ┌──────────┐
│ GCP    │        │  Gemini    │        │  GCS     │
│ STT    │        │  2.5 Flash │        │  Audio   │
│(ur-PK/ │        │  (Vertex)  │        │  (7-day  │
│ en-US) │        │            │        │  retent) │
└────────┘        └─────┬──────┘        └──────────┘
                        │
                   ┌────┴────┐
                   │ Webhook │
                   │  POST   │
                   └────┬────┘
                        │
                   ┌────┴────┐
                   │ Express │
                   │ Backend │
                   └─────────┘
```

---

<details>
<summary>API Reference</summary>

### REST Endpoints

#### `POST /api/v1/sessions`

Create a new interview session.

**Request:**
```json
{
  "client_id": "user_123",
  "metadata": { "case_id": 44 }
}
```

**Response** (201):
```json
{
  "session_id": "abc123def456",
  "ws_url": "wss://host/api/v1/ws/abc123def456",
  "created_at": "2026-01-27T12:00:00",
  "expires_at": "2026-01-27T13:00:00",
  "status": "pending"
}
```

#### `GET /api/v1/sessions/{session_id}`

Retrieve interview results after completion.

**Response:**
```json
{
  "session_id": "abc123def456",
  "status": "completed",
  "transcript": [
    {"role": "agent", "message": "Apna naam batayein?", "timestamp": "..."},
    {"role": "user", "message": "Mera naam Ali hai", "language": "ur-PK", "timestamp": "..."}
  ],
  "analysis": {
    "primary_language": "urdu",
    "legal_domain": "property_law",
    "confidence_score": 0.92,
    "key_entities": {
      "parties": ["Ali Ahmed", "Sarfraz Khan"],
      "locations": ["Lahore"],
      "dates": ["January 2025"],
      "amounts": ["5,00,000"]
    },
    "issue_summary": "Property boundary dispute between neighbours in Lahore",
    "case_title_en": "Ali Ahmed vs. Sarfraz Khan",
    "case_title_ur": "علی احمد بمقابلہ سرفراز خان",
    "adr_suitable": true,
    "adr_reasoning": "Dispute involves neighbours with ongoing relationship",
    "urgency": "medium",
    "urgency_reasoning": "No immediate court deadline mentioned"
  },
  "audio_duration_seconds": 180,
  "audio_url": "https://storage.googleapis.com/.../abc123def456.wav?signature=...",
  "created_at": "2026-01-27T12:00:00",
  "completed_at": "2026-01-27T12:03:00"
}
```

#### `GET /api/v1/health`

**Response:** `{"status": "healthy", "service": "voice-interview-agent", "version": "0.1.0", "timestamp": "..."}`

#### `GET /`

**Response:** `{"service": "Voice Interview Agent", "version": "0.1.0", "status": "running"}`

#### `GET /test`

Serves an interactive HTML test UI for the conversational WebSocket.

---

### WebSocket Protocol (Conversational — Primary)

**Endpoint:** `GET /api/v1/ws/{session_id}`

The agent conducts a back-and-forth conversation:

```
AGENT: [TTS audio + text] "Apna naam batayein?" (What is your name?)
CLIENT: [audio chunks] → [user_finished signal]
SERVER: transcribes → Gemini decides next question
AGENT: [TTS audio + text] "Apni masla ki type batayein?" (Describe your issue)
... continues until interview is complete ...
AGENT: "Thank you, your interview is complete."
SERVER: returns results, sends webhook
```

**Client → Server Messages:**

| Type | Format | Description |
|------|--------|-------------|
| `audio` | `{"type":"audio","audio":"<base64>","sequence":1}` | Stream audio chunks (WebM Opus) |
| `user_finished` | `{"type":"user_finished"}` | Signal end of current turn |
| `end_interview` | `{"type":"end_interview"}` | End interview early |

**Server → Client Messages:**

| Type | Format | Description |
|------|--------|-------------|
| `agent_speech` | `{"type":"agent_speech","audio":"<base64>","text":"...","language":"ur","sequence":1}` | TTS-generated agent speech |
| `transcript` | `{"type":"transcript","text":"...","is_final":true,"language":"ur-PK","confidence":0.95}` | Real-time transcription |
| `conversation_status` | `{"type":"conversation_status","agent_state":"speaking|listening|thinking|complete","can_speak":true,"message_en":"...","message_ur":"..."}` | State indicator |
| `results` | `{"type":"results","session_id":"...","transcript":[...],"analysis":{...}}` | Final results on completion |
| `error` | `{"type":"error","message_en":"...","message_ur":"...","code":"SESSION_EXPIRED"}` | Error |
| `status` | `{"type":"status","message_en":"...","message_ur":"..."}` | Status update |

---

### WebSocket Protocol (Listen-Only — Legacy)

---

## 🤖 Core Services

### Conversation Manager (`conversation_service.py`)

The Gemini-driven conversation flow engine:

1. **17 predefined bilingual questions** covering: name, issue description, timeline, parties, location, financial impact, prior action, urgency, documentation, witnesses, desired outcome, police involvement, mediation attempts
2. **Gemini-powered follow-up**: After each user response, Gemini decides:
   - What information was extracted (`extracted_info`)
   - Whether clarification is needed (`needs_clarification`)
   - Which question to ask next or whether the interview is complete
   - Custom rephrased questions based on context
3. **Fallback sequence**: If Gemini fails to respond, falls back to a predefined question order
4. **Completion criteria**: Minimum 3 info fields extracted, must have name + issue type
5. **Max 20 questions** cap

### Speech-to-Text (`stt_service.py`)

- **Primary language**: Urdu (Pakistan) — `ur-PK`
- **Fallback language**: English (US) — `en-US`
- **Format**: WebM Opus → converted to LINEAR16 PCM via ffmpeg
- **Config**: 48kHz sample rate, automatic punctuation
- **Dual mode**: `streaming_recognize()` (for real-time) and `recognize_audio()` (for complete turn)

### Text-to-Speech (`tts_service.py`)

| Language | Voice | Gender |
|----------|-------|--------|
| Urdu (ur-IN, ur-PK, ur) | `ur-IN-Wavenet-A` | Female |
| English (en-US, en) | `en-US-Wavenet-D` | Male |

- Generates MP3 audio at configurable speaking rate (0.5–2.0)
- Returns base64-encoded audio for WebSocket delivery

### Gemini Service (`gemini_service.py`)

- **Model**: `gemini-2.5-flash` via Vertex AI
- **Two modes**:
  - `analyze_transcript()` — Final legal classification with full `LegalAnalysis` output
  - `generate_json_response()` — Conversation control (next question, extracted info, completion check)
- **Temperature**: 0.2 (analysis), 0.3 (conversation)
- **JSON extraction**: Strips markdown fences, brace-matching fallback

### Question Bank (`question_bank.py`)

17 bilingual questions covering the full legal intake scope:

| Key | English | Urdu |
|-----|---------|------|
| `greeting` | "Apna naam batayein?" | "What is your name?" |
| `issue_description` | "Apni masla ki type batayein?" | "Describe your legal issue" |
| `timeline_start` | "Ye masla kab shuru hua?" | "When did this issue start?" |
| `parties_involved` | "Is maslay mein aur kon log shamil hain?" | "Who else is involved?" |
| ... | *(12 more questions)* | ... |
| `acknowledgment_noted` | "Note kar liya, aur kuch?" | "Noted, what else?" |

### Session Manager (`session_service.py`)

- In-memory session store with UUID v4 keys
- 60-minute session timeout (auto-expiry with cleanup)
- Audio chunk storage with size guardrails (~2.4MB max per session)
- Transcript and analysis persistence

### Storage Service (`storage_service.py`)

- Google Cloud Storage with auto-bucket creation
- 7-day lifecycle delete rule on all objects
- Audio uploaded as `interviews/{session_id}.wav`
- Signed URLs for secure temporary access

### Webhook Service (`webhook_service.py`)

- POSTs structured `InterviewResult` to Express backend
- `X-Webhook-Secret` header for authentication
- 3 retries with exponential backoff (2s, 4s, 8s)

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|-----------|
| **Framework** | FastAPI + Uvicorn + WebSockets |
| **Speech-to-Text** | Google Cloud Speech-to-Text (bilingual ur-PK/en-US) |
| **Text-to-Speech** | Google Cloud Text-to-Speech (Urdu Wavenet-A / English Wavenet-D) |
| **AI Classification** | Gemini 2.5 Flash via Vertex AI |
| **Conversation Engine** | Gemini-driven with 17-question bilingual bank |
| **Audio Storage** | Google Cloud Storage (7-day lifecycle, signed URLs) |
| **Webhook** | httpx async with retry (3 attempts, exponential backoff) |
| **Deployment** | Google Cloud Run (GitHub-connected auto-deploy) |
| **Language** | Python 3.11 |

---

## 🚀 Local Development

### Prerequisites

- Python 3.11+
- GCP service account with Speech-to-Text, Text-to-Speech, Vertex AI, and Storage access
- `ffmpeg` installed locally

### Setup

```bash
# Clone
git clone https://github.com/abuzarai/voice-intake-agent.git
cd voice-intake-agent

# Virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Environment
cp .env.example .env
# Edit .env with your GCP project ID
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT_ID` | Yes | — | GCP project ID |
| `GCP_CREDENTIALS_PATH` | No | — | Path to service account key |
| `SESSION_TIMEOUT_MINUTES` | No | `60` | Session expiry timeout |
| `MAX_AUDIO_DURATION_SECONDS` | No | `600` | Max audio per session |
| `AUDIO_RETENTION_DAYS` | No | `7` | GCS audio retention |
| `CORS_ORIGINS` | No | `http://localhost:3000` | Comma-separated CORS origins |
| `EXPRESS_WEBHOOK_URL` | No | — | Express backend webhook URL |
| `EXPRESS_WEBHOOK_SECRET` | No | — | Shared webhook secret |
| `AUDIO_STORAGE_BUCKET` | No | `interview-audio-{project}` | GCS bucket name |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `ENVIRONMENT` | No | `development` | `development` or `production` |

### Run

```bash
python -m uvicorn app.main:app --reload
```

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- Test UI: `http://localhost:8000/test`

### Test

```bash
# REST API tests
pytest tests/test_rest_sessions_generated.py -v

# Comprehensive suite
pytest tests/test_comprehensive.py -v

# Quick smoke tests
pytest tests/test_quick.py -v
```

---

## 🐳 Docker

```bash
docker build -t voice-intake-agent .
docker run --rm -p 8000:8000 --env-file .env voice-intake-agent
```

---

## 📂 Repository Structure

```
voice-intake-agent/
├── app/
│   ├── main.py                       # FastAPI entrypoint, CORS, routes
│   ├── config.py                     # Pydantic settings (all env vars)
│   ├── api/
│   │   ├── rest.py                   # POST/GET sessions, health
│   │   └── websocket_conversational.py  # Main conversational WebSocket
│   ├── middleware/
│   │   └── request_logging.py        # Request/response logging
│   ├── models/
│   │   ├── enums.py                  # LegalDomain, Language, Status, Urgency
│   │   ├── schemas.py                # Pydantic request/response models
│   │   └── conversation.py           # Conversation state, turn models
│   ├── services/
│   │   ├── gemini_service.py         # Vertex AI Gemini (analysis + conversation)
│   │   ├── stt_service.py            # GCP Speech-to-Text (bilingual)
│   │   ├── tts_service.py            # GCP Text-to-Speech (Urdu/English)
│   │   ├── session_service.py        # In-memory session & audio management
│   │   ├── storage_service.py        # GCS bucket with lifecycle rules
│   │   ├── webhook_service.py        # Express backend webhook with retry
│   │   ├── conversation_service.py   # Gemini-driven conversation manager
│   │   └── question_bank.py          # 17 bilingual intake questions
│   └── utils/
│       ├── audio.py                  # Base64/PCM conversion, quality check
│       └── logger.py                 # Bilingual (en/ur) structured logger
├── tests/
│   ├── test_rest_sessions_generated.py  # FastAPI TestClient tests
│   ├── test_comprehensive.py         # Full suite (REST, services, models, WS)
│   ├── test_quick.py                 # Smoke tests
│   ├── test_api.py                   # Manual HTTP tests
│   └── test_websocket.py             # Async WebSocket test
├── docs/
│   ├── gcp-setup.md                  # Full GCP project setup guide
│   └── integration-guide.md          # Express + React integration reference
├── scripts/
├── test_ui.html                      # Interactive browser test UI
├── Dockerfile                        # Cloud Run build
└── requirements.txt                  # Python dependencies
```

---

## 📝 License

Licensed under the [Apache License 2.0](LICENSE).  

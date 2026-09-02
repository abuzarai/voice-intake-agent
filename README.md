# Voice Interview Agent

> Final Year Project, AI microservice · Part of the [Insafdaar](https://github.com/abuzarai/insafdaar-webapp) legal case management platform.  
> A cost-efficient bilingual (Urdu/English) legal intake service conducting structured interviews over WebSocket audio with real-time transcription and AI classification.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![WebSockets](https://img.shields.io/badge/WebSockets-Coversational-010101?logo=socket.io&logoColor=white)](https://websockets.readthedocs.io/)
[![Gemini](https://img.shields.io/badge/Gemini-API-4285F4?logo=google&logoColor=white)](https://ai.google.dev)

---

## What Is This?

This microservice runs structured legal intake interviews over WebSocket audio. The agent speaks bilingual questions (Urdu/English), transcribes client replies with the Gemini API, classifies the legal domain and urgency, and forwards the structured result to the Express backend via webhook.

It powers the **Voice Intake** feature inside the main Insafdaar webapp. Clients can speak their case details instead of filling long forms.

---

## Architecture

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
│  │  1. Agent speaks greeting (edge-tts Urdu/English)        │    │
│  │  2. Client responds with audio chunks                     │    │
│  │  3. Gemini transcribes audio (bilingual ur-PK/en-US)     │    │
│  │  4. Conversation Manager (Gemini-driven) determines       │    │
│  │     next question or signals completion                   │    │
│  │  5. Agent speaks next question via edge-tts               │    │
│  │  6. Repeat until all info gathered                        │    │
│  │  7. Gemini produces LegalAnalysis (domain, urgency,       │    │
│  │     entities, summary)                                    │    │
│  │  8. Audio saved locally (7-day retention cleanup)         │    │
│  │  9. Webhook POST to Express backend                       │    │
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
┌────────────┐    ┌────────────┐        ┌───────────────┐
│   Gemini   │    │  Gemini    │        │  Local audio  │
│  (audio →  │    │  2.5 Flash │        │  storage      │
│  text)     │    │  (API key) │        │  (7-day       │
│ (ur-PK/    │    │            │        │  cleanup)     │
│  en-US)    │    └─────┬──────┘        └───────────────┘
└────────────┘          │
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

Transcription, classification, and conversation control all use a single Gemini API key. There are no separate speech services, local models, or cloud storage dependencies.

---

<details>
<summary>API Reference</summary>

### REST Endpoints

#### `POST /api/v1/sessions`

Create a new interview session (per-IP rate limited).

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
  "audio_file": "interviews/abc123def456.webm",
  "created_at": "2026-01-27T12:00:00",
  "completed_at": "2026-01-27T12:03:00"
}
```

#### `GET /api/v1/health`

**Response:** `{"status": "healthy", "service": "voice-interview-agent", "version": "0.1.0", "timestamp": "..."}`

#### `GET /`

**Response:** `{"service": "Voice Interview Agent", "version": "0.1.0", "status": "running"}`

---

### WebSocket Protocol (Conversational)

**Endpoint:** `GET /api/v1/ws/{session_id}`

The agent conducts a back-and-forth conversation:

```
AGENT: [TTS audio + text] "Apna naam batayein?" (What is your name?)
CLIENT: [audio chunks] → [user_finished signal]
SERVER: Gemini transcribes → Conversation Manager decides next question
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

</details>

---

## Core Services

### Conversation Manager

The Gemini-driven conversation flow engine:

1. **17 predefined bilingual questions** covering: name, issue description, timeline, parties, location, financial impact, prior action, urgency, documentation, witnesses, desired outcome, police involvement, mediation attempts
2. **Gemini-powered follow-up**: after each user response, Gemini decides:
   - What information was extracted (`extracted_info`)
   - Whether clarification is needed (`needs_clarification`)
   - Which question to ask next or whether the interview is complete
   - Custom rephrased questions based on context
3. **Fallback sequence**: If Gemini fails to respond, falls back to a predefined question order
4. **Completion criteria**: Minimum 3 info fields extracted, must have name + issue type
5. **Max 20 questions** cap

### Transcription

- **Gemini audio transcription** (genai client, same API key). No local models or separate speech services are involved.
- **Primary language**: Urdu (Pakistan), `ur-PK`
- **Fallback language**: English (US), `en-US`
- **Format**: WebM Opus → converted to PCM via ffmpeg before transcription
- Measured ~0.16 WER on legal Urdu

### Text-to-Speech

edge-tts (Microsoft neural voices):

| Language | Voice | Gender |
|----------|-------|--------|
| Urdu (ur, ur-PK, ur-IN) | `ur-PK-UzmaNeural` | Female |
| English (en, en-US) | `en-US-AndrewNeural` | Male |

- Generates MP3 audio at configurable speaking rate (0.5 to 2.0)
- Returns base64-encoded audio for WebSocket delivery

### Gemini Service

- **Model**: `gemini-2.5-flash` via the Gemini API (API key, no GCP project)
- **Two modes**:
  - `analyze_transcript()`: final legal classification with full `LegalAnalysis` output
  - `generate_json_response()`: conversation control (next question, extracted info, completion check)
- **Temperature**: 0.2 (analysis), 0.3 (conversation)
- **JSON extraction**: Strips markdown fences, brace-matching fallback; retries with backoff on transient failures

### Question Bank

17 bilingual questions covering the full legal intake scope:

| Key | English | Urdu |
|-----|---------|------|
| `greeting` | "Apna naam batayein?" | "What is your name?" |
| `issue_description` | "Apni masla ki type batayein?" | "Describe your legal issue" |
| `timeline_start` | "Ye masla kab shuru hua?" | "When did this issue start?" |
| `parties_involved` | "Is maslay mein aur kon log shamil hain?" | "Who else is involved?" |
| ... | *(12 more questions)* | ... |
| `acknowledgment_noted` | "Note kar liya, aur kuch?" | "Noted, what else?" |

### Session Manager

- In-memory session store with UUID v4 keys
- 60-minute session timeout (auto-expiry with cleanup)
- Audio chunk storage with size guardrails (~2.4MB max per session)
- Transcript and analysis persistence

### Storage Service

- **Local filesystem** under `AUDIO_DIR` (no cloud storage)
- 7-day retention with a nightly cleanup job
- Audio saved as `interviews/{session_id}.webm` with metadata JSON

### Webhook Service

- POSTs structured `InterviewResult` to the Express backend
- `X-Webhook-Secret` header for authentication (fail-closed)
- Retries with exponential backoff (2s, 4s, 8s); results havehed for idempotent delivery

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| **Framework** | FastAPI + Uvicorn + WebSockets |
| **Transcription** | Gemini API audio transcription (bilingual ur-PK/en-US) |
| **Text-to-Speech** | edge-tts `ur-PK-UzmaNeural` / `en-US-AndrewNeural` |
| **AI Classification** | Gemini 2.5 Flash via the Gemini API |
| **Conversation Engine** | Gemini-driven with 17-question bilingual bank |
| **Audio Storage** | Local filesystem (7-day retention, nightly cleanup) |
| **Webhook** | httpx async with retry (3 attempts, exponential backoff) |
| **Deployment** | Container in the Insafdaar compose stack (Oracle Cloud Infrastructure) |
| **Language** | Python 3.11 |

---

## Local Development

### Prerequisites

- Python 3.11+
- Gemini API key ([AI Studio](https://aistudio.google.com/apikey))
- `ffmpeg` installed locally

### Setup

```bash
# Clone
git clone https://github.com/abuzarai/voice-intake-agent.git
cd voice-intake-agent

# Install dependencies from the locked graph (pyproject.toml + uv.lock)
uv sync

# Environment
cp .env.example .env
# Edit .env with your Gemini API key
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | Yes | None | Gemini API key (transcription + classification) |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model name |
| `ENVIRONMENT` | No | `development` | `development` or `production` |
| `SESSION_TIMEOUT_MINUTES` | No | `60` | Session expiry timeout |
| `MAX_AUDIO_DURATION_SECONDS` | No | `600` | Max audio per session |
| `AUDIO_RETENTION_DAYS` | No | `7` | Local audio retention |
| `AUDIO_DIR` | No | `./data/audio` | Local audio storage directory |
| `CORS_ORIGINS` | No | `http://localhost:3000` | Comma-separated CORS origins |
| `EXPRESS_WEBHOOK_URL` | No | None | Express backend webhook URL |
| `EXPRESS_WEBHOOK_SECRET` | No | None | Shared webhook secret |
| `LOG_LEVEL` | No | `INFO` | Logging level |

### Run

```bash
uv run uvicorn app.main:app --reload
```

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs` *(development builds only)*
- Test UI: `http://localhost:8000/test` *(development builds only)*

### Test

```bash
# REST API tests
uv run pytest tests/test_rest_sessions_generated.py -v

# Comprehensive suite
uv run pytest tests/test_comprehensive.py -v

# Quick smoke tests
uv run pytest tests/test_quick.py -v
```

---

## Docker

```bash
docker build -t voice-intake-agent .
docker run --rm -p 8000:8000 --env-file .env voice-intake-agent
```

---

## Deployment

- **Platform**: container in the Insafdaar compose stack (Oracle Cloud Infrastructure); loopback-published and reached only through the host's TLS proxy
- **Auth**: webhooks carry the shared `X-Webhook-Secret`; session creation and WebSocket handshakes are per-IP rate limited; docs/test UI are disabled in production builds
- **Deploys**: handled by the main webapp's pipeline (GitHub Actions builds the image on a runner, ships it, and applies the stack)

---

## Repository Structure

```
voice-intake-agent/
├── app/
│   ├── main.py                       # FastAPI entrypoint, CORS, routes
│   ├── config.py                     # Pydantic settings (all env vars)
│   ├── api/
│   │   ├── rest.py                   # POST/GET sessions, health
│   │   └── websocket_conversational.py  # Conversational WebSocket
│   ├── middleware/
│   │   ├── request_logging.py        # Request/response logging
│   │   └── rate_limit.py             # Sliding-window per-IP limiter
│   ├── models/
│   │   ├── enums.py                  # LegalDomain, Language, Status, Urgency
│   │   ├── schemas.py                # Pydantic request/response models
│   │   └── conversation.py           # Conversation state, turn models
│   ├── services/
│   │   ├── gemini_service.py         # Gemini API (analysis + conversation)
│   │   ├── stt_service.py            # Gemini audio transcription (bilingual)
│   │   ├── tts_service.py            # edge-tts (Urdu/English)
│   │   ├── session_service.py        # In-memory session & audio management
│   │   ├── storage_service.py        # Local audio storage + cleanup
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
│   ├── gcp-setup.md                  # GCP-era setup guide (legacy)
│   └── integration-guide.md          # Express + React integration reference
├── scripts/
├── test_ui.html                      # Interactive browser test UI (dev only)
├── Dockerfile                        # Container build
├── pyproject.toml                    # Dependencies & project metadata
└── uv.lock                           # Locked dependency graph
```

---

## License

Licensed under the [Apache License 2.0](LICENSE).
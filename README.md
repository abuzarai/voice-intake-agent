# Voice Interview Agent

> **Final Year Project — AI Microservice** · Part of the [Insafdaar](https://github.com/abuzarai/insafdaar-webapp) legal case management platform.  
> A cost-optimized, GCP-native microservice for conducting bilingual (Urdu/English) legal intake interviews with real-time transcription and AI-powered classification.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![WebSockets](https://img.shields.io/badge/WebSockets-Realtime-010101?logo=socket.io&logoColor=white)](https://websockets.readthedocs.io/)
[![GCP STT](https://img.shields.io/badge/GCP-Speech--to--Text-4285F4?logo=google&logoColor=white)](https://cloud.google.com/speech-to-text)

---

## 📖 What Is This?

This microservice conducts structured legal intake interviews over WebSocket audio. It streams audio from the browser, transcribes it in real-time using GCP Speech-to-Text (bilingual Urdu/English), classifies the legal domain and urgency using Gemini 2.5 Flash via Vertex AI, and forwards the structured result to the Express backend via webhook.

It powers the **Voice Intake** feature inside the main Insafdaar webapp — clients can speak their case details instead of filling long forms.

---

## 🏗️ Architecture

```
Browser (Mic) ──WebSocket──► Voice Agent
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
            GCP STT        Gemini 2.5     GCS Audio
          (real-time)    (classification)  (7-day retention)
                    │            │
                    └────────────┘
                         │
                    Webhook POST
                         ▼
               Express Backend
```

---

## ✨ Features

- **Real-Time Audio Streaming** — WebSocket-based audio capture and transcription
- **Bilingual Transcription** — Urdu and English via GCP Speech-to-Text (dynamic language detection)
- **AI Classification** — Legal domain, urgency, and case type via Gemini 2.5 Flash
- **Entity Extraction** — Automatic extraction of parties, locations, dates, amounts
- **Structured Output** — JSON format for seamless Express backend integration
- **Webhook Delivery** — Real-time results pushed to the main app
- **Audio Storage** — 7-day retention with auto-cleanup on GCS
- **Conversational Flow** — Gemini-driven question-asking for complete intake

---

## 🛠️ Tech Stack

- **Framework**: FastAPI + WebSockets
- **Speech-to-Text**: Google Cloud Speech-to-Text API
- **AI Classification**: Gemini 2.5 Flash via Vertex AI
- **Storage**: Google Cloud Storage (7-day lifecycle)
- **Deployment**: Google Cloud Run
- **Language**: Python 3.11

---

## 🚀 Local Development

### Prerequisites

- Python 3.11+
- GCP service account with STT, Vertex AI, and Storage access
- Modern browser (for the test UI)

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
# Edit .env with your GCP project ID and credentials
```

### Run

```bash
python -m uvicorn app.main:app --reload
```

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- Test UI: `http://localhost:8000/test`

---

## 🐳 Docker

```bash
docker build -t voice-intake-agent .
docker run --rm -p 8000:8000 --env-file .env voice-intake-agent
```

---

## 📁 Repository Structure

```
voice-intake-agent/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── config.py            # Pydantic settings
│   ├── api/
│   │   ├── rest.py          # REST endpoints
│   │   ├── websocket.py     # Legacy listen-only WebSocket
│   │   └── websocket_conversational.py  # Main conversational WebSocket
│   ├── services/
│   │   ├── gemini_service.py          # Vertex AI Gemini calls
│   │   ├── stt_service.py            # GCP Speech-to-Text
│   │   ├── tts_service.py            # GCP Text-to-Speech
│   │   ├── conversation_service.py   # Gemini-driven conversation flow
│   │   ├── question_bank.py          # Bilingual intake questions
│   │   ├── session_service.py        # In-memory session management
│   │   ├── storage_service.py        # GCS audio storage
│   │   └── webhook_service.py        # Express backend webhook
│   └── models/
│       ├── schemas.py       # Pydantic request/response models
│       └── conversation.py  # Conversation state models
├── tests/
└── docs/
    ├── gcp-setup.md
    └── integration-guide.md
```

---

## 📝 License

Licensed under the [Apache License 2.0](LICENSE).  

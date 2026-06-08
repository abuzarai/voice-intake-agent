# Integration Guide - Express Backend & React Frontend

This guide shows how to integrate the Voice Interview Agent into your Express/React case management platform.

## Architecture Overview

```
React Frontend → Express Backend → Voice Interview Service
                     ↑                      ↓
                     └──── Webhook ─────────┘
```

## Express Backend Integration

### 1. Install Dependencies

```bash
npm install axios
```

### 2. Create Interview Routes

```javascript
// routes/interviews.js
const express = require('express');
const axios = require('axios');
const router = express.Router();

const VOICE_SERVICE_URL = process.env.VOICE_SERVICE_URL || 'http://localhost:8000';

// Start new interview session
router.post('/start-interview', async (req, res) => {
  try {
    const { clientId } = req.body;
    
    // Create session in voice service
    const response = await axios.post(`${VOICE_SERVICE_URL}/api/v1/sessions`, {
      client_id: clientId,
      metadata: {
        case_manager: req.user?.name,
        created_from: 'web_app'
      }
    });
    
    // Store session in database
    await db.query(
      'INSERT INTO interview_sessions (id, client_id, ws_url, status, created_by) VALUES ($1, $2, $3, $4, $5)',
      [response.data.session_id, clientId, response.data.ws_url, 'pending', req.user.id]
    );
    
    res.json({
      sessionId: response.data.session_id,
      wsUrl: response.data.ws_url
    });
  } catch (error) {
    console.error('Error starting interview:', error);
    res.status(500).json({
      error: 'Failed to start interview',
      message_ur: 'انٹرویو شروع کرنے میں ناکامی'
    });
  }
});

// Get interview results
router.get('/interview-results/:sessionId', async (req, res) => {
  try {
    const { sessionId } = req.params;
    
    // Fetch from voice service
    const response = await axios.get(`${VOICE_SERVICE_URL}/api/v1/sessions/${sessionId}`);
    
    res.json(response.data);
  } catch (error) {
    console.error('Error fetching results:', error);
    res.status(500).json({ error: 'Failed to fetch results' });
  }
});

module.exports = router;
```

### 3. Create Webhook Endpoint

```javascript
// routes/webhooks.js
const express = require('express');
const router = express.Router();

const WEBHOOK_SECRET = process.env.VOICE_WEBHOOK_SECRET;

// Webhook to receive interview results
router.post('/interview-complete', async (req, res) => {
  try {
    // Verify webhook secret
    const receivedSecret = req.headers['x-webhook-secret'];
    if (receivedSecret !== WEBHOOK_SECRET) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    
    const {
      session_id,
      client_id,
      transcript,
      analysis,
      audio_url,
      audio_duration_seconds,
      completed_at
    } = req.body;
    
    // Update database
    await db.query(
      `UPDATE interview_sessions 
       SET status = $1, transcript = $2, analysis = $3, audio_url = $4, 
           audio_duration = $5, completed_at = $6
       WHERE id = $7`,
      ['completed', transcript, JSON.stringify(analysis), audio_url, 
       audio_duration_seconds, completed_at, session_id]
    );
    
    // Trigger lawyer matching (your existing logic)
    await matchLawyerToClient(client_id, analysis);
    
    // Send notification to client
    await sendClientNotification(client_id, {
      message: 'Interview complete! We are matching you with a lawyer.',
      message_ur: 'انٹرویو مکمل! ہم آپ کو وکیل سے ملا رہے ہیں۔'
    });
    
    res.json({ status: 'success' });
  } catch (error) {
    console.error('Webhook error:', error);
    res.status(500).json({ error: 'Webhook processing failed' });
  }
});

module.exports = router;
```

### 4. Add Routes to Express App

```javascript
// app.js
const interviewRoutes = require('./routes/interviews');
const webhookRoutes = require('./routes/webhooks');

app.use('/api/interviews', interviewRoutes);
app.use('/api/webhooks', webhookRoutes);
```

### 5. Update Environment Variables

```env
# .env
VOICE_SERVICE_URL=https://voice-interview-agent-xyz.run.app
VOICE_WEBHOOK_SECRET=your-secure-random-secret
```

---

## React Frontend Integration

### 1. Create Interview Component

```javascript
// components/VoiceInterview.jsx
import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';

export function VoiceInterview({ clientId, onComplete }) {
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [status, setStatus] = useState('');
  const [results, setResults] = useState(null);
  
  const wsRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const sessionIdRef = useRef(null);
  
  const startInterview = async () => {
    try {
      // Create session via Express backend
      const { data } = await axios.post('/api/interviews/start-interview', {
        clientId
      });
      
      sessionIdRef.current = data.sessionId;
      
      // Connect to WebSocket
      wsRef.current = new WebSocket(data.wsUrl);
      
      wsRef.current.onmessage = (event) => {
        const message = JSON.parse(event.data);
        
        switch (message.type) {
          case 'transcript':
            setTranscript(prev => prev + ' ' + message.text);
            break;
          case 'status':
            setStatus(message.message_en);
            break;
          case 'results':
            setResults(message.analysis);
            setIsRecording(false);
            onComplete?.(message);
            break;
          case 'error':
            console.error('Error:', message.message_en);
            alert(message.message_ur || message.message_en);
            break;
        }
      };
      
      wsRef.current.onopen = () => {
        startAudioCapture();
      };
      
    } catch (error) {
      console.error('Failed to start interview:', error);
      alert('Failed to start interview');
    }
  };
  
  const startAudioCapture = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true
        }
      });
      
      // Use MediaRecorder with appropriate format
      mediaRecorderRef.current = new MediaRecorder(stream, {
        mimeType: 'audio/webm;codecs=opus'
      });
      
      mediaRecorderRef.current.ondataavailable = async (event) => {
        if (event.data.size > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
          // Convert to base64
          const reader = new FileReader();
          reader.onload = () => {
            const base64Audio = btoa(
              new Uint8Array(reader.result)
                .reduce((data, byte) => data + String.fromCharCode(byte), '')
            );
            
            wsRef.current.send(JSON.stringify({
              type: 'audio',
              audio: base64Audio
            }));
          };
          reader.readAsArrayBuffer(event.data);
        }
      };
      
      mediaRecorderRef.current.start(100); // Send chunks every 100ms
      setIsRecording(true);
      
    } catch (error) {
      console.error('Microphone error:', error);
      alert('Failed to access microphone');
    }
  };
  
  const stopInterview = () => {
    if (mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current.stream.getTracks().forEach(track => track.stop());
    }
    
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'end_interview' }));
    }
    
    setStatus('Processing interview...');
  };
  
  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stream?.getTracks().forEach(track => track.stop());
      }
    };
  }, []);
  
  return (
    <div className="voice-interview">
      <h2>Voice Interview</h2>
      
      {!isRecording && !results && (
        <button onClick={startInterview} className="btn-primary">
          Start Interview / انٹرویو شروع کریں
        </button>
      )}
      
      {isRecording && (
        <>
          <div className="recording-indicator">
            🔴 Recording...
          </div>
          <button onClick={stopInterview} className="btn-danger">
            End Interview / انٹرویو ختم کریں
          </button>
        </>
      )}
      
      {status && (
        <div className="status-message">
          {status}
        </div>
      )}
      
      {transcript && (
        <div className="transcript-box">
          <h3>Transcript:</h3>
          <p>{transcript}</p>
        </div>
      )}
      
      {results && (
        <div className="results-panel">
          <h3>Interview Complete!</h3>
          <div className="result-item">
            <strong>Legal Domain:</strong> {results.legal_domain}
          </div>
          <div className="result-item">
            <strong>Confidence:</strong> {(results.confidence_score * 100).toFixed(1)}%
          </div>
          <div className="result-item">
            <strong>Summary:</strong> {results.issue_summary}
          </div>
          <div className="result-item">
            <strong>ADR Suitable:</strong> {results.adr_suitable ? 'Yes' : 'No'}
          </div>
          <div className="result-item">
            <strong>Urgency:</strong> {results.urgency}
          </div>
        </div>
      )}
    </div>
  );
}
```

### 2. Add Styling

```css
/* styles/VoiceInterview.css */
.voice-interview {
  max-width: 800px;
  margin: 0 auto;
  padding: 2rem;
}

.recording-indicator {
  background: #ff4444;
  color: white;
  padding: 1rem;
  border-radius: 8px;
  text-align: center;
  margin: 1rem 0;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.transcript-box {
  background: #f5f5f5;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 1rem;
  margin: 1rem 0;
  max-height: 300px;
  overflow-y: auto;
}

.results-panel {
  background: #e8f5e9;
  border: 2px solid #4caf50;
  border-radius: 8px;
  padding: 1.5rem;
  margin-top: 1rem;
}

.result-item {
  margin: 0.5rem 0;
  padding: 0.5rem 0;
  border-bottom: 1px solid #ddd;
}
```

---

## Database Schema

```sql
-- Add to your PostgreSQL schema
CREATE TABLE interview_sessions (
  id UUID PRIMARY KEY,
  client_id INTEGER REFERENCES clients(id),
  ws_url TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  transcript TEXT,
  analysis JSONB,
  audio_url TEXT,
  audio_duration FLOAT,
  created_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP,
  created_by INTEGER REFERENCES users(id)
);

CREATE INDEX idx_interview_client ON interview_sessions(client_id);
CREATE INDEX idx_interview_status ON interview_sessions(status);
CREATE INDEX idx_interview_created ON interview_sessions(created_at);

-- Query examples
-- Get latest interview for a client
SELECT * FROM interview_sessions 
WHERE client_id = $1 
ORDER BY created_at DESC 
LIMIT 1;

-- Get all completed interviews
SELECT * FROM interview_sessions 
WHERE status = 'completed' 
  AND analysis->>'legal_domain' = 'property_law';
```

---

## API Reference

### REST Endpoints

#### Create Session
```http
POST /api/v1/sessions
Content-Type: application/json

{
  "client_id": "optional_client_id",
  "metadata": {
    "case_manager": "John Doe"
  }
}

Response:
{
  "session_id": "uuid",
  "ws_url": "wss://service.run.app/api/v1/ws/{session_id}",
  "created_at": "2026-01-21T16:00:00Z",
  "expires_at": "2026-01-21T17:00:00Z",
  "status": "pending"
}
```

#### Get Session Results
```http
GET /api/v1/sessions/{session_id}

Response:
{
  "session_id": "uuid",
  "status": "completed",
  "transcript": "Full transcript...",
  "analysis": {
    "primary_language": "urdu",
    "legal_domain": "property_law",
    "confidence_score": 0.89,
    "key_entities": {...},
    "issue_summary": "...",
    "adr_suitable": true,
    "urgency": "medium"
  },
  "audio_duration_seconds": 342,
  "audio_url": "https://storage.googleapis.com/...",
  "created_at": "...",
  "completed_at": "..."
}
```

### WebSocket Protocol

#### Client → Server Messages

```json
// Audio chunk
{
  "type": "audio",
  "audio": "base64_encoded_pcm_data",
  "sequence": 123
}

// End interview
{
  "type": "end_interview"
}
```

#### Server → Client Messages

```json
// Live transcript
{
  "type": "transcript",
  "text": "میں نے اپنی جائیداد کا مسئلہ ہے",
  "is_final": false,
  "language": "ur-PK",
  "confidence": 0.95
}

// Status update
{
  "type": "status",
  "message_en": "Processing interview...",
  "message_ur": "انٹرویو پر کارروائی ہو رہی ہے..."
}

// Final results
{
  "type": "results",
  "session_id": "uuid",
  "transcript": "Full transcript",
  "analysis": {...}
}

// Error
{
  "type": "error",
  "message_en": "Error message",
  "message_ur": "خرابی کا پیغام",
  "code": "ERROR_CODE"
}
```

### Webhook Payload

```json
POST {EXPRESS_WEBHOOK_URL}
X-Webhook-Secret: secret_key
Content-Type: application/json

{
  "session_id": "uuid",
  "client_id": "client_id",
  "transcript": "Full bilingual transcript",
  "analysis": {
    "primary_language": "urdu",
    "legal_domain": "property_law",
    "confidence_score": 0.89,
    "key_entities": {
      "parties": ["Ahmed Khan", "Municipal Corporation"],
      "locations": ["Garden Town, Lahore"],
      "dates": ["January 2025"],
      "amounts": ["Rs. 5,000,000"]
    },
    "issue_summary": "Property dispute regarding unauthorized construction...",
    "adr_suitable": true,
    "adr_reasoning": "Involves civil matter suitable for mediation",
    "urgency": "medium",
    "urgency_reasoning": "No immediate legal deadline mentioned"
  },
  "audio_url": "https://storage.googleapis.com/...",
  "audio_duration_seconds": 342.5,
  "completed_at": "2026-01-21T16:30:00Z",
  "metadata": {...}
}
```

---

## Testing

### Test with Postman

1. Create session:
   - POST `http://localhost:8000/api/v1/sessions`
2. Copy `ws_url`
3. Use WebSocket client to connect
4. Send test audio

### Test with cURL

```bash
# Create session
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"client_id":"test_client"}'

# Get results
curl http://localhost:8000/api/v1/sessions/{session_id}
```

---

## Troubleshooting

### WebSocket Connection Fails
- Check CORS configuration
- Verify session ID is valid and not expired
- Check firewall/network settings

### Audio Not Transcribing
- Verify microphone permissions in browser
- Check audio format (16kHz, mono, PCM)
- Look at GCP STT quotas

### Webhook Not Received
- Verify Express backend is accessible from internet (use ngrok for local testing)
- Check webhook secret matches
- Review Cloud Run logs

---

## Production Checklist

- [ ] Update CORS origins to production domains
- [ ] Set up proper authentication (not `--allow-unauthenticated`)
- [ ] Configure Cloud Run min instances for zero cold starts
- [ ] Set up monitoring and alerting
- [ ] Test webhook delivery reliability
- [ ] Implement rate limiting on Express backend
- [ ] Add session cleanup cron job
- [ ] Review GCP quotas and billing alerts

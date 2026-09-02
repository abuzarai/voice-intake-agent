# Google Cloud Platform Setup Guide

This guide will walk you through setting up GCP for the Voice Interview Agent microservice.

## Step 1: Create GCP Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a Project** → **New Project**
3. Enter project name (e.g., `legal-voice-interview`)
4. Click **Create**
5. Note your **Project ID** (you'll need this later)

## Step 2: Enable Billing

1. Go to **Billing** in the left menu
2. Link a billing account (use your $300 student credits!)
3. Set up budget alerts:
   - Navigate to **Billing** → **Budgets & Alerts**
   - Create budget: $15/month
   - Set alerts at 50%, 90%, 100%

## Step 3: Enable Required APIs

Run these commands in Cloud Shell or your local terminal (after installing `gcloud CLI`):

```bash
# Set your project
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable speech.googleapis.com
gcloud services enable generativelanguage.googleapis.com
gcloud services enable storage-api.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
```

Or enable via Console:
1. Go to **APIs & Services** → **Library**
2. Search and enable each:
   - Cloud Speech-to-Text API
   - Generative Language API
   - Cloud Storage API
   - Cloud Run API
   - Cloud Build API

## Step 4: Create Service Account

### Via Console:

1. Go to **IAM & Admin** → **Service Accounts**
2. Click **Create Service Account**
3. Name: `voice-interview-agent`
4. Click **Create and Continue**
5. Grant roles:
   - **Cloud Speech Client**
   - **Storage Object Admin**
   - **Cloud Run Invoker**
6. Click **Done**

### Via gcloud:

```bash
# Create service account
gcloud iam service-accounts create voice-interview-agent \
    --display-name="Voice Interview Agent Service Account"

# Grant permissions
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:voice-interview-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/speech.client"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:voice-interview-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:voice-interview-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

## Step 5: Create and Download Credentials

### Via Console:

1. Go to **IAM & Admin** → **Service Accounts**
2. Click on `voice-interview-agent@...`
3. Go to **Keys** tab
4. Click **Add Key** → **Create New Key**
5. Select **JSON**
6. Click **Create** (file downloads automatically)
7. Save as `service-account-key.json` in your project root
8. **NEVER COMMIT THIS FILE TO GIT!**

### Via gcloud:

```bash
gcloud iam service-accounts keys create service-account-key.json \
    --iam-account=voice-interview-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

## Step 6: Get Gemini API Key

1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Click **Create API Key**
3. Select your project
4. Copy the API key
5. Add to your `.env` file

## Step 7: Create Cloud Storage Bucket

```bash
# Create bucket for audio storage
gsutil mb -l us-central1 gs://interview-audio-YOUR_PROJECT_ID

# Set lifecycle policy (7-day deletion)
echo '{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 7}
      }
    ]
  }
}' > lifecycle.json

gsutil lifecycle set lifecycle.json gs://interview-audio-YOUR_PROJECT_ID
```

## Step 8: Configure Environment Variables

Update your `.env` file:

```env
GCP_PROJECT_ID=YOUR_PROJECT_ID
GCP_CREDENTIALS_PATH=./service-account-key.json
GEMINI_API_KEY=your_gemini_api_key_here
AUDIO_STORAGE_BUCKET=interview-audio-YOUR_PROJECT_ID
```

## Step 9: Test GCP Access

Run this Python script to verify everything works:

```python
# test_gcp.py
from google.cloud import speech, storage
import google.generativeai as genai
import os

# Test Speech-to-Text
client = speech.SpeechClient()
print("✅ Speech-to-Text: Connected")

# Test Cloud Storage
storage_client = storage.Client()
buckets = list(storage_client.list_buckets())
print(f"✅ Cloud Storage: {len(buckets)} buckets found")

# Test Gemini
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash-exp')
print("✅ Gemini: Connected")

print("\n🎉 All GCP services configured successfully!")
```

Run it:
```bash
python test_gcp.py
```

## Step 10: Set Up Secrets (For Production)

For Cloud Run deployment, use Secret Manager instead of env vars:

```bash
# Create secrets
echo -n "your-gemini-api-key" | gcloud secrets create gemini-api-key --data-file=-
echo -n "your-webhook-secret" | gcloud secrets create webhook-secret --data-file=-

# Grant access to service account
gcloud secrets add-iam-policy-binding gemini-api-key \
    --member="serviceAccount:voice-interview-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

## Troubleshooting

### "Permission Denied" Errors

```bash
# Verify service account has correct permissions
gcloud projects get-iam-policy YOUR_PROJECT_ID \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:voice-interview-agent@*"
```

### "API Not Enabled" Errors

```bash
# List enabled APIs
gcloud services list --enabled

# Enable missing API
gcloud services enable SERVICE_NAME.googleapis.com
```

### Quota Errors

1. Go to **IAM & Admin** → **Quotas**
2. Filter by API name
3. Request quota increase if needed

## Cost Monitoring

### Set Up Budget Alerts

1. Go to **Billing** → **Budgets & Alerts**
2. Create budget with these thresholds:
   - $5 (50%)
   - $10 (90%)
   - $15 (100%)
3. Add your email for notifications

### Monitor Usage

```bash
# View current billing
gcloud billing accounts list

# Check usage
gcloud alpha billing accounts get-iam-policy BILLING_ACCOUNT_ID
```

## What's Next?

✅ GCP project created  
✅ APIs enabled  
✅ Service account configured  
✅ Credentials downloaded  
✅ Storage bucket created  
✅ Environment variables set

**Next steps:**
1. Run the application locally: `uv run uvicorn app.main:app --reload`
2. Test the API endpoints
3. Deploy to Cloud Run

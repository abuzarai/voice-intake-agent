"""Voice Interview Agent - GCP-based legal intake microservice."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env early so GCP clients can find credentials at import time
load_dotenv()

_creds_path = os.getenv("GCP_CREDENTIALS_PATH")
if _creds_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    # Resolve relative paths from project root
    resolved = Path(_creds_path).resolve()
    if resolved.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)

__version__ = "0.1.0"


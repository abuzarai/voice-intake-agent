"""FastAPI application entry point."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.api import rest
from app.utils import get_logger
from app.utils.logger import configure_logging
from app.middleware.request_logging import request_logging_middleware
from app.middleware.rate_limit import rate_limit_sessions

configure_logging()
logger = get_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Voice Interview Agent",
    description="GCP-based legal intake microservice with real-time bilingual transcription and AI classification",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,  # Disable docs in production
    redoc_url="/redoc" if not settings.is_production else None
)

# Configure CORS from settings (explicit origins; credentials off — the
# browser client uses bearer tokens, not cookies).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Request/response logging
app.middleware("http")(request_logging_middleware)

# Include routers
app.include_router(rest.router, dependencies=[Depends(rate_limit_sessions)])
# Conversational WebSocket (default)
from app.api import websocket_conversational
app.include_router(websocket_conversational.router)


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info(
        "Voice Interview Agent starting...",
        "Voice Interview Agent شروع ہو رہا ہے...",
        environment=settings.ENVIRONMENT,
        gcp_project=settings.GCP_PROJECT_ID
    )


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info(
        "Voice Interview Agent shutting down...",
        "Voice Interview Agent بند ہو رہا ہے..."
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    logger.error(
        f"Unhandled exception: {str(exc)}",
        f"غیر ہینڈل شدہ exception: {str(exc)}",
        path=request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={
            "message_en": "An internal error occurred",
            "message_ur": "ایک اندرونی خرابی پیش آئی"
        }
    )


@app.get("/")
async def root():
    """Root endpoint."""
    payload = {
        "service": "Voice Interview Agent",
        "version": "0.1.0",
        "status": "running",
        "message_en": "Legal intake voice interview service",
        "message_ur": "قانونی داخلہ صوتی انٹرویو سروس",
    }
    if not settings.is_production:
        payload["test_ui"] = "/test"
    return payload


# Serve Test UI only outside production; it is a raw recording/TTS harness
# that would burn Gemini quota if exposed on a deployed instance.
from fastapi.responses import HTMLResponse
import os


async def _test_ui():
    """Serve the test UI page."""
    # Get the path to test_ui.html (in root directory)
    test_ui_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_ui.html")

    if os.path.exists(test_ui_path):
        with open(test_ui_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # Replace hardcoded URLs with empty string (use relative URLs)
        html_content = html_content.replace("http://127.0.0.1:8001", "")
        html_content = html_content.replace("http://127.0.0.1:8000", "")
        html_content = html_content.replace("ws://127.0.0.1:8001", "")
        html_content = html_content.replace("ws://127.0.0.1:8000", "")

        # Fix WebSocket URL to use current host
        html_content = html_content.replace(
            "const API_BASE = '';",
            "const API_BASE = window.location.origin;"
        )
        html_content = html_content.replace(
            "const WS_BASE = '';",
            "const WS_BASE = window.location.origin.replace('http', 'ws');"
        )

        return HTMLResponse(content=html_content)
    else:
        return HTMLResponse(content="<h1>Test UI not found</h1><p>test_ui.html is missing</p>", status_code=404)


if not settings.is_production:
    app.get("/test", response_class=HTMLResponse)(_test_ui)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=not settings.is_production,
        log_level=settings.LOG_LEVEL.lower()
    )

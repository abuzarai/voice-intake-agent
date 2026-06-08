"""Request/response logging middleware for FastAPI."""

import time
from starlette.requests import Request
from starlette.responses import Response
from app.utils import get_logger

logger = get_logger("app.request")


async def request_logging_middleware(request: Request, call_next):
    """
    Log inbound HTTP requests with timing and status.
    
    Keeps behavior unchanged; only emits structured logs.
    """
    start = time.perf_counter()
    response: Response | None = None
    
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        # Log and re-raise so global handler can respond
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            f"{request.method} {request.url.path} failed: {exc}",
            f"{request.method} {request.url.path} ناکام: {exc}",
            method=request.method,
            path=request.url.path,
            client_ip=request.client.host if request.client else None,
            status_code=500,
            process_ms=duration_ms,
        )
        raise
    finally:
        if response is not None:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                f"{request.method} {request.url.path} completed",
                f"{request.method} {request.url.path} مکمل ہوا",
                method=request.method,
                path=request.url.path,
                client_ip=request.client.host if request.client else None,
                status_code=response.status_code if response else None,
                process_ms=duration_ms,
            )

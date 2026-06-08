"""Human-friendly logging with bilingual support."""

import logging
import sys
from typing import Any, Dict
from app.config import settings


def _format_message(message_en: str, message_ur: str, extra: Dict[str, Any]) -> str:
    """Create a readable, single-line log message."""
    bilingual = message_en if not message_ur else f"{message_en} | {message_ur}"
    if extra:
        context = " ".join(f"{k}={v}" for k, v in extra.items())
        return f"{bilingual} | {context}"
    return bilingual


class BilingualLogger:
    """Logger with bilingual message support."""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self._configure_logger()
    
    def _configure_logger(self):
        """Configure human-readable logging."""
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
            # Avoid duplicate logs when root logger is also configured
            self.logger.propagate = False
    
    def _log(self, level: int, message_en: str, message_ur: str, **kwargs):
        msg = _format_message(message_en, message_ur, kwargs)
        self.logger.log(level, msg)
    
    def info(self, message_en: str, message_ur: str = "", **kwargs):
        self._log(logging.INFO, message_en, message_ur, **kwargs)
    
    def error(self, message_en: str, message_ur: str = "", **kwargs):
        self._log(logging.ERROR, message_en, message_ur, **kwargs)
    
    def warning(self, message_en: str, message_ur: str = "", **kwargs):
        self._log(logging.WARNING, message_en, message_ur, **kwargs)
    
    def debug(self, message_en: str, message_ur: str = "", **kwargs):
        self._log(logging.DEBUG, message_en, message_ur, **kwargs)


def get_logger(name: str) -> BilingualLogger:
    """Get a configured logger instance."""
    return BilingualLogger(name)


def configure_logging():
    """Configure root and Uvicorn loggers for readable output."""
    root_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    # Force a clean, consistent console handler even if Uvicorn set one first
    logging.basicConfig(
        level=root_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,  # override any existing handlers so logs always show
    )
    
    # Make Uvicorn/Starlette loggers propagate to root so they share the formatter
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(root_level)

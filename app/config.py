"""Configuration management using Pydantic Settings."""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration with environment variable support."""
    
    # Gemini model (LLM + audio transcription)
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # GCP Configuration (only needed for Vertex AI / legacy GCS mode)
    GCP_PROJECT_ID: Optional[str] = ""
    GCP_CREDENTIALS_PATH: Optional[str] = None
    
    # Service Configuration
    SESSION_TIMEOUT_MINUTES: int = 60
    MAX_AUDIO_DURATION_SECONDS: int = 600
    AUDIO_RETENTION_DAYS: int = 7
    
    # CORS Configuration
    CORS_ORIGINS: str = "http://localhost:3000"
    
    # Express Backend Integration
    EXPRESS_WEBHOOK_URL: Optional[str] = None
    EXPRESS_WEBHOOK_SECRET: Optional[str] = None
    
    # Audio Storage (local directory; replaces Cloud Storage bucket)
    AUDIO_DIR: str = "./data/audio"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"
    
    # Server Configuration
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENVIRONMENT.lower() == "production"
    
    @property
    def audio_dir(self) -> str:
        """Local directory where interview audio is stored."""
        return self.AUDIO_DIR


# Global settings instance
settings = Settings()

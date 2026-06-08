"""Configuration management using Pydantic Settings."""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration with environment variable support."""
    
    # GCP Configuration
    GCP_PROJECT_ID: str
    GCP_CREDENTIALS_PATH: Optional[str] = None
    
    # Gemini API (optional - use Vertex AI by default)
    GEMINI_API_KEY: Optional[str] = None
    
    # Service Configuration
    SESSION_TIMEOUT_MINUTES: int = 60
    MAX_AUDIO_DURATION_SECONDS: int = 600
    AUDIO_RETENTION_DAYS: int = 7
    
    # CORS Configuration
    CORS_ORIGINS: str = "http://localhost:3000"
    
    # Express Backend Integration
    EXPRESS_WEBHOOK_URL: Optional[str] = None
    EXPRESS_WEBHOOK_SECRET: Optional[str] = None
    
    # Cloud Storage
    AUDIO_STORAGE_BUCKET: Optional[str] = None
    
    # Logging
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"
    
    # Server Configuration
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
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
    def storage_bucket_name(self) -> str:
        """Get or generate storage bucket name."""
        if self.AUDIO_STORAGE_BUCKET:
            return self.AUDIO_STORAGE_BUCKET
        return f"interview-audio-{self.GCP_PROJECT_ID}"


# Global settings instance
settings = Settings()

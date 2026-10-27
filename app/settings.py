from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    LIVE_ENABLED: bool = False
    LIVE_BOOTSTRAP_SECRET: SecretStr | None = Field(
        default=None, min_length=32, max_length=256
    )
    LIVE_CAPABILITY_TTL_SECONDS: int = Field(default=300, ge=30, le=300)
    LIVE_MAX_SESSIONS: int = Field(default=16, ge=1, le=64)
    LIVE_ADMISSIONS_PER_MINUTE: int = Field(default=6, ge=1, le=60)
    LIVE_API_KEY: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    LIVE_MODEL: str | None = Field(default=None, min_length=1, max_length=128)
    LIVE_FREE_TIER_ATTESTED: bool = False
    CORS_ORIGINS: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=None, case_sensitive=True, extra="ignore", hide_input_in_errors=True
    )

    @field_validator(
        "LIVE_BOOTSTRAP_SECRET", "LIVE_API_KEY", "LIVE_MODEL", mode="before"
    )
    @classmethod
    def empty_optional_configuration(cls, value):
        # Compose substitutes absent optional values with empty strings. Keep the
        # disabled service bootable; admission still rejects missing credentials.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

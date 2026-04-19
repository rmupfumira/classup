"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "ClassUp"
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: str
    app_base_url: str = "http://localhost:8000"
    app_debug: bool = False
    app_log_level: str = "INFO"

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, v):
        """Strip whitespace + lowercase — Railway env vars can sneak in a
        trailing space that would otherwise fail the Literal check."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("app_base_url", mode="before")
    @classmethod
    def _normalize_app_base_url(cls, v):
        """Strip whitespace + trailing slash from the base URL so email
        links don't double-slash or contain hidden whitespace."""
        if isinstance(v, str):
            return v.strip().rstrip("/")
        return v

    @field_validator("app_log_level", mode="before")
    @classmethod
    def _normalize_app_log_level(cls, v):
        """Strip whitespace + uppercase. Accepts 'info', 'INFO ', etc."""
        if isinstance(v, str):
            return v.strip().upper()
        return v

    # Database
    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # Redis (optional for local dev)
    redis_url: str | None = None

    # JWT Authentication
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440  # 24 hours
    jwt_refresh_token_expire_days: int = 30

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "classup-files"
    r2_public_url: str | None = None

    # Email (fallback defaults, SMTP config is stored in DB)
    email_from_address: str = "notifications@classup.co.za"
    email_from_name: str = "ClassUp"

    # WhatsApp (Meta Cloud API)
    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_business_account_id: str = ""

    # Paystack
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_base_url: str = "https://api.paystack.co"

    # Defaults
    default_language: str = "en"
    supported_languages: str = "en,af"
    max_upload_size_mb: int = 10
    invitation_code_expiry_days: int = 7

    @property
    def effective_jwt_secret(self) -> str:
        """Get the JWT secret key, falling back to app secret key."""
        return self.jwt_secret_key or self.app_secret_key

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.app_env == "development"

    @property
    def async_database_url(self) -> str:
        """Get database URL with asyncpg driver for async SQLAlchemy."""
        url = self.database_url
        # Convert postgresql:// to postgresql+asyncpg://
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """Get database URL with psycopg2 driver for sync operations (Alembic)."""
        url = self.database_url
        # Convert to standard postgresql:// format
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        # Remove any async driver specification
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "", 1)
        return url

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.app_env == "production"

    @property
    def supported_languages_list(self) -> list[str]:
        """Get supported languages as a list."""
        return [lang.strip() for lang in self.supported_languages.split(",")]

    @property
    def max_upload_size_bytes(self) -> int:
        """Get max upload size in bytes."""
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def r2_endpoint_url(self) -> str:
        """Get the R2 endpoint URL."""
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def redis_available(self) -> bool:
        """Check if Redis is configured."""
        return bool(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()

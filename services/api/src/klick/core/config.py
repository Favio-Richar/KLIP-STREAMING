"""Configuración de la aplicación (variables de entorno; contrato en docs/03 §8)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = ""
    database_url_system: str = ""
    database_url_owner: str = ""
    redis_url: str = "redis://localhost:6381/0"

    app_secret_key: SecretStr = SecretStr("change-me-dev-only")
    encryption_master_key: SecretStr = SecretStr("change-me-dev-only-32-bytes-base64")
    jwt_issuer: str = "http://localhost:8000"
    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"

    cookie_secure: bool | None = None  # None: Secure solo en producción
    access_token_ttl_s: int = 900
    refresh_token_ttl_days: int = 30
    verify_email_ttl_h: int = 48
    reset_password_ttl_h: int = 1

    email_backend: Literal["console", "smtp"] = "console"
    email_from: str = "Klick <no-reply@klick.local>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = True

    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr = SecretStr("")

    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    feature_fake_adapters: bool = True

    @model_validator(mode="after")
    def _no_default_secrets_in_production(self) -> "Settings":
        if self.environment == "production":
            defaults = {
                "APP_SECRET_KEY": self.app_secret_key.get_secret_value().startswith("change-me"),
                "ENCRYPTION_MASTER_KEY": self.encryption_master_key.get_secret_value().startswith(
                    "change-me"
                ),
            }
            bad = [name for name, is_default in defaults.items() if is_default]
            if bad:
                raise ValueError(f"Valores por defecto no permitidos en producción: {bad}")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

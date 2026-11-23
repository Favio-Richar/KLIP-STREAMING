"""Configuración de la aplicación (variables de entorno; contrato en docs/03 §8)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    redis_url: str = "redis://localhost:6381/0"

    app_secret_key: SecretStr = SecretStr("change-me-dev-only")
    encryption_master_key: SecretStr = SecretStr("change-me-dev-only-32-bytes-base64")
    jwt_issuer: str = "http://localhost:8000"
    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"

    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    feature_fake_adapters: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

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
    rate_limit_register_ip: int = 10  # por hora
    rate_limit_login_ip: int = 30  # por 5 minutos

    s3_endpoint: str = "http://localhost:9000"  # interno (API y workers)
    s3_public_endpoint: str = "http://localhost:9000"  # el que usa el navegador en URLs firmadas
    s3_bucket: str = "klick"
    s3_access_key: str = "klick_minio"
    s3_secret_key: SecretStr = SecretStr("klick_minio_secret")
    s3_region: str = "us-east-1"
    storage_url_ttl_s: int = 900  # vida de las URL firmadas de subida y descarga
    storage_orphan_grace_h: int = 24  # ADR-005: gracia antes de borrar un objeto sin referencias
    storage_upload_stale_h: int = 24  # subidas sin completar se consideran abandonadas

    job_heartbeat_interval_s: float = 10
    job_heartbeat_timeout_s: float = 60  # sin latido durante este tiempo = worker perdido
    job_backoff_base_s: float = 5  # reintento n espera base * 2**(n-1), máx. 15 min
    job_redispatch_after_s: float = 600  # re-envía jobs en cola cuyo mensaje pudo perderse
    outbox_batch_size: int = 100
    idempotency_ttl_h: int = 24
    audit_retention_days: int = 730

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
                "S3_SECRET_KEY": self.s3_secret_key.get_secret_value() == "klick_minio_secret",
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

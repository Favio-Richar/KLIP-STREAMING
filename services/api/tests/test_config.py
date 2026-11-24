from pathlib import Path

from klick.core.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def test_env_example_is_a_valid_configuration() -> None:
    """Quien sigue el runbook copia `.env.example` tal cual: debe cargar sin errores."""
    assert ENV_EXAMPLE.is_file()
    settings = Settings(_env_file=ENV_EXAMPLE)  # type: ignore[call-arg]
    assert settings.environment == "development"
    assert settings.cookie_secure is None  # vacío = automático
    assert not settings.is_production


def test_empty_cookie_secure_means_automatic_and_explicit_values_still_work() -> None:
    assert Settings(cookie_secure="").cookie_secure is None  # type: ignore[arg-type]
    assert Settings(cookie_secure="  ").cookie_secure is None  # type: ignore[arg-type]
    assert Settings(cookie_secure="true").cookie_secure is True  # type: ignore[arg-type]
    assert Settings(cookie_secure="false").cookie_secure is False  # type: ignore[arg-type]

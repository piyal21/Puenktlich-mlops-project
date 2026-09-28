from pathlib import Path

import pytest

from dbdelay.config import Settings, get_settings
from dbdelay.errors import ConfigError


def test_defaults_target_local_minio_bucket() -> None:
    settings = get_settings()
    assert settings.app_env == "local"
    assert settings.aws_region == "eu-central-1"
    assert settings.storage_endpoint_url is None
    assert settings.data_bucket == "puenktlich-local"


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATA_BUCKET", "puenktlich-data-123")
    monkeypatch.setenv("STORAGE_ENDPOINT_URL", "http://localhost:9000")
    settings = get_settings()
    assert settings.app_env == "prod"
    assert settings.data_bucket == "puenktlich-data-123"
    assert settings.storage_endpoint_url == "http://localhost:9000"


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MODELS_BUCKET=from-dotenv\n", encoding="utf-8")
    assert get_settings().models_bucket == "from-dotenv"


def test_invalid_value_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ConfigError, match="app_env"):
        get_settings()


def test_config_error_does_not_leak_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "super-secret-value")
    with pytest.raises(ConfigError) as exc_info:
        get_settings()
    assert "super-secret-value" not in str(exc_info.value)


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_API_KEY", "my-real-key")
    settings = get_settings()
    assert "my-real-key" not in repr(settings)
    assert settings.db_api_key is not None
    assert settings.db_api_key.get_secret_value() == "my-real-key"


def test_settings_are_cached_and_frozen() -> None:
    first = get_settings()
    assert get_settings() is first
    with pytest.raises(ValueError, match="frozen"):
        first.data_bucket = "other"


def test_settings_can_be_built_explicitly() -> None:
    settings = Settings(data_bucket="explicit", _env_file=None)  # type: ignore[call-arg]
    assert settings.data_bucket == "explicit"


def test_empty_env_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `.env.example` ships `DB_API_KEY=`; that must not become an empty secret.
    monkeypatch.setenv("DB_API_KEY", "")
    monkeypatch.setenv("STORAGE_ENDPOINT_URL", "")
    settings = get_settings()
    assert settings.db_api_key is None
    assert settings.storage_endpoint_url is None


def test_stations_file_defaults_to_repo_config_and_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).stations_file == Path("configs/stations.yaml")  # type: ignore[call-arg]
    monkeypatch.setenv("STATIONS_FILE", "/opt/airflow/configs/stations.yaml")
    assert Settings(_env_file=None).stations_file == Path("/opt/airflow/configs/stations.yaml")  # type: ignore[call-arg]

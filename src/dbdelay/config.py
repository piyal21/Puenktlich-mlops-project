"""Runtime settings, loaded from environment variables (and `.env` locally).

Non-secret tunables (stations, training, monitoring) live in YAML under `configs/`;
this module only holds environment-specific settings (see docs/architecture.md §13).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from dbdelay.errors import ConfigError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Environment settings. Field names map to upper-case env vars (e.g. ``DATA_BUCKET``)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        # `DB_API_KEY=` in .env means "not set", not an empty secret.
        env_ignore_empty=True,
    )

    app_env: Literal["local", "test", "prod"] = "local"
    log_level: LogLevel = "INFO"
    aws_region: str = "eu-central-1"

    # Storage: endpoint set → S3-compatible (MinIO); unset → real AWS S3.
    storage_endpoint_url: str | None = None
    storage_access_key_id: SecretStr | None = None
    storage_secret_access_key: SecretStr | None = None
    data_bucket: str = "puenktlich-local"
    models_bucket: str = "puenktlich-local"
    web_bucket: str = "puenktlich-local"

    # Model pointer: local file path, or SSM parameter name in prod.
    model_pointer_param: str = ".local/champion_version"

    # DB Timetables API credentials (only needed by ingestion).
    db_api_client_id: SecretStr | None = None
    db_api_key: SecretStr | None = None

    mlflow_tracking_uri: str = "http://localhost:5000"

    # Station list (relative to the working directory; containers set an absolute path).
    stations_file: Path = Path("configs/stations.yaml")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, validated once.

    Returns:
        The cached ``Settings`` instance.

    Raises:
        ConfigError: if any environment value is invalid.
    """
    try:
        return Settings()
    except ValidationError as exc:
        # Only field names and error types — values may contain secrets.
        problems = ", ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid settings: {problems}") from None

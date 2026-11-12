"""DB Timetables API credentials: `.env` values locally, SSM SecureString parameters in prod."""

from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from dbdelay.config import Settings
from dbdelay.errors import ConfigError, ExternalServiceError

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient


def load_db_api_credentials(settings: Settings, ssm: "SSMClient | None") -> tuple[str, str]:
    """(client id, api key). Errors name settings or parameters, never values.

    Raises:
        ConfigError: neither values nor parameter names are configured, or a parameter is missing.
        ExternalServiceError: SSM could not be read.
    """
    if settings.db_api_client_id is not None and settings.db_api_key is not None:
        return settings.db_api_client_id.get_secret_value(), settings.db_api_key.get_secret_value()
    id_param, key_param = settings.db_api_client_id_param, settings.db_api_key_param
    if id_param is None or key_param is None or ssm is None:
        raise ConfigError(
            "DB API credentials missing: set DB_API_CLIENT_ID and DB_API_KEY, "
            "or DB_API_CLIENT_ID_PARAM and DB_API_KEY_PARAM"
        )
    names = (id_param, key_param)
    try:
        response = ssm.get_parameters(Names=list(names), WithDecryption=True)
    except (ClientError, BotoCoreError) as exc:
        raise ExternalServiceError(f"SSM read of {', '.join(names)} failed") from exc
    values = {p["Name"]: p["Value"] for p in response.get("Parameters", [])}
    absent = [name for name in names if name not in values]
    if absent:
        raise ConfigError(f"SSM parameter(s) missing: {', '.join(absent)}")
    return values[names[0]], values[names[1]]

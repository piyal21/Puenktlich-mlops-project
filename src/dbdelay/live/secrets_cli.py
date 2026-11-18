"""`make put-db-api-secrets`: copy the DB API keys from `.env` into the two SSM SecureString
parameters Terraform created (placeholder values). Prints parameter names only, never values."""

import argparse
import sys
from collections.abc import Sequence

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from dbdelay.aws_session import refusal_line
from dbdelay.config import get_settings
from dbdelay.storage import BOTO_CONFIG

DEFAULT_REGION = "eu-north-1"


def put_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy DB API keys from .env into SSM.")
    parser.add_argument("--client-id-param", required=True)
    parser.add_argument("--key-param", required=True)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.db_api_client_id is None or settings.db_api_key is None:
        sys.stderr.write("put refused: DB_API_CLIENT_ID and DB_API_KEY must be set in .env\n")
        return 1
    values = {
        args.client_id_param: settings.db_api_client_id.get_secret_value(),
        args.key_param: settings.db_api_key.get_secret_value(),
    }
    try:
        ssm = boto3.client("ssm", region_name=args.region, config=BOTO_CONFIG)
        for name, value in values.items():
            ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)
    except (BotoCoreError, ClientError) as exc:
        sys.stderr.write(refusal_line("put", exc) + "\n")
        return 1
    sys.stdout.write(f"stored SecureString parameters: {', '.join(values)}\n")
    return 0

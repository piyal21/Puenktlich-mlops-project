"""`make put-db-api-secrets`: DB API keys from .env → SSM SecureString (values never printed)."""

from dbdelay.live.secrets_cli import put_main

if __name__ == "__main__":
    raise SystemExit(put_main())

"""Append missing Airflow secrets to `.env` without printing their values.

Usage: uv run python scripts/ensure_airflow_env.py [path-to-.env]
"""

import base64
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

GENERATORS: dict[str, Callable[[], str]] = {
    "AIRFLOW_ADMIN_PASSWORD": lambda: secrets.token_urlsafe(24),
    "AIRFLOW_SECRET_KEY": lambda: secrets.token_urlsafe(32),
    "AIRFLOW_FERNET_KEY": lambda: base64.urlsafe_b64encode(os.urandom(32)).decode(),
}


def ensure_keys(env_file: Path) -> list[str]:
    """Add each missing key with a fresh random value; return the names added."""
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    present = {line.split("=", 1)[0].strip() for line in text.splitlines() if "=" in line}
    added = [key for key in GENERATORS if key not in present]
    if added:
        lines = [f"{key}={GENERATORS[key]()}" for key in added]
        prefix = "" if not text or text.endswith("\n") else "\n"
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\n".join(lines) + "\n")
    return added


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".env")
    names = ensure_keys(target)
    sys.stdout.write(f"added: {', '.join(names) or 'nothing'}\n")

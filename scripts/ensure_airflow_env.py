"""Fill missing or placeholder Airflow secrets in `.env` without printing their values.

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


PLACEHOLDER_PREFIX = "change-me"


def _needs_value(value: str) -> bool:
    value = value.strip()
    return not value or value.startswith(PLACEHOLDER_PREFIX)


def ensure_keys(env_file: Path) -> list[str]:
    """Give each missing, empty or placeholder key a fresh random value; return the names set.

    Placeholders (copied from `.env.example`) are replaced in place; missing keys are appended.
    """
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    lines = text.splitlines()
    present: set[str] = set()
    replaced: set[str] = set()
    for index, line in enumerate(lines):
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or key not in GENERATORS:
            continue
        present.add(key)
        if _needs_value(value):
            lines[index] = f"{key}={GENERATORS[key]()}"
            replaced.add(key)
    lines += [f"{key}={GENERATORS[key]()}" for key in GENERATORS if key not in present]
    changed = [key for key in GENERATORS if key in replaced or key not in present]
    if changed:
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".env")
    names = ensure_keys(target)
    sys.stdout.write(f"added: {', '.join(names) or 'nothing'}\n")

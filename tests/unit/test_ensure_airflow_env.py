import base64
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ensure_airflow_env.py"
spec = importlib.util.spec_from_file_location("ensure_airflow_env", SCRIPT)
assert spec is not None
assert spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_adds_only_missing_keys_and_keeps_existing_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("APP_ENV=local\nAIRFLOW_SECRET_KEY=keep-me\n", encoding="utf-8")

    added = module.ensure_keys(env)

    text = env.read_text(encoding="utf-8")
    assert added == ["AIRFLOW_ADMIN_PASSWORD", "AIRFLOW_FERNET_KEY"]
    assert "AIRFLOW_SECRET_KEY=keep-me" in text
    assert text.count("AIRFLOW_ADMIN_PASSWORD=") == 1
    assert module.ensure_keys(env) == []


def test_fernet_key_is_urlsafe_base64_of_32_bytes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    module.ensure_keys(env)
    line = next(
        entry
        for entry in env.read_text(encoding="utf-8").splitlines()
        if entry.startswith("AIRFLOW_FERNET_KEY=")
    )
    assert len(base64.urlsafe_b64decode(line.split("=", 1)[1])) == 32


def test_missing_file_is_created(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert len(module.ensure_keys(env)) == 3
    assert env.exists()

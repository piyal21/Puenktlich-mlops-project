import json

import pytest

from dbdelay.logging import get_logger


def test_logger_emits_json_with_service(capsys: pytest.CaptureFixture[str]) -> None:
    logger = get_logger("unit-test")
    logger.info("hello", eva="8010101")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["service"] == "unit-test"
    assert record["message"] == "hello"
    assert record["eva"] == "8010101"
    assert record["level"] == "INFO"


def test_logger_respects_configured_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    logger = get_logger("unit-test-level")
    logger.info("hidden")
    logger.warning("shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "shown" in out

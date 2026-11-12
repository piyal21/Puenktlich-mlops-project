from datetime import date
from typing import Any

import pytest

from dbdelay.errors import ConfigError
from dbdelay.live import cli
from dbdelay.live.ingest import IngestResult


def test_ingest_main_reports_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "make_client", lambda settings: object())
    monkeypatch.setattr(
        cli, "ingest_job", lambda *a, **k: IngestResult(3, 2, ("8000105",), 1, 9, 4)
    )
    assert cli.ingest_main([]) == 0
    assert "'events': 9" in capsys.readouterr().out


def test_ingest_main_missing_keys_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.ingest_main([]) == 1
    assert "DB_API_CLIENT_ID" in capsys.readouterr().err


def test_etl_main_passes_the_day(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[date] = []

    def fake(settings: Any, day: date) -> Any:
        seen.append(day)
        raise ConfigError("stop here")

    monkeypatch.setattr(cli, "etl_job", fake)
    assert cli.etl_main(["--day", "2026-11-14"]) == 1
    assert seen == [date(2026, 11, 14)]

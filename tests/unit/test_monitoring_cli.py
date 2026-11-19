from datetime import date
from typing import Any

import pytest

from dbdelay.monitoring import cli
from dbdelay.monitoring.triggers import ThresholdOverrides
from tests.unit.test_monitor_handler import _outcome


def test_cli_runs_day_with_overrides(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**kw: Any) -> Any:
        calls.append(kw)
        return _outcome(kw["day"], "ok")

    monkeypatch.setattr(cli, "run_monitor", fake)
    monkeypatch.setattr(cli, "load_monitoring_config", lambda path: None)
    assert cli.monitor_main(["--day", "2026-11-16", "--drift-share-max", "0"]) == 0
    assert calls[0]["day"] == date(2026, 11, 16)
    assert calls[0]["overrides"] == ThresholdOverrides(drift_share_max=0.0)
    assert capsys.readouterr().out.startswith("2026-11-16 ok rows=12000")


def test_cli_without_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**kw: Any) -> Any:
        calls.append(kw)
        return _outcome(kw["day"], "no_data")

    monkeypatch.setattr(cli, "run_monitor", fake)
    monkeypatch.setattr(cli, "load_monitoring_config", lambda path: None)
    assert cli.monitor_main(["--day", "2026-11-16"]) == 0
    assert calls[0]["overrides"] is None

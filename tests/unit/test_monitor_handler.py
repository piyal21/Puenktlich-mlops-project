import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import monitor_handler
from dbdelay.monitoring.drift import ColumnDrift, DriftResult
from dbdelay.monitoring.history import DailyResult, DayStatus, Verdict
from dbdelay.monitoring.job import MonitorOutcome
from dbdelay.monitoring.performance import PerformanceMetrics
from dbdelay.monitoring.triggers import ThresholdOverrides
from tests.unit.conftest import TEST_BUCKET

REPO = Path(__file__).resolve().parents[2]
CTX = SimpleNamespace(aws_request_id="ctx")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    monkeypatch.setenv("MODELS_BUCKET", TEST_BUCKET)
    monkeypatch.setenv("MONITORING_CONFIG_FILE", str(REPO / "configs" / "monitoring.yaml"))


def _outcome(day: date, status: DayStatus) -> MonitorOutcome:
    ok = status == "ok"
    col = ColumnDrift(
        column="p_late", method="wasserstein_normed", score=0.05, drifted=False, counted=False
    )
    return MonitorOutcome(
        result=DailyResult(
            day=day,
            status=status,
            model_version="1",
            champion_test_brier=0.14,
            performance=PerformanceMetrics(rows=12000, brier=0.161, roc_auc=0.78)
            if ok
            else PerformanceMetrics(rows=0),
            brier_ratio=1.15 if ok else None,
            drift=DriftResult(
                columns=[],
                drift_share=2 / 7,
                prediction=col,
                reference_rows=10,
                current_rows=10,
                day_type="weekday",
            )
            if ok
            else None,
            verdict=Verdict(
                drift_bad=False, performance_bad=ok, drift_share_max=0.3, brier_ratio_max=1.1
            ),
            excluded_rows={},
            computed_at=datetime(2026, 11, 19, 3, 30, tzinfo=UTC),
        ),
        flag_written=None,
    )


def _capture(monkeypatch: pytest.MonkeyPatch, status: DayStatus) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(**kw: Any) -> MonitorOutcome:
        calls.append(kw)
        return _outcome(kw["day"], status)

    monkeypatch.setattr(monitor_handler, "run_monitor", fake)
    return calls


def _metrics(text: str) -> dict[str, Any]:
    lines = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    (metric,) = [line for line in lines if "_aws" in line]
    assert isinstance(metric, dict)
    return metric


def test_default_day_is_yesterday(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, "ok")
    out = monitor_handler.handler({}, CTX)
    expected = datetime.now(UTC).date() - timedelta(days=1)
    assert calls[0]["day"] == expected
    assert out["day"] == expected.isoformat()
    assert calls[0]["overrides"] is None


def test_explicit_day_and_overrides_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, "ok")
    out = monitor_handler.handler(
        {"day": "2026-11-16", "threshold_overrides": {"drift_share_max": 0.0}}, CTX
    )
    assert calls[0]["day"] == date(2026, 11, 16)
    assert calls[0]["overrides"] == ThresholdOverrides(drift_share_max=0.0)
    assert out == {"day": "2026-11-16", "status": "ok", "rows": 12000, "flag": None}


def test_rejects_unknown_event_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, "ok")
    with pytest.raises(ValidationError):
        monitor_handler.handler({"dya": "2026-11-16"}, CTX)


def test_emits_all_metrics_on_ok_day(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture(monkeypatch, "ok")
    monitor_handler.handler({"day": "2026-11-16"}, CTX)
    metric = _metrics(capsys.readouterr().out)
    assert metric["service"] == "monitor"
    assert metric["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Puenktlich"
    assert metric["LabeledEvents"] == [12000.0]
    assert metric["BrierDaily"] == [0.161]
    assert metric["AUCDaily"] == [0.78]
    assert metric["BrierRatio"] == [1.15]
    assert metric["DriftShare"] == [pytest.approx(2 / 7)]


def test_emits_labeled_events_zero_on_no_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture(monkeypatch, "no_data")
    monitor_handler.handler({"day": "2026-11-16"}, CTX)
    metric = _metrics(capsys.readouterr().out)
    assert metric["LabeledEvents"] == [0.0]
    for name in ("BrierDaily", "AUCDaily", "BrierRatio", "DriftShare"):
        assert name not in metric

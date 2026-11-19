from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dbdelay.monitoring.config import load_monitoring_config
from dbdelay.monitoring.history import HistoryEntry, Verdict
from dbdelay.monitoring.triggers import ThresholdOverrides, evaluate_trigger
from tests.unit.test_monitoring_history import entry

CFG = load_monitoring_config(Path(__file__).resolve().parents[2] / "configs" / "monitoring.yaml")
NOW = datetime(2026, 11, 20, 3, 31, tzinfo=UTC)
D0 = date(2026, 11, 15)


def bad(*, drift: bool = False, perf: bool = False) -> Verdict:
    return Verdict(drift_bad=drift, performance_bad=perf, drift_share_max=0.3, brier_ratio_max=1.1)


def days(n: int, start: date = D0) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def streak(verdicts: list[Verdict], **kw: object) -> list[HistoryEntry]:
    return [entry(d, verdict=v, **kw) for d, v in zip(days(len(verdicts)), verdicts, strict=True)]


def test_three_bad_days_performance() -> None:
    history = streak([bad(perf=True)] * 3, brier_ratio=1.2)
    request = evaluate_trigger(history, CFG, now=NOW)
    assert request is not None
    assert request.reason == "performance"
    assert request.days == days(3)
    assert request.state == "requested"
    assert not request.forced
    assert request.model_version == "1"
    assert request.request_id == "20261120T033100Z-performance"
    assert request.thresholds == {"drift_share_max": 0.30, "brier_ratio_max": 1.10}


def test_two_days_not_enough() -> None:
    assert evaluate_trigger(streak([bad(perf=True)] * 2), CFG, now=NOW) is None


def test_only_newest_days_count() -> None:
    history = streak([bad(perf=True)] * 3 + [bad()])
    assert evaluate_trigger(history, CFG, now=NOW) is None


def test_gap_day_breaks_streak() -> None:
    history = streak([bad(perf=True)] * 3)
    history[0] = history[0].model_copy(update={"day": D0 - timedelta(days=1)})
    assert evaluate_trigger(history, CFG, now=NOW) is None


def test_no_data_day_breaks_streak() -> None:
    history = streak([bad(perf=True)] * 3)
    history[1] = history[1].model_copy(update={"status": "no_data"})
    assert evaluate_trigger(history, CFG, now=NOW) is None


def test_model_change_breaks_streak() -> None:
    history = streak([bad(perf=True)] * 3)
    history[2] = history[2].model_copy(update={"model_version": "2"})
    assert evaluate_trigger(history, CFG, now=NOW) is None


def test_mixed_reasons_do_not_trigger() -> None:
    history = streak([bad(perf=True), bad(drift=True), bad(perf=True)])
    assert evaluate_trigger(history, CFG, now=NOW) is None


def test_drift_wins_when_both() -> None:
    history = streak([bad(drift=True, perf=True)] * 3)
    request = evaluate_trigger(history, CFG, now=NOW)
    assert request is not None
    assert request.reason == "drift"


def test_overrides_force_and_mark() -> None:
    history = streak([bad()] * 3, drift_share=0.05)
    assert evaluate_trigger(history, CFG, now=NOW) is None
    request = evaluate_trigger(
        history, CFG, now=NOW, overrides=ThresholdOverrides(drift_share_max=0.0)
    )
    assert request is not None
    assert request.forced
    assert request.reason == "drift"
    assert request.thresholds == {"drift_share_max": 0.0, "brier_ratio_max": 1.10}
    assert request.metrics == {str(d): 0.05 for d in days(3)}
    assert all(not e.verdict.drift_bad for e in history)

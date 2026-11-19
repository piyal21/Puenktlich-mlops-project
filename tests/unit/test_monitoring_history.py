from datetime import date, timedelta
from pathlib import Path

from dbdelay.monitoring.config import load_monitoring_config
from dbdelay.monitoring.history import HistoryEntry, Verdict, upsert, verdict_for
from dbdelay.monitoring.performance import PerformanceMetrics

CFG = load_monitoring_config(Path(__file__).resolve().parents[2] / "configs" / "monitoring.yaml")
OK = Verdict(drift_bad=False, performance_bad=False, drift_share_max=0.3, brier_ratio_max=1.1)


def entry(day: date, **kw: object) -> HistoryEntry:
    base: dict[str, object] = {
        "day": day,
        "status": "ok",
        "model_version": "1",
        "rows": 12000,
        "brier": 0.15,
        "roc_auc": 0.8,
        "ece": 0.01,
        "late_rate": 0.2,
        "mean_p_late": 0.18,
        "brier_ratio": 1.0,
        "drift_share": 0.0,
        "prediction_drift": 0.02,
        "verdict": OK,
    }
    return HistoryEntry.model_validate(base | kw)


def test_upsert_replaces_same_day() -> None:
    d = date(2026, 11, 15)
    history = upsert([entry(d, rows=1)], entry(d, rows=2), max_days=366)
    assert [e.rows for e in history] == [2]


def test_upsert_sorts_and_caps() -> None:
    days = [date(2026, 11, 1) + timedelta(days=i) for i in range(5)]
    history: list[HistoryEntry] = []
    for d in reversed(days):
        history = upsert(history, entry(d), max_days=3)
    assert [e.day for e in history] == days[-3:]


def test_verdict_none_for_too_few_rows() -> None:
    perf = PerformanceMetrics(rows=10, brier=0.5)
    v = verdict_for(perf, 3.0, None, "too_few_rows", CFG)
    assert not v.drift_bad and not v.performance_bad
    assert v.drift_share_max == 0.30 and v.brier_ratio_max == 1.10


def test_verdict_performance_bad_above_ratio() -> None:
    perf = PerformanceMetrics(rows=5000, brier=0.17)
    assert verdict_for(perf, 1.11, None, "ok", CFG).performance_bad
    assert not verdict_for(perf, 1.10, None, "ok", CFG).performance_bad


def test_history_entry_roundtrips_json() -> None:
    e = entry(date(2026, 11, 15))
    assert HistoryEntry.model_validate_json(e.model_dump_json()) == e

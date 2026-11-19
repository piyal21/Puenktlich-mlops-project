import json
from datetime import UTC, date, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from dbdelay.errors import MonitoringUnavailableError
from dbdelay.monitoring.history import DailyResult, RetrainRecord, Verdict
from dbdelay.monitoring.job import HISTORY_KEY, RETRAINS_KEY, daily_key
from dbdelay.monitoring.performance import PerformanceMetrics
from dbdelay.serving.monitoring import MonitoringSource
from dbdelay.storage import ObjectStore
from tests.unit.test_monitoring_history import entry

DAY = date(2026, 11, 16)
VERDICT = Verdict(drift_bad=False, performance_bad=False, drift_share_max=0.3, brier_ratio_max=1.1)


def daily(day: date) -> DailyResult:
    return DailyResult(
        day=day,
        status="ok",
        model_version="1",
        champion_test_brier=0.14,
        performance=PerformanceMetrics(rows=100, brier=0.15),
        brier_ratio=0.15 / 0.14,
        drift=None,
        verdict=VERDICT,
        excluded_rows={"cancelled": 3},
        computed_at=datetime(2026, 11, 17, 3, 30, tzinfo=UTC),
    )


def retrain() -> RetrainRecord:
    return RetrainRecord(
        request_id="20261120T033100Z-performance",
        reason="performance",
        forced=False,
        requested_at=datetime(2026, 11, 20, 3, 31, tzinfo=UTC),
        decided_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        decision="rejected",
        challenger_version="2",
        champion_before="1",
        champion_after="1",
        failed_checks=["brier_vs_champion"],
    )


def put_monitoring(store: ObjectStore, days: list[date]) -> None:
    store.put_bytes(
        HISTORY_KEY, json.dumps([entry(d).model_dump(mode="json") for d in days]).encode()
    )
    for d in days:
        store.put_bytes(daily_key(d), daily(d).model_dump_json().encode())
    store.put_bytes(RETRAINS_KEY, json.dumps([retrain().model_dump(mode="json")]).encode())


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_empty_before_first_run(s3_store: ObjectStore) -> None:
    view = MonitoringSource(s3_store).get()
    assert view.latest is None
    assert view.history == []
    assert view.retrains == []


def test_full_view_latest_is_newest_day(s3_store: ObjectStore) -> None:
    put_monitoring(s3_store, [DAY - timedelta(days=1), DAY])
    view = MonitoringSource(s3_store).get()
    assert view.latest is not None
    assert view.latest.day == DAY
    assert [e.day for e in view.history] == [DAY - timedelta(days=1), DAY]
    assert view.retrains == [retrain()]


def test_corrupt_history_raises(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(HISTORY_KEY, b"{not json")
    with pytest.raises(MonitoringUnavailableError):
        MonitoringSource(s3_store).get()


def test_cached_for_ttl(s3_store: ObjectStore) -> None:
    clock = Clock()
    source = MonitoringSource(s3_store, ttl_s=300, clock=clock)
    assert source.get().history == []
    put_monitoring(s3_store, [DAY])
    clock.t = 299
    assert source.get().history == []
    clock.t = 301
    assert len(source.get().history) == 1


def test_access_denied_counts_as_missing(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(**_: object) -> None:
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")

    monkeypatch.setattr(s3_store._client, "get_object", denied)
    assert MonitoringSource(s3_store).get().history == []

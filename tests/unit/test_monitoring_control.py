from datetime import UTC, date, datetime, timedelta

from dbdelay.monitoring.control import (
    decision_key,
    mark,
    pending_request,
    read_decisions,
    record_failure,
)
from dbdelay.monitoring.history import RetrainRecord
from dbdelay.monitoring.job import FLAG_KEY
from dbdelay.monitoring.triggers import RetrainRequest
from dbdelay.storage import ObjectStore

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def request(state: str = "requested", *, forced: bool = False) -> RetrainRequest:
    return RetrainRequest.model_validate(
        {
            "request_id": "20261120T033100Z-performance",
            "state": state,
            "reason": "performance",
            "days": [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)],
            "model_version": "1",
            "metrics": {"2026-11-19": 1.2},
            "forced": forced,
            "thresholds": {"drift_share_max": 0.3, "brier_ratio_max": 1.1},
            "requested_at": NOW - timedelta(hours=6),
        }
    )


def put_flag(store: ObjectStore, flag: RetrainRequest) -> None:
    store.put_bytes(FLAG_KEY, flag.model_dump_json().encode())


def test_pending_only_when_requested(s3_store: ObjectStore) -> None:
    assert pending_request(s3_store) is None
    put_flag(s3_store, request())
    assert pending_request(s3_store) == request()
    put_flag(s3_store, request("picked_up"))
    assert pending_request(s3_store) is None


def test_mark_picked_up_sets_time(s3_store: ObjectStore) -> None:
    put_flag(s3_store, request())
    marked = mark(s3_store, request(), "picked_up", NOW)
    assert marked.state == "picked_up"
    assert marked.picked_up_at == NOW
    assert RetrainRequest.model_validate_json(s3_store.get_bytes(FLAG_KEY)) == marked


def test_record_failure_once(s3_store: ObjectStore) -> None:
    first = record_failure(s3_store, request(forced=True), champion="1", now=NOW)
    assert first is not None
    assert first.decision == "failed"
    assert first.forced
    assert first.champion_before == first.champion_after == "1"
    assert record_failure(s3_store, request(), champion="1", now=NOW) is None
    assert (
        RetrainRecord.model_validate_json(s3_store.get_bytes(decision_key(request().request_id)))
        == first
    )


def test_read_decisions_sorted(s3_store: ObjectStore) -> None:
    record_failure(s3_store, request(), champion="1", now=NOW)
    later = request().model_copy(update={"request_id": "20261125T033100Z-drift"})
    record_failure(s3_store, later, champion="1", now=NOW + timedelta(days=5))
    assert [d.request_id for d in read_decisions(s3_store)] == [
        "20261120T033100Z-performance",
        "20261125T033100Z-drift",
    ]

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from dbdelay.data.live_report import live_quality_key, live_silver_key
from dbdelay.monitoring.control import write_decision
from dbdelay.monitoring.history import RetrainRecord
from dbdelay.monitoring.job import FLAG_KEY, RETRAINS_KEY
from dbdelay.monitoring.sync import retrain_main, retrain_publish, retrain_sync
from dbdelay.monitoring.triggers import RetrainRequest
from dbdelay.registry.artifacts import write_bundle
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.storage import ObjectStore
from tests.bundles import bundle_manifest
from tests.unit.test_monitoring_control import request

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
FIRST = date(2026, 11, 13)


@pytest.fixture
def stores(s3_store: ObjectStore) -> tuple[ObjectStore, ObjectStore]:
    """(aws, local): two buckets in one moto mock."""
    s3_store._client.create_bucket(
        Bucket="local", CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
    )
    return s3_store, ObjectStore(s3_store._client, "local")


def _put_live(store: ObjectStore, day: date) -> None:
    store.put_bytes(live_silver_key(day), f"silver {day}".encode())
    store.put_bytes(live_quality_key(day), f"report {day}".encode())


def test_sync_copies_missing_days_only(stores: tuple[ObjectStore, ObjectStore]) -> None:
    aws, local = stores
    for i in range(4):
        _put_live(aws, FIRST + timedelta(days=i))
    local.put_bytes(live_silver_key(FIRST), b"already here")
    local.put_bytes(live_quality_key(FIRST), b"already here")
    report = retrain_sync(aws, local, first_day=FIRST, today=FIRST + timedelta(days=5), now=NOW)
    assert report.days_copied == [FIRST + timedelta(days=i) for i in (1, 2, 3)]
    assert report.days_missing == [FIRST + timedelta(days=4)]
    assert local.get_bytes(live_silver_key(FIRST)) == b"already here"
    assert local.get_bytes(live_quality_key(FIRST + timedelta(days=2))) == b"report 2026-11-15"


def test_sync_picks_up_flag_once(stores: tuple[ObjectStore, ObjectStore]) -> None:
    aws, local = stores
    aws.put_bytes(FLAG_KEY, request().model_dump_json().encode())
    first = retrain_sync(aws, local, first_day=FIRST, today=FIRST, now=NOW)
    assert first.flag == request().request_id
    assert RetrainRequest.model_validate_json(local.get_bytes(FLAG_KEY)).state == "requested"
    in_aws = RetrainRequest.model_validate_json(aws.get_bytes(FLAG_KEY))
    assert (in_aws.state, in_aws.picked_up_at) == ("picked_up", NOW)
    assert retrain_sync(aws, local, first_day=FIRST, today=FIRST, now=NOW).flag is None


def test_sync_keeps_a_running_local_request(stores: tuple[ObjectStore, ObjectStore]) -> None:
    aws, local = stores
    local.put_bytes(FLAG_KEY, request("picked_up").model_dump_json().encode())
    newer = request().model_copy(update={"request_id": "20261125T033100Z-drift"})
    aws.put_bytes(FLAG_KEY, newer.model_dump_json().encode())
    assert retrain_sync(aws, local, first_day=FIRST, today=FIRST, now=NOW).flag is None
    assert RetrainRequest.model_validate_json(aws.get_bytes(FLAG_KEY)).state == "requested"


def _record(decision: str, *, after: str | None = "1", request_id: str = "r1") -> RetrainRecord:
    return RetrainRecord.model_validate(
        {
            "request_id": request_id,
            "reason": "performance",
            "forced": False,
            "requested_at": NOW - timedelta(hours=6),
            "decided_at": NOW,
            "decision": decision,
            "challenger_version": "1",
            "champion_before": None,
            "champion_after": after,
            "failed_checks": [] if decision == "released" else ["brier_vs_champion"],
        }
    )


def _publish(aws: ObjectStore, local: ObjectStore) -> list[RetrainRecord]:
    return retrain_publish(
        local_data=local,
        local_models=local,
        aws_data=aws,
        aws_models=aws,
        local_pointer=ObjectStorePointer(local),
        aws_pointer=ObjectStorePointer(aws),
    )


def test_publish_appends_new_decisions_once(stores: tuple[ObjectStore, ObjectStore]) -> None:
    aws, local = stores
    write_decision(local, _record("rejected"))
    assert [r.request_id for r in _publish(aws, local)] == ["r1"]
    assert _publish(aws, local) == []
    stored = [RetrainRecord.model_validate(r) for r in json.loads(aws.get_bytes(RETRAINS_KEY))]
    assert stored == [_record("rejected")]


def test_publish_releases_only_gate_passed_champion(
    stores: tuple[ObjectStore, ObjectStore], bundle_files: dict[str, bytes]
) -> None:
    aws, local = stores
    write_bundle(local, bundle_manifest(bundle_files), bundle_files)
    ObjectStorePointer(local).set("1", None)
    write_decision(local, _record("released"))
    _publish(aws, local)
    state = ObjectStorePointer(aws).get()
    assert state is not None
    assert state.champion_version == "1"
    assert aws.exists("models/1/manifest.json")


def test_publish_rejected_does_not_touch_pointer(
    stores: tuple[ObjectStore, ObjectStore], bundle_files: dict[str, bytes]
) -> None:
    aws, local = stores
    write_bundle(local, bundle_manifest(bundle_files), bundle_files)
    ObjectStorePointer(local).set("1", None)
    write_decision(local, _record("rejected"))
    _publish(aws, local)
    assert ObjectStorePointer(aws).get() is None


def test_cli_sync_and_publish(
    stores: tuple[ObjectStore, ObjectStore],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    aws, _ = stores
    monkeypatch.setenv("DATA_BUCKET", "local")
    monkeypatch.setenv("MODELS_BUCKET", "local")
    args = ["--data-bucket", aws.bucket, "--models-bucket", aws.bucket]
    assert retrain_main(["sync", *args, "--from", "2026-11-19"]) == 0
    assert capsys.readouterr().out.startswith("copied 0 days")
    assert retrain_main(["publish", *args]) == 0
    assert capsys.readouterr().out == "published nothing\n"

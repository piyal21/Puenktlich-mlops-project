from datetime import UTC, datetime, timedelta

from dbdelay.errors import ExternalServiceError
from dbdelay.live.plans import (
    PLANS_KEY,
    PlanCache,
    add_hour,
    hour_key,
    load_cache,
    missing,
    prune,
    save_cache,
    wanted_hours,
)
from dbdelay.storage import ObjectStore
from dbdelay.timetables.parser import StopEvent

NOW = datetime(2026, 11, 12, 10, 20, tzinfo=UTC)  # 11:20 Berlin (CET)
EVAS = [f"80000{i:02d}" for i in range(10, 40)]  # 30 stations


def stop(stop_id: str, planned: datetime) -> StopEvent:
    return StopEvent(
        stop_id=stop_id,
        ride_id=stop_id.rsplit("-", 1)[0],
        stop_index=int(stop_id.rsplit("-", 1)[1]),
        eva="8000010",
        train_type="RE",
        train_number="1",
        line_number=None,
        planned_utc=planned,
        changed_utc=None,
        cancelled=False,
        planned_platform=None,
        changed_platform=None,
        planned_path=("A",),
        changed_path=None,
        distant_endpoint=None,
    )


def keys(now: datetime) -> list[str]:
    return [hour_key(h) for h in wanted_hours(now)]


def test_wanted_hours_order() -> None:
    assert keys(NOW) == [
        "26111211", "26111212", "26111213", "26111214", "26111215", "26111216", "26111217",
        "26111210", "26111209", "26111208",
    ]  # fmt: skip


def test_wanted_hours_fall_back_day_no_duplicate() -> None:
    now = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST, before clocks go back
    got = keys(now)
    assert len(got) == len(set(got))
    assert got[0] == "26102502"
    assert got[1] == "26102503"  # 02 CET (repeated hour) is the same key, skipped


def test_wanted_hours_spring_forward_skips_missing_hour() -> None:
    now = datetime(2027, 3, 28, 0, 30, tzinfo=UTC)  # 01:30 CET
    got = keys(now)
    assert got[:2] == ["27032801", "27032803"]
    assert "27032802" not in got


def test_missing_respects_budget_and_order() -> None:
    todo = missing(PlanCache(), EVAS, NOW, budget=70)
    assert len(todo) == 70
    assert {hour_key(h) for _, h in todo[:30]} == {"26111211"}
    assert {hour_key(h) for _, h in todo[30:60]} == {"26111212"}
    assert [eva for eva, _ in todo[:30]] == EVAS


def test_missing_skips_fetched_hours() -> None:
    cache = PlanCache(fetched={eva: keys(NOW) for eva in EVAS[1:]})
    todo = missing(cache, EVAS, NOW, budget=70)
    assert [eva for eva, _ in todo] == [EVAS[0]] * 10


def test_add_hour_and_prune_drop_old_hours() -> None:
    old_hour = wanted_hours(NOW - timedelta(hours=5))[0]  # 06 Berlin, outside H-3
    old = stop("-1-2611120500-1", datetime(2026, 11, 12, 5, 30, tzinfo=UTC))
    fresh = stop("-2-2611121000-1", datetime(2026, 11, 12, 10, 30, tzinfo=UTC))
    cache = add_hour(PlanCache(), "8000010", old_hour, [old], NOW - timedelta(hours=5))
    cache = add_hour(cache, "8000010", wanted_hours(NOW)[0], [fresh], NOW)
    pruned = prune(cache, NOW)
    assert pruned.fetched == {"8000010": ["26111211"]}
    assert [s.stop_id for s in pruned.stops] == [fresh.stop_id]
    assert set(pruned.seen_at) == {fresh.stop_id}


def test_add_hour_replaces_same_stop() -> None:
    first = stop("-2-2611121000-1", datetime(2026, 11, 12, 10, 30, tzinfo=UTC))
    again = first.model_copy(update={"planned_platform": "5"})
    hour = wanted_hours(NOW)[0]
    cache = add_hour(PlanCache(), "8000010", hour, [first], NOW)
    cache = add_hour(cache, "8000010", hour, [again], NOW + timedelta(minutes=15))
    assert [s.planned_platform for s in cache.stops] == ["5"]
    assert cache.fetched["8000010"] == ["26111211"]


def test_cache_roundtrip(s3_store: ObjectStore) -> None:
    fresh = stop("-2-2611121000-1", datetime(2026, 11, 12, 10, 30, tzinfo=UTC))
    cache = add_hour(PlanCache(), "8000010", wanted_hours(NOW)[0], [fresh], NOW)
    save_cache(s3_store, cache)
    assert load_cache(s3_store) == cache


def test_missing_or_invalid_cache_reads_as_empty(s3_store: ObjectStore) -> None:
    assert load_cache(s3_store) == PlanCache()
    s3_store.put_bytes(PLANS_KEY, b"not gzip")
    assert load_cache(s3_store) == PlanCache()


class _NoReadStore(ObjectStore):
    """S3 without s3:ListBucket answers 403 (not 404) for a missing object."""

    def get_bytes(self, key: str) -> bytes:
        raise ExternalServiceError(f"get {key} failed")


def test_unreadable_cache_reads_as_empty(s3_store: ObjectStore) -> None:
    store = _NoReadStore(s3_store._client, s3_store.bucket)
    assert load_cache(store) == PlanCache()

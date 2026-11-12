from datetime import UTC, datetime, timedelta
from typing import Any

from dbdelay.data.stations import Station
from dbdelay.live.board import build_live_board
from dbdelay.live.plans import PlanCache, add_hour, wanted_hours
from dbdelay.serving.board import LiveBoard
from dbdelay.timetables.parser import StopEvent

NOW = datetime(2026, 11, 12, 10, 20, tzinfo=UTC)
FETCHED = NOW - timedelta(minutes=1)
KIEL, FFM = "8000199", "8000105"
STATIONS = [
    Station(eva=KIEL, name="Kiel Hbf", state="SH"),
    Station(eva=FFM, name="Frankfurt(Main)Hbf", state="HE"),
]


def event(n: int, minutes: int, eva: str = KIEL, **overrides: Any) -> StopEvent:
    fields: dict[str, Any] = {
        "stop_id": f"-{n}-2611120900-2",
        "ride_id": f"-{n}-2611120900",
        "stop_index": 2,
        "eva": eva,
        "train_type": "RE",
        "train_number": str(n),
        "line_number": "RE7",
        "planned_utc": NOW + timedelta(minutes=minutes),
        "changed_utc": None,
        "cancelled": False,
        "planned_platform": "2",
        "changed_platform": None,
        "planned_path": ("Neumünster", "Hamburg Hbf"),
        "changed_path": None,
        "distant_endpoint": None,
    }
    return StopEvent(**(fields | overrides))


def change(n: int, eva: str = KIEL, **overrides: Any) -> StopEvent:
    base = {"planned_utc": None, "train_type": None, "train_number": None, "planned_path": ()}
    return event(n, 0, eva, **(base | overrides))


def cache_of(*events: StopEvent) -> PlanCache:
    cache = PlanCache()
    for e in events:
        cache = add_hour(cache, e.eva, wanted_hours(NOW)[0], [e], FETCHED)
    return cache


def build(
    cache: PlanCache,
    changes: dict[str, list[StopEvent]] | None = None,
    failed: frozenset[str] = frozenset(),
    previous: LiveBoard | None = None,
) -> LiveBoard:
    return build_live_board(
        cache,
        changes or {},
        failed=failed,
        previous=previous,
        stations=STATIONS,
        now=NOW,
        fetched_at=FETCHED,
    )


def test_board_source_is_live() -> None:
    board = build(cache_of(event(1, 10)))
    assert board.source == "live"
    assert board.replayed_from is None
    assert board.generated_at == FETCHED
    (dep,) = board.departures
    assert dep.station_name == "Kiel Hbf"
    assert dep.final_destination == "Hamburg Hbf"
    assert dep.delay_min == 0


def test_delay_and_platform_from_change() -> None:
    planned = NOW + timedelta(minutes=10)
    changes = {KIEL: [change(1, changed_utc=planned + timedelta(minutes=8), changed_platform="5")]}
    (dep,) = build(cache_of(event(1, 10)), changes).departures
    assert dep.delay_min == 8
    assert dep.changed_departure_utc == planned + timedelta(minutes=8)
    assert dep.platform == "5"


def test_window_keeps_delayed_train_planned_40_min_ago() -> None:
    late = event(1, -40)
    gone = event(2, -40)
    far = event(3, 6 * 60 + 5)
    changes = {KIEL: [change(1, changed_utc=late.planned_utc + timedelta(minutes=30))]}  # type: ignore[operator]
    board = build(cache_of(late, gone, far), changes)
    assert [d.train_number for d in board.departures] == ["1"]


def test_cancelled_kept_with_null_delay() -> None:
    changes = {KIEL: [change(1, cancelled=True, changed_path=())]}
    (dep,) = build(cache_of(event(1, 10)), changes).departures
    assert dep.is_cancelled
    assert dep.delay_min is None


def test_via_from_planned_path_and_changed_path() -> None:
    changes = {FFM: [change(2, FFM, changed_path=("Hanau Hbf", "Fulda"))]}
    board = build(cache_of(event(1, 10), event(2, 12, FFM)), changes)
    vias = {d.eva: d.via for d in board.departures}
    assert vias == {KIEL: ("Neumünster", "Hamburg Hbf"), FFM: ("Hanau Hbf", "Fulda")}


def test_failed_station_keeps_previous_rows() -> None:
    first = build(cache_of(event(1, 10), event(2, 12, FFM)))
    # Next run: Frankfurt's fchg call failed and its plan is gone from the cache view.
    board = build(cache_of(event(1, 10)), failed=frozenset({FFM}), previous=first)
    assert {d.eva for d in board.departures} == {KIEL, FFM}
    ffm = next(d for d in board.departures if d.eva == FFM)
    assert ffm == next(d for d in first.departures if d.eva == FFM)


def test_failed_station_previous_rows_refiltered_to_window() -> None:
    first = build(cache_of(event(2, -40, FFM)))
    assert first.departures == ()
    board = build(cache_of(), failed=frozenset({FFM}), previous=first)
    assert board.departures == ()


def test_unknown_station_and_missing_category_skipped() -> None:
    board = build(cache_of(event(1, 10, "8000207"), event(2, 10, train_type=None)))
    assert board.departures == ()


def test_sorted_by_planned_time() -> None:
    board = build(cache_of(event(1, 30), event(2, 10)))
    assert [d.train_number for d in board.departures] == ["2", "1"]

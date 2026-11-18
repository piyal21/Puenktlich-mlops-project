from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from pydantic import SecretStr

from dbdelay.config import Settings
from dbdelay.data.stations import Station
from dbdelay.errors import ConfigError, ExternalServiceError
from dbdelay.live.ingest import run_ingest
from dbdelay.live.plans import PLANS_KEY, load_cache
from dbdelay.live.secrets import load_db_api_credentials
from dbdelay.serving.board import board_from_bytes, board_to_bytes
from dbdelay.storage import ObjectStore
from dbdelay.timetables.bronze import iter_records
from dbdelay.timetables.client import ApiResponse
from tests.boards import departure, live_board

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
KIEL, FFM = "8000199", "8000105"
STATIONS = [
    Station(eva=KIEL, name="Kiel Hbf", state="SH"),
    Station(eva=FFM, name="Frankfurt(Main)Hbf", state="HE"),
]
# The Kiel fixtures were captured 2026-09-25 ~17:00 Berlin; the plan is for hour 17.
NOW = datetime(2026, 9, 25, 15, 10, tzinfo=UTC)
BOARD = "live/boards/candidate.json.gz"


class FakeApi:
    def __init__(self, fchg: dict[str, ApiResponse | Exception]) -> None:
        self._fchg = fchg
        self.plan_calls: list[tuple[str, str]] = []

    def fchg(self, eva: str) -> ApiResponse:
        result = self._fchg[eva]
        if isinstance(result, Exception):
            raise result
        return result

    def plan(self, eva: str, hour_local: datetime) -> ApiResponse:
        self.plan_calls.append((eva, f"{hour_local:%y%m%d%H}"))
        if eva == KIEL and f"{hour_local:%y%m%d%H}" == "26092517":
            return ApiResponse(200, (FIXTURES / "sample_plan.xml").read_bytes())
        return ApiResponse(200, b"<timetable/>")


def ok(name: str) -> ApiResponse:
    return ApiResponse(200, (FIXTURES / name).read_bytes())


def ingest(store: ObjectStore, api: FakeApi, budget: int = 70) -> Any:
    return run_ingest(store, api, STATIONS, now=NOW, board_key=BOARD, plan_budget=budget)


def test_ingest_writes_bronze_cache_and_board(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<timetable/>")})
    result = ingest(s3_store, api)
    assert result.fchg_ok == 2
    assert result.failed_stations == ()
    assert result.plan_hours_fetched == 20  # 10 hours x 2 stations
    assert result.calls == 22
    assert result.events > 0
    keys = sorted(s3_store.iter_keys("bronze/timetables/"))
    assert keys == [
        "bronze/timetables/kind=fchg/date=2026-09-25/run=2026-09-25T151000Z.jsonl.gz",
        "bronze/timetables/kind=plan/date=2026-09-25/run=2026-09-25T151000Z.jsonl.gz",
    ]
    fchg = list(iter_records(s3_store.get_bytes(keys[0])))
    assert [(r.eva, r.status) for r in fchg] == [(KIEL, 200), (FFM, 200)]
    assert len(load_cache(s3_store).fetched[KIEL]) == 10
    board = board_from_bytes(s3_store.get_bytes(BOARD))
    assert board.source == "live"
    assert result.board_rows == len(board.departures) > 0


def test_ingest_counts_html_body_as_failed(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<html>oops</html>")})
    result = ingest(s3_store, api)
    assert result.failed_stations == (FFM,)
    assert result.fchg_ok == 1
    fchg_key = next(iter(s3_store.iter_keys("bronze/timetables/kind=fchg/")))
    ffm = [r for r in iter_records(s3_store.get_bytes(fchg_key)) if r.eva == FFM]
    assert ffm[0].error == "DataValidationError"


def test_ingest_non_200_and_connection_errors_fail_station(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ApiResponse(503, b""), FFM: ExternalServiceError("down")})
    with pytest.raises(ExternalServiceError):
        ingest(s3_store, api)
    # bronze is written even when every fchg call failed; the board is left alone
    assert len(list(s3_store.iter_keys("bronze/timetables/kind=fchg/"))) == 1
    assert not s3_store.exists(BOARD)


def test_plan_budget_limits_calls(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<timetable/>")})
    result = ingest(s3_store, api, budget=3)
    assert result.plan_hours_fetched == 3
    assert api.plan_calls[:2] == [(KIEL, "26092517"), (FFM, "26092517")]


def test_second_run_fetches_only_new_hours(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<timetable/>")})
    ingest(s3_store, api)
    again = ingest(s3_store, api)
    assert again.plan_hours_fetched == 0
    assert s3_store.exists(PLANS_KEY)


@pytest.fixture
def ssm() -> Iterator[Any]:
    with mock_aws():
        yield boto3.client("ssm", region_name="eu-north-1")


def test_credentials_from_env_values() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, db_api_client_id=SecretStr("cid"), db_api_key=SecretStr("key")
    )
    assert load_db_api_credentials(settings, None) == ("cid", "key")


def test_credentials_from_ssm(ssm: Any) -> None:
    ssm.put_parameter(Name="/p/client_id", Value="cid-ssm", Type="SecureString")
    ssm.put_parameter(Name="/p/api_key", Value="key-ssm", Type="SecureString")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, db_api_client_id_param="/p/client_id", db_api_key_param="/p/api_key"
    )
    assert load_db_api_credentials(settings, ssm) == ("cid-ssm", "key-ssm")


def test_credentials_missing_ssm_parameter_message_has_names_only(ssm: Any) -> None:
    ssm.put_parameter(Name="/p/client_id", Value="cid-ssm", Type="SecureString")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, db_api_client_id_param="/p/client_id", db_api_key_param="/p/api_key"
    )
    with pytest.raises(ConfigError, match="/p/api_key") as exc_info:
        load_db_api_credentials(settings, ssm)
    assert "cid-ssm" not in str(exc_info.value)


def test_credentials_missing_entirely() -> None:
    with pytest.raises(ConfigError, match="DB_API_CLIENT_ID"):
        load_db_api_credentials(Settings(_env_file=None), None)  # type: ignore[call-arg]


def test_no_plan_calls_when_every_fchg_call_failed(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ExternalServiceError("down"), FFM: ApiResponse(503, b"")})
    with pytest.raises(ExternalServiceError):
        ingest(s3_store, api)
    assert api.plan_calls == []


class _FirstRunStore(ObjectStore):
    """First run without s3:ListBucket: missing keys read as 403 (ExternalServiceError)."""

    def get_bytes(self, key: str) -> bytes:
        if key in {PLANS_KEY, BOARD}:
            denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")
            raise ExternalServiceError(f"get {key} failed") from denied
        return super().get_bytes(key)


def test_first_run_without_list_permission_succeeds(s3_store: ObjectStore) -> None:
    store = _FirstRunStore(s3_store._client, s3_store.bucket)
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<timetable/>")})
    result = ingest(store, api)
    assert result.fchg_ok == 2
    assert s3_store.exists(BOARD)


def test_ingest_stops_starting_calls_when_time_runs_short(s3_store: ObjectStore) -> None:
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<timetable/>")})
    left = iter([300.0, 10.0])  # plenty for the first fchg call, then almost nothing

    result = run_ingest(
        s3_store, api, STATIONS, now=NOW, board_key=BOARD, plan_budget=70,
        time_left=lambda: next(left, 10.0),
    )  # fmt: skip
    assert result.fchg_ok == 1
    assert result.failed_stations == (FFM,)
    assert api.plan_calls == []
    assert s3_store.exists(BOARD)  # what was fetched is still written


class _RateLimitedPlans(FakeApi):
    def plan(self, eva: str, hour_local: datetime) -> ApiResponse:
        self.plan_calls.append((eva, f"{hour_local:%y%m%d%H}"))
        return ApiResponse(429, b"")


def test_plan_top_up_stops_after_a_429(s3_store: ObjectStore) -> None:
    api = _RateLimitedPlans({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(200, b"<timetable/>")})
    result = ingest(s3_store, api)
    assert len(api.plan_calls) == 1
    assert result.plan_hours_fetched == 0


def test_sample_board_rows_are_never_copied_into_a_live_board(s3_store: ObjectStore) -> None:
    sample = live_board(departure(5, eva=FFM, planned_departure_utc=NOW + timedelta(minutes=5)))
    s3_store.put_bytes(BOARD, board_to_bytes(sample))
    api = FakeApi({KIEL: ok("sample_fchg.xml"), FFM: ApiResponse(503, b"")})
    ingest(s3_store, api)
    board = board_from_bytes(s3_store.get_bytes(BOARD))
    assert board.source == "live"
    assert all(d.eva != FFM for d in board.departures)

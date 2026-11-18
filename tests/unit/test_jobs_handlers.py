import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from moto import mock_aws

import etl_handler
import ingest_handler
from dbdelay.config import Settings
from dbdelay.errors import ConfigError, ExternalServiceError
from dbdelay.live import jobs
from dbdelay.live.ingest import IngestResult
from dbdelay.live.quality import LiveQualityReport


def _metric_lines(text: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    return [line for line in lines if "_aws" in line]


def test_yesterday_is_the_utc_day_before() -> None:
    assert jobs.yesterday(datetime(2026, 11, 13, 2, 30, tzinfo=UTC)) == date(2026, 11, 12)


def test_ingest_handler_returns_counts_and_metric(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = IngestResult(
        calls=60, fchg_ok=30, failed_stations=(), plan_hours_fetched=30, events=4321, board_rows=900
    )
    monkeypatch.setattr(ingest_handler, "make_client", lambda settings: object())
    monkeypatch.setattr(
        ingest_handler, "ingest_job", lambda settings, client, now, time_left: result
    )
    out = ingest_handler.handler({}, SimpleNamespace(aws_request_id="r"))
    assert out["events"] == 4321
    assert out["failed_stations"] == []
    (metric,) = _metric_lines(capsys.readouterr().out)
    assert metric["EventsIngested"] == [4321.0]  # Powertools EMF writes value lists
    assert metric["service"] == "ingest"
    assert metric["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Puenktlich"


def test_ingest_handler_builds_the_client_once(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[int] = []
    result = IngestResult(1, 1, (), 0, 0, 0)
    monkeypatch.setattr(ingest_handler, "_client", None)

    def fake_client(settings: object) -> object:
        made.append(1)
        return object()

    monkeypatch.setattr(ingest_handler, "make_client", fake_client)
    monkeypatch.setattr(
        ingest_handler, "ingest_job", lambda settings, client, now, time_left: result
    )
    ingest_handler.handler({}, None)
    ingest_handler.handler({}, None)
    assert made == [1]


def _report(day: date, empty: list[str]) -> LiveQualityReport:
    return LiveQualityReport(
        day=day, rows_out=0, drops={}, late_rate=None, cancelled_rate=None, stations=[],
        low_volume_hours=[], fchg_runs_ok={}, longest_fchg_gap_min={}, plan_hours_missing={},
        never_seen_in_fchg=0, fchg_only_stops=0, empty_stations=empty, content_hash="x",
    )  # fmt: skip


def test_etl_handler_defaults_to_yesterday_and_emits_empty_stations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    days: list[date] = []

    def fake(settings: object, day: date) -> LiveQualityReport:
        days.append(day)
        return _report(day, ["8000199"])

    monkeypatch.setattr(etl_handler, "etl_job", fake)
    out = etl_handler.handler({}, None)
    assert days == [jobs.yesterday(datetime.now(UTC))]
    assert out["empty_stations"] == ["8000199"]
    (metric,) = _metric_lines(capsys.readouterr().out)
    assert metric["EmptyStations"] == [1.0]
    assert metric["service"] == "etl_daily"


def test_etl_handler_takes_a_day_from_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    days: list[date] = []

    def fake(settings: object, day: date) -> LiveQualityReport:
        days.append(day)
        return _report(day, [])

    monkeypatch.setattr(etl_handler, "etl_job", fake)
    etl_handler.handler({"day": "2026-11-14"}, None)
    assert days == [date(2026, 11, 14)]


def test_make_client_reads_keys_from_ssm_when_only_names_are_set() -> None:
    with mock_aws():
        ssm = boto3.client("ssm", region_name="eu-north-1")
        ssm.put_parameter(Name="/p/client_id", Value="cid", Type="SecureString")
        ssm.put_parameter(Name="/p/api_key", Value="key", Type="SecureString")
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None, db_api_client_id_param="/p/client_id", db_api_key_param="/p/api_key"
        )
        client = jobs.make_client(settings)
    assert "TimetablesClient" in repr(client)
    assert "key" not in repr(client).replace("TimetablesClient", "")


def test_make_client_without_any_keys_fails_clearly() -> None:
    with pytest.raises(ConfigError, match="DB_API_CLIENT_ID"):
        jobs.make_client(Settings(_env_file=None))  # type: ignore[call-arg]


def test_ingest_handler_drops_the_client_after_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # e.g. keys rotated in SSM: every call 401 → run fails → next invocation reloads the keys
    monkeypatch.setattr(ingest_handler, "_client", object())

    def boom(settings: object, client: object, now: object, time_left: object = None) -> object:
        raise ExternalServiceError("all 30 fchg calls failed")

    monkeypatch.setattr(ingest_handler, "ingest_job", boom)
    with pytest.raises(ExternalServiceError):
        ingest_handler.handler({}, None)
    assert ingest_handler._client is None

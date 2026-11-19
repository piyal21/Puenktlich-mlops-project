"""Wiring for the live jobs (Lambda handlers, laptop CLIs): settings → client, store, stations."""

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import boto3

from dbdelay.config import Settings
from dbdelay.data.live_report import LiveQualityReport
from dbdelay.data.stations import load_stations
from dbdelay.live.etl import run_etl
from dbdelay.live.ingest import IngestResult, TimetablesApi, run_ingest
from dbdelay.live.secrets import load_db_api_credentials
from dbdelay.storage import BOTO_CONFIG, ObjectStore, make_s3_client
from dbdelay.timetables.client import TimetablesClient
from dbdelay.timetables.ratelimit import RateLimiter


def yesterday(now: datetime) -> date:
    return (now.astimezone(UTC) - timedelta(days=1)).date()


def make_client(settings: Settings) -> TimetablesClient:
    """DB API client; keys from `.env` values or, if only parameter names are set, from SSM."""
    ssm = None
    if settings.db_api_client_id is None and settings.db_api_client_id_param is not None:
        ssm = boto3.client("ssm", region_name=settings.aws_region, config=BOTO_CONFIG)
    client_id, api_key = load_db_api_credentials(settings, ssm)
    return TimetablesClient(
        client_id,
        api_key,
        limiter=RateLimiter(settings.api_calls_per_min),
        base_url=settings.db_api_base_url,
    )


def _store(settings: Settings) -> ObjectStore:
    return ObjectStore(make_s3_client(settings), settings.data_bucket)


def ingest_job(
    settings: Settings,
    client: TimetablesApi,
    now: datetime,
    time_left: Callable[[], float] = lambda: math.inf,
) -> IngestResult:
    return run_ingest(
        _store(settings),
        client,
        load_stations(settings.stations_file),
        now=now,
        board_key=settings.board_key,
        plan_budget=settings.plan_call_budget,
        time_left=time_left,
    )


def etl_job(settings: Settings, day: date) -> LiveQualityReport:
    return run_etl(_store(settings), load_stations(settings.stations_file), day)

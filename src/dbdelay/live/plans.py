"""Plan cache `live/plans/latest.json.gz`: parsed `plan` stops for Berlin hours H-3 … H+6.

Each 15-min ingest run fetches only the station-hours that are missing, nearest hour first, within
a call budget; after an outage the cache fills up again over a few runs.
"""

import gzip
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from botocore.exceptions import ClientError
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from dbdelay.errors import ExternalServiceError, NotFoundError
from dbdelay.logging import get_logger
from dbdelay.storage import ObjectStore
from dbdelay.timetables.parser import BERLIN, StopEvent

PLANS_KEY = "live/plans/latest.json.gz"
AHEAD_HOURS = 6
BEHIND_HOURS = 3


class PlanCache(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    fetched: dict[str, list[str]] = {}  # eva -> Berlin "YYMMDDHH" hours fetched
    stops: list[StopEvent] = []
    seen_at: dict[str, AwareDatetime] = {}  # stop_id -> fetch time of its plan


def hour_key(hour: datetime) -> str:
    """Berlin ``YYMMDDHH`` of an aware time."""
    return f"{hour.astimezone(BERLIN):%y%m%d%H}"


def wanted_hours(now: datetime) -> list[datetime]:
    """Berlin hour starts in fetch order: H, H+1 … H+6, then H-1 … H-3 (each local hour once).

    Stepping in UTC hours handles DST: the repeated autumn hour maps to one key, the skipped
    spring hour never appears.
    """
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    offsets = [*range(AHEAD_HOURS + 1), *range(-1, -BEHIND_HOURS - 1, -1)]
    hours: list[datetime] = []
    seen: set[str] = set()
    for offset in offsets:
        local = (start + timedelta(hours=offset)).astimezone(BERLIN)
        key = hour_key(local)
        if key not in seen:
            seen.add(key)
            hours.append(local.replace(minute=0, second=0, microsecond=0))
    return hours


def missing(
    cache: PlanCache, evas: Sequence[str], now: datetime, budget: int
) -> list[tuple[str, datetime]]:
    """(eva, hour) pairs not yet fetched, hour by hour, at most ``budget``."""
    todo: list[tuple[str, datetime]] = []
    for hour in wanted_hours(now):
        key = hour_key(hour)
        for eva in evas:
            if len(todo) >= budget:
                return todo
            if key not in cache.fetched.get(eva, []):
                todo.append((eva, hour))
    return todo


def add_hour(
    cache: PlanCache,
    eva: str,
    hour: datetime,
    events: Sequence[StopEvent],
    seen_at: datetime,
) -> PlanCache:
    """Cache with one more fetched station-hour; a re-fetched stop replaces the old one."""
    key = hour_key(hour)
    fetched = {e: list(hours) for e, hours in cache.fetched.items()}
    if key not in fetched.setdefault(eva, []):
        fetched[eva].append(key)
    new_ids = {e.stop_id for e in events}
    stops = [s for s in cache.stops if s.stop_id not in new_ids] + list(events)
    seen = dict(cache.seen_at) | dict.fromkeys(new_ids, seen_at)
    return PlanCache(fetched=fetched, stops=stops, seen_at=seen)


def prune(cache: PlanCache, now: datetime) -> PlanCache:
    """Forget hours (and their stops) outside H-3 … H+6."""
    wanted = wanted_hours(now)
    keys = {hour_key(h) for h in wanted}
    earliest = min(wanted)
    fetched = {e: [k for k in hours if k in keys] for e, hours in cache.fetched.items()}
    stops = [s for s in cache.stops if s.planned_utc is not None and s.planned_utc >= earliest]
    kept = {s.stop_id for s in stops}
    return PlanCache(
        fetched={e: hours for e, hours in fetched.items() if hours},
        stops=stops,
        seen_at={k: v for k, v in cache.seen_at.items() if k in kept},
    )


def load_cache(store: ObjectStore) -> PlanCache:
    """The stored cache; empty when missing or unreadable (the next runs refill it).

    The ingest role has no s3:ListBucket, so S3 answers 403 (not 404) for a missing object.
    """
    try:
        data = store.get_bytes(PLANS_KEY)
    except NotFoundError:
        return PlanCache()
    except ExternalServiceError as exc:
        if not _access_denied(exc):
            raise  # a real storage problem: keep the good cache, fail this run
        return PlanCache()
    try:
        return PlanCache.model_validate_json(gzip.decompress(data))
    except (OSError, EOFError, ValidationError):
        get_logger("ingest").warning("plan cache unreadable, starting empty")
        return PlanCache()


def _access_denied(exc: ExternalServiceError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, ClientError) and cause.response.get("Error", {}).get("Code") in {
        "AccessDenied",
        "403",
    }


def save_cache(store: ObjectStore, cache: PlanCache) -> None:
    data = gzip.compress(cache.model_dump_json().encode("utf-8"), mtime=0)
    store.put_bytes(PLANS_KEY, data, "application/gzip")

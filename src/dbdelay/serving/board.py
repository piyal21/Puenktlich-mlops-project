"""Live board `live/boards/latest.json.gz` (schema version 1): contract, cached reader, selection.

`make seed` writes sample boards (Phase 5); live ingestion writes the same format (Phase 7).
"""

import gzip
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from dbdelay.errors import DataValidationError, ExternalServiceError, NotFoundError
from dbdelay.logging import get_logger
from dbdelay.storage import ObjectStore

BOARD_KEY = "live/boards/latest.json.gz"
Clock = Callable[[], float]


class BoardDeparture(BaseModel):
    """One departure: silver columns (architecture §3.3) plus the platform."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=r"^[0-9a-f]{40}$")
    eva: str = Field(pattern=r"^[1-9][0-9]{6}$")
    station_name: str
    ride_id: str
    stop_index: int = Field(ge=1, le=32767)
    train_type: str = Field(min_length=1)
    train_number: str | None = None
    line_number: str | None = None
    final_destination: str | None = None
    planned_departure_utc: AwareDatetime
    changed_departure_utc: AwareDatetime | None = None
    delay_min: int | None = None
    is_cancelled: bool
    platform: str | None = None

    @field_validator("planned_departure_utc", "changed_departure_utc")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @property
    def leaves_at(self) -> datetime:
        """Best known departure time: the changed time if reported, else the planned one."""
        return self.changed_departure_utc or self.planned_departure_utc


class LiveBoard(BaseModel):
    """All supported stations' departures around ``generated_at``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    generated_at: AwareDatetime
    source: Literal["sample", "live"]
    replayed_from: date | None = None
    departures: tuple[BoardDeparture, ...]

    @field_validator("generated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def board_to_bytes(board: LiveBoard) -> bytes:
    """Gzipped JSON; ``mtime=0`` keeps identical boards byte-identical."""
    return gzip.compress(board.model_dump_json().encode("utf-8"), mtime=0)


def board_from_bytes(data: bytes) -> LiveBoard:
    """Parse and validate a board file.

    Raises:
        DataValidationError: not gzip, not JSON, or not schema version 1.
    """
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError) as exc:
        raise DataValidationError("live board is not gzip data") from exc
    try:
        return LiveBoard.model_validate_json(raw)
    except ValidationError as exc:
        raise DataValidationError(f"live board invalid: {exc.error_count()} errors") from None


class BoardSource:
    """Thread-safe board cache; on a failed refresh the last good board is kept."""

    def __init__(
        self,
        store: ObjectStore,
        key: str = BOARD_KEY,
        *,
        ttl_s: float = 60.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self._store = store
        self._key = key
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._board: LiveBoard | None = None
        self._loaded_at: float | None = None

    def get(self) -> LiveBoard:
        """The current board.

        Raises:
            ExternalServiceError: the board is missing, invalid or unreachable and none is cached.
        """
        with self._lock:
            now = self._clock()
            if (
                self._board is not None
                and self._loaded_at is not None
                and now - self._loaded_at < self._ttl_s
            ):
                return self._board
            try:
                board = board_from_bytes(self._store.get_bytes(self._key))
            except (NotFoundError, DataValidationError, ExternalServiceError) as exc:
                if self._board is None:
                    raise ExternalServiceError(f"live board unavailable: {exc}") from exc
                get_logger("api").warning(
                    "live board refresh failed; serving the cached board", extra={"error": str(exc)}
                )
                self._loaded_at = now
                return self._board
            self._board, self._loaded_at = board, now
            return board

    def generated_at_or_none(self) -> datetime | None:
        """``generated_at`` of the current board, or ``None`` (for `/health`; never raises)."""
        try:
            return self.get().generated_at
        except ExternalServiceError:
            return None


def select_departures(
    board: LiveBoard, eva: str, now: datetime, hours: int
) -> list[BoardDeparture]:
    """Departures of one station still to leave (planned or changed time >= ``now``) and planned
    within ``hours`` from ``now``, sorted by planned time."""
    end = now + timedelta(hours=hours)
    rows = [
        d
        for d in board.departures
        if d.eva == eva and d.planned_departure_utc <= end and d.leaves_at >= now
    ]
    return sorted(rows, key=lambda d: (d.planned_departure_utc, d.event_id))


def is_stale(board: LiveBoard, now: datetime, stale_after_s: int) -> bool:
    return (now - board.generated_at).total_seconds() > stale_after_s

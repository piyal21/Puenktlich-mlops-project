"""Retrain flag states and decision records (spec §4.5).

The flag moves `requested` → `picked_up` → `done` instead of being deleted (`ObjectStore` has
no delete by design). Decisions live in `control/decisions/<request_id>.json` (local MinIO) and
are published to `monitoring/retrains.json` by `make retrain-publish`.
"""

from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from dbdelay.errors import DataValidationError
from dbdelay.monitoring.history import RetrainRecord
from dbdelay.monitoring.job import FLAG_KEY, read_flag
from dbdelay.monitoring.triggers import RetrainRequest
from dbdelay.storage import ObjectStore, get_optional_bytes

DECISIONS_PREFIX = "control/decisions/"
JSON = "application/json"


def decision_key(request_id: str) -> str:
    return f"{DECISIONS_PREFIX}{request_id}.json"


def pending_request(store: ObjectStore) -> RetrainRequest | None:
    """The flag when it waits to be picked up, else None."""
    flag = read_flag(store)
    return flag if flag is not None and flag.state == "requested" else None


def mark(
    store: ObjectStore,
    request: RetrainRequest,
    state: Literal["picked_up", "done"],
    now: datetime,
) -> RetrainRequest:
    """Write the flag with a new state (and the pick-up time)."""
    update: dict[str, object] = {"state": state}
    if state == "picked_up":
        update["picked_up_at"] = now
    marked = request.model_copy(update=update)
    store.put_bytes(FLAG_KEY, marked.model_dump_json(indent=2).encode(), JSON)
    return marked


def write_decision(store: ObjectStore, record: RetrainRecord) -> None:
    store.put_bytes(
        decision_key(record.request_id), record.model_dump_json(indent=2).encode(), JSON
    )


def record_failure(
    store: ObjectStore, request: RetrainRequest, *, champion: str | None, now: datetime
) -> RetrainRecord | None:
    """A `failed` decision when the pipeline wrote none (e.g. it crashed before the gate)."""
    if get_optional_bytes(store, decision_key(request.request_id)) is not None:
        return None
    record = RetrainRecord(
        request_id=request.request_id,
        reason=request.reason,
        forced=request.forced,
        requested_at=request.requested_at,
        decided_at=now,
        decision="failed",
        challenger_version=None,
        champion_before=champion,
        champion_after=champion,
        failed_checks=[],
    )
    write_decision(store, record)
    return record


def read_decisions(store: ObjectStore) -> list[RetrainRecord]:
    """All local decision records, oldest request first.

    Raises:
        DataValidationError: if a stored record is invalid.
    """
    records = []
    for key in sorted(store.iter_keys(DECISIONS_PREFIX)):
        try:
            records.append(RetrainRecord.model_validate_json(store.get_bytes(key)))
        except ValidationError:
            raise DataValidationError(f"{key} is not a valid decision record") from None
    return records

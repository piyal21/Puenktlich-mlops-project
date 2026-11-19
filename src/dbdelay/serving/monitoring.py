"""Monitoring results for the health page (`GET /api/v1/monitoring`, Phase 9)."""

import json
import threading
import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.errors import DataValidationError, ExternalServiceError, MonitoringUnavailableError
from dbdelay.monitoring.history import DailyResult, HistoryEntry, RetrainRecord
from dbdelay.monitoring.job import RETRAINS_KEY, daily_key, read_history
from dbdelay.storage import ObjectStore, get_optional_bytes


class MonitoringView(BaseModel):
    model_config = ConfigDict(frozen=True)

    latest: DailyResult | None
    history: list[HistoryEntry]
    retrains: list[RetrainRecord]


def load_view(store: ObjectStore) -> MonitoringView:
    """Missing files mean "not yet": empty lists and no latest day.

    Raises:
        MonitoringUnavailableError: a stored file is invalid, or storage fails.
    """
    try:
        history = read_history(store)
        latest = None
        if history:
            raw = get_optional_bytes(store, daily_key(history[-1].day))
            latest = None if raw is None else DailyResult.model_validate_json(raw)
        raw_retrains = get_optional_bytes(store, RETRAINS_KEY)
        retrains = (
            []
            if raw_retrains is None
            else [RetrainRecord.model_validate(r) for r in json.loads(raw_retrains)]
        )
    except (DataValidationError, ExternalServiceError, ValidationError, ValueError) as exc:
        raise MonitoringUnavailableError(f"monitoring results unreadable: {exc}") from exc
    return MonitoringView(latest=latest, history=history, retrains=retrains)


class MonitoringSource:
    """Thread-safe view cache (results change once a day; 5 min keeps S3 reads rare)."""

    def __init__(
        self,
        store: ObjectStore,
        *,
        ttl_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._view: MonitoringView | None = None
        self._loaded_at = 0.0

    def get(self) -> MonitoringView:
        """Raises ``MonitoringUnavailableError`` (see ``load_view``)."""
        with self._lock:
            now = self._clock()
            if self._view is None or now - self._loaded_at >= self._ttl_s:
                self._view = load_view(self._store)
                self._loaded_at = now
            return self._view

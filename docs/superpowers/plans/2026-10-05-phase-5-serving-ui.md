# Phase 5 — Local serving & UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FastAPI service scores a seeded live board with the champion model, and a React app shows risk badges and reasons at `http://localhost:5173`.

**Architecture:** Logic lives in `src/dbdelay/serving/` (model provider with TTL + checksum-verified bundles, board contract + cache, scoring, explanations, seed). `services/api/app/` holds thin FastAPI routers built by `create_app(deps)`; one Docker image with `lambda` and `local` targets. `frontend/` is a Vite + React 19 + TS strict + Tailwind v4 app talking to `/api` through the Vite proxy; compose profile `app` runs both.

**Tech Stack:** Python 3.12, FastAPI, Mangum, uvicorn, LightGBM, pandas, Pydantic v2, moto, pytest; Node 24 LTS, React 19, Vite, TypeScript, Tailwind CSS v4, TanStack Query, React Router, lucide-react, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-phase-5-serving-ui-design.md`

## Global Constraints

- Python 3.12, type hints everywhere, `mypy --strict` on `src/dbdelay/` and `services/api/`; ruff `E,F,I,B,UP,S,SIM,RUF,PL`, line length 100.
- Routers are thin: parse → call `dbdelay.serving` → map to schema. No business logic in `services/api/app/routers/`.
- Models load only via `dbdelay.registry.artifacts.load_bundle` (checksums, fail closed); features only via `dbdelay.features.build.build_features`.
- `dbdelay.serving.*`, `dbdelay.registry.artifacts` and `services/api` must import without scikit-learn (guard test).
- Errors are `application/problem+json` `{type, title, status, detail, instance, request_id}`; no stack trace in responses.
- Inputs: `eva` = `^[1-9][0-9]{6}$`, `hours` 1–6, POST body ≤ 4096 bytes, `q` ≤ 50 chars.
- Times: UTC inside; API responses use the Europe/Berlin offset; UI shows 24-hour Berlin time.
- Board file: `live/boards/latest.json.gz`, schema version 1, `source` `sample|live`; stale when older than 20 min.
- Pointer TTL 300 s, board TTL 60 s. Risk: `p < medium` → low, `p < high` → medium, else high (thresholds from the bundle's `feature_spec.json`).
- Frontend: TS strict, no `any`, tokens from `design.md` §11 only (no hex in components), risk = icon + word + number, every data view has loading/empty/error/stale/no-model states, WCAG 2.1 AA.
- New dependencies are exactly those listed in spec §2 and §9 (owner-approved 2026-10-04). Nothing else without asking.
- Commits: Conventional Commits, **no Claude co-author trailer**. Never push `main`. Never delete branches.
- Before each commit: `uv run ruff check --fix . && uv run ruff format .` (pre-commit aborts silently otherwise).
- Owner decision 2026-10-05: MLflow telemetry off (`MLFLOW_DISABLE_TELEMETRY=true`).

## Review Focus

- A board departure from a station/train type the model never saw in training (maps to `OTHER`) must still get a forecast, not a 500 → Task 9 test `test_predict_station_unseen_by_model`.
- Winter dates must render with `+01:00` and summer with `+02:00`; the seed must keep wall-clock times across a DST switch → Task 7 `test_replay_keeps_wall_clock_across_dst`, Task 9 `test_departure_times_use_berlin_offset_in_winter`.
- A supported station with no departures in the window must return 200 with `departures: []` (UI empty state), not 404 → Task 9 `test_station_without_departures_is_empty`.
- A MinIO hiccup after a good load must keep serving the cached board and model instead of erroring → Task 3 `test_storage_outage_keeps_last_good_bundle`, Task 4 `test_board_source_serves_cached_board_when_refresh_fails`.
- Odd search input (`(`, `%`, emoji, umlaut spellings like `muenchen`) must not crash and should match → Task 8 `test_station_search_odd_input`, `test_station_search_umlaut_spellings`.

---

### Task 1: Turn MLflow telemetry off

**Files:**
- Modify: `src/dbdelay/training/tracking.py:30-34`
- Modify: `docker-compose.yml` (mlflow service env, `x-airflow-env`)
- Modify: `Makefile` (top: export)
- Test: `tests/unit/test_tracking.py:59-71`

**Interfaces:**
- Produces: `CLIENT_ENV_DEFAULTS["MLFLOW_DISABLE_TELEMETRY"] == "true"`.

- [ ] **Step 1: Update the failing test** — in `tests/unit/test_tracking.py::test_tracker_sets_client_env_defaults` extend the comment and expected dict:

```python
    # ... emoji "View run" print crashes cp1252 consoles on Windows -> suppressed. Telemetry is
    # off by owner decision (2026-10-05).
    for name in CLIENT_ENV_DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    MlflowTracker("http://127.0.0.1:9")
    assert {name: os.environ[name] for name in CLIENT_ENV_DEFAULTS} == {
        "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",
        "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false",
        "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT": "true",
        "MLFLOW_DISABLE_TELEMETRY": "true",
    }
```

- [ ] **Step 2: Run it — expect FAIL** (`KeyError`/dict mismatch)

Run: `uv run pytest tests/unit/test_tracking.py -q`

- [ ] **Step 3: Implement** — `tracking.py`:

```python
CLIENT_ENV_DEFAULTS: dict[str, str] = {
    "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",
    "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false",
    "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT": "true",
    # Owner decision 2026-10-05: no usage telemetry from this project.
    "MLFLOW_DISABLE_TELEMETRY": "true",
}
```

`docker-compose.yml`: add `MLFLOW_DISABLE_TELEMETRY: "true"` to the `mlflow` service `environment` and to `x-airflow-env`.
`Makefile`, below the `GIT_SHA` export:

```make
# Owner decision 2026-10-05: MLflow usage telemetry off for every make recipe (train, rollback).
export MLFLOW_DISABLE_TELEMETRY := true
```

- [ ] **Step 4: Run — expect PASS**; then `docker compose up -d mlflow && docker compose exec mlflow printenv MLFLOW_DISABLE_TELEMETRY` → `true`.

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/training/tracking.py tests/unit/test_tracking.py docker-compose.yml Makefile
git commit -m "chore(mlflow): disable MLflow telemetry (owner decision)"
```

---

### Task 2: sklearn-free model loading path

**Files:**
- Create: `src/dbdelay/training/report.py`
- Modify: `src/dbdelay/training/evaluate.py` (move the 5 report models out, re-export)
- Modify: `src/dbdelay/training/calibrate.py` (sklearn import only inside `fit`)
- Modify: `src/dbdelay/registry/artifacts.py` (import from `report`; `ModelBundle.report`)
- Test: `tests/unit/test_serving_imports.py` (new), `tests/unit/test_artifacts.py`

**Interfaces:**
- Produces: `dbdelay.training.report` with `Metrics`, `CalibrationBin`, `SplitReport`, `EvaluationReport`, `TrainingReport` (identical fields). `ModelBundle(manifest, booster, calibrator, spec, report: TrainingReport)`. `tests/unit/test_serving_imports.py::SERVING_MODULES: list[str]` — later tasks append module names.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_serving_imports.py`:

```python
"""The serving path must import without scikit-learn (slim API image, spec §5)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# Later tasks append the serving and API modules.
SERVING_MODULES = ["dbdelay.registry.artifacts", "dbdelay.training.calibrate"]

_BLOCK_SKLEARN = """
import importlib, importlib.abc, sys

class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "sklearn" or name.startswith("sklearn."):
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Block())
importlib.import_module(sys.argv[1])
"""


@pytest.mark.parametrize("module", SERVING_MODULES)
def test_imports_without_sklearn(module: str) -> None:
    paths = [str(REPO / "src"), str(REPO / "services" / "api")]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and inline script
        [sys.executable, "-c", _BLOCK_SKLEARN, module],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(paths)},
    )
    assert result.returncode == 0, result.stderr[-2000:]
```

Append to `tests/unit/test_artifacts.py`:

```python
def test_bundle_carries_training_report(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    write_bundle(s3_store, bundle_manifest(bundle_files), bundle_files)
    bundle = load_bundle(s3_store, "1")
    assert bundle.report.challenger_test.overall.brier == 0.14
    assert bundle.report.baseline_test.overall.brier == 0.16


def test_invalid_metrics_json_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    manifest = bundle_manifest(bundle_files)
    broken = bundle_files | {"metrics.json": b"{}"}
    write_bundle(s3_store, manifest.model_copy(update={"files": {
        name: sha256_ref(broken[name]) for name in BUNDLE_FILES
    }}), broken)
    with pytest.raises(ArtifactIntegrityError, match="parse"):
        load_bundle(s3_store, "1")
```

- [ ] **Step 2: Run — expect FAIL**: `uv run pytest tests/unit/test_serving_imports.py tests/unit/test_artifacts.py -q` → import of `dbdelay.registry.artifacts` fails with `blocked: sklearn`; `report` attribute missing.

- [ ] **Step 3: Implement**

`src/dbdelay/training/report.py` — move the classes **verbatim** from `evaluate.py` (lines 23–76: `Metrics`, `CalibrationBin`, `SplitReport`, `EvaluationReport`, `TrainingReport` incl. `_champion_pair`), with this header:

```python
"""Evaluation report models (`metrics.json`); no scikit-learn so serving can read them."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, model_validator
```

`evaluate.py`: delete those classes, import them back and declare the public surface so existing `from dbdelay.training.evaluate import TrainingReport` keeps working:

```python
from dbdelay.training.report import (
    CalibrationBin,
    EvaluationReport,
    Metrics,
    SplitReport,
    TrainingReport,
)

__all__ = [
    "SLICE_COLUMNS",
    "CalibrationBin",
    "EvaluationReport",
    "Metrics",
    "SplitReport",
    "TrainingReport",
    "calibration_bins",
    "compute_metrics",
    "evaluate_split",
    "expected_calibration_error",
    "hour_band",
    "slice_frame",
]
```

(Check `evaluate.py` for other public names after the move and add them to `__all__`; drop now-unused imports such as `date`, `datetime`, `Any`, `model_validator`.)

`calibrate.py`: remove the top-level `from sklearn.isotonic import IsotonicRegression`; inside `fit`, first line after the docstring:

```python
        # Fitting only (training); serving loads calibrator.json without scikit-learn.
        from sklearn.isotonic import IsotonicRegression  # noqa: PLC0415
```

`artifacts.py`: `from dbdelay.training.report import TrainingReport`; add the field and parse it:

```python
@dataclass(frozen=True)
class ModelBundle:
    manifest: Manifest
    booster: lgb.Booster
    calibrator: IsotonicCalibrator
    spec: FeatureSpec
    report: TrainingReport
```

```python
    try:
        return ModelBundle(
            manifest=manifest,
            booster=booster_from_text(files["model.txt"].decode("utf-8")),
            calibrator=IsotonicCalibrator.from_json(files["calibrator.json"]),
            spec=FeatureSpec.from_json(files["feature_spec.json"]),
            report=TrainingReport.model_validate_json(files["metrics.json"]),
        )
    except (LightGBMError, DataValidationError, UnicodeDecodeError, ValidationError) as exc:
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit -q` (whole unit suite: the move must not break training tests), `uv run mypy`.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training/report.py src/dbdelay/training/evaluate.py src/dbdelay/training/calibrate.py src/dbdelay/registry/artifacts.py tests/unit/test_serving_imports.py tests/unit/test_artifacts.py
git commit -m "refactor(registry): load model bundles without scikit-learn"
```

---

### Task 3: Serving settings and the champion `ModelProvider`

**Files:**
- Modify: `src/dbdelay/config.py`
- Create: `src/dbdelay/serving/__init__.py`, `src/dbdelay/serving/model_loader.py`
- Test: `tests/unit/test_config.py` (append), `tests/unit/test_model_loader.py` (new), `tests/unit/test_serving_imports.py`

**Interfaces:**
- Consumes: `load_bundle(store, version, root) -> ModelBundle`, `ModelPointer.get() -> PointerState | None`.
- Produces: `Settings.board_key: str`, `board_ttl_s: float`, `board_stale_after_s: int`, `model_pointer_ttl_s: float`, `cors_origins: list[str]`. `ModelProvider(store, pointer, *, ttl_s=300.0, clock=time.monotonic, root="")` with `get() -> ModelBundle` (raises `ModelNotAvailableError`), `version_or_none() -> str | None`, `pointer_state() -> PointerState | None`. `Clock = Callable[[], float]`.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_config.py`:

```python
def test_serving_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.board_key == "live/boards/latest.json.gz"
    assert settings.board_ttl_s == 60
    assert settings.board_stale_after_s == 1200
    assert settings.model_pointer_ttl_s == 300
    assert settings.cors_origins == []


def test_cors_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')
    assert Settings(_env_file=None).cors_origins == ["http://localhost:5173"]  # type: ignore[call-arg]
```

`tests/unit/test_model_loader.py`:

```python
import pytest

from dbdelay.errors import ExternalServiceError, ModelNotAvailableError
from dbdelay.registry import artifacts
from dbdelay.registry.artifacts import models_prefix, write_bundle
from dbdelay.registry.pointer import ObjectStorePointer, PointerState
from dbdelay.serving import model_loader
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.storage import ObjectStore
from tests.bundles import bundle_manifest


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FlakyPointer:
    """Pointer whose storage can be switched off."""

    def __init__(self, inner: ObjectStorePointer) -> None:
        self.inner = inner
        self.down = False

    def get(self) -> PointerState | None:
        if self.down:
            raise ExternalServiceError("minio down")
        return self.inner.get()

    def set(self, champion: str, previous: str | None) -> PointerState:
        return self.inner.set(champion, previous)


def _release(store: ObjectStore, files: dict[str, bytes], version: str) -> None:
    write_bundle(store, bundle_manifest(files, version=version), files)


def test_no_pointer_means_no_model(s3_store: ObjectStore) -> None:
    provider = ModelProvider(s3_store, ObjectStorePointer(s3_store), clock=FakeClock())
    with pytest.raises(ModelNotAvailableError, match="no champion"):
        provider.get()
    assert provider.version_or_none() is None


def test_loads_champion_and_rereads_pointer_after_ttl(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, ttl_s=300, clock=clock)
    first = provider.get()
    assert first.manifest.version == "1"
    _release(s3_store, bundle_files, "2")
    pointer.set("2", "1")
    clock.t = 299
    assert provider.get() is first
    clock.t = 300
    assert provider.get().manifest.version == "2"
    state = provider.pointer_state()
    assert state is not None
    assert state.previous_version == "1"


def test_same_version_is_not_reloaded(
    s3_store: ObjectStore, bundle_files: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def counting(store: ObjectStore, version: str, root: str = "") -> artifacts.ModelBundle:
        calls.append(version)
        return artifacts.load_bundle(store, version, root)

    monkeypatch.setattr(model_loader, "load_bundle", counting)
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    for t in (0, 300, 600):
        clock.t = t
        provider.get()
    assert calls == ["1"]


def test_tampered_new_version_fails_closed_then_recovers(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    provider.get()
    _release(s3_store, bundle_files, "2")
    s3_store.put_bytes(models_prefix("2") + "model.txt", b"tampered")
    pointer.set("2", "1")
    clock.t = 300
    with pytest.raises(ModelNotAvailableError, match="v2"):
        provider.get()
    _release(s3_store, bundle_files, "2")  # fixed by a re-release
    clock.t = 600
    assert provider.get().manifest.version == "2"


def test_storage_outage_keeps_last_good_bundle(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = FlakyPointer(ObjectStorePointer(s3_store))
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    good = provider.get()
    pointer.down = True
    clock.t = 300
    assert provider.get() is good


def test_storage_outage_before_first_load_is_unavailable(s3_store: ObjectStore) -> None:
    pointer = FlakyPointer(ObjectStorePointer(s3_store))
    pointer.down = True
    provider = ModelProvider(s3_store, pointer, clock=FakeClock())
    with pytest.raises(ModelNotAvailableError, match="unreadable"):
        provider.get()


def test_corrupt_pointer_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    _release(s3_store, bundle_files, "1")
    s3_store.put_bytes("models/_pointer.json", b"not json")
    provider = ModelProvider(s3_store, ObjectStorePointer(s3_store), clock=FakeClock())
    with pytest.raises(ModelNotAvailableError, match="corrupt"):
        provider.get()
```

Append `"dbdelay.serving.model_loader"` to `SERVING_MODULES`.

- [ ] **Step 2: Run — expect FAIL**: `uv run pytest tests/unit/test_config.py tests/unit/test_model_loader.py -q` (missing settings / module).

- [ ] **Step 3: Implement**

`config.py` — add to `Settings` after `model_pointer_param`:

```python
    # Serving (Phase 5): live board object, cache TTLs, dev-only CORS origins.
    board_key: str = "live/boards/latest.json.gz"
    board_ttl_s: float = Field(default=60, gt=0)
    board_stale_after_s: int = Field(default=1200, gt=0)
    model_pointer_ttl_s: float = Field(default=300, gt=0)
    # JSON list, e.g. CORS_ORIGINS='["http://localhost:5173"]'; empty in prod (same origin).
    cors_origins: list[str] = []
```

(import `Field` from pydantic.)

`src/dbdelay/serving/__init__.py`:

```python
"""Online serving: champion model, live board, scoring, explanations (Phase 5)."""
```

`src/dbdelay/serving/model_loader.py`:

```python
"""Champion model for serving: pointer (re-read every TTL) → verified bundle (architecture §6–7).

Integrity problems fail closed (no forecasts); a storage outage keeps the last good bundle.
"""

import threading
import time
from collections.abc import Callable

from dbdelay.errors import ArtifactIntegrityError, ExternalServiceError, ModelNotAvailableError
from dbdelay.logging import get_logger
from dbdelay.registry.artifacts import ModelBundle, load_bundle
from dbdelay.registry.pointer import ModelPointer, PointerState
from dbdelay.storage import ObjectStore

Clock = Callable[[], float]


class ModelProvider:
    """Thread-safe cache of the champion bundle (one load per version)."""

    def __init__(  # noqa: PLR0913 - collaborators + TTL knobs (drop if ruff reports RUF100)
        self,
        store: ObjectStore,
        pointer: ModelPointer,
        *,
        ttl_s: float = 300.0,
        clock: Clock = time.monotonic,
        root: str = "",
    ) -> None:
        self._store = store
        self._pointer = pointer
        self._ttl_s = ttl_s
        self._clock = clock
        self._root = root
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._state: PointerState | None = None
        self._bundle: ModelBundle | None = None
        self._problem = "model not loaded yet"

    def get(self) -> ModelBundle:
        """The champion bundle.

        Raises:
            ModelNotAvailableError: no champion released, or it failed verification/loading.
        """
        with self._lock:
            now = self._clock()
            if self._checked_at is None or now - self._checked_at >= self._ttl_s:
                self._checked_at = now
                self._refresh()
            if self._bundle is None:
                raise ModelNotAvailableError(self._problem)
            return self._bundle

    def version_or_none(self) -> str | None:
        """Loaded champion version, or ``None`` (for `/health`; never raises)."""
        try:
            return self.get().manifest.version
        except ModelNotAvailableError:
            return None

    def pointer_state(self) -> PointerState | None:
        """Pointer as of the last successful read (for ``previous_version``)."""
        with self._lock:
            return self._state

    def _refresh(self) -> None:
        try:
            state = self._pointer.get()
        except ExternalServiceError as exc:
            # Transient: keep serving what we have; retry after the next TTL.
            get_logger("api").warning("model pointer read failed", extra={"error": str(exc)})
            if self._bundle is None:
                self._problem = "model pointer unreadable"
            return
        except ArtifactIntegrityError as exc:
            self._fail(str(exc))
            return
        if state is None:
            self._fail("no champion model released yet")
            return
        self._state = state
        if self._bundle is not None and self._bundle.manifest.version == state.champion_version:
            return
        try:
            self._bundle = load_bundle(self._store, state.champion_version, self._root)
        except (ArtifactIntegrityError, ExternalServiceError) as exc:
            self._fail(f"champion v{state.champion_version} not loadable: {exc}")
            return
        get_logger("api").info("champion loaded", extra={"model_version": state.champion_version})

    def _fail(self, problem: str) -> None:
        self._bundle = None
        self._problem = problem
        get_logger("api").error("champion unavailable", extra={"problem": problem})
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_config.py tests/unit/test_model_loader.py tests/unit/test_serving_imports.py -q && uv run mypy`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/config.py src/dbdelay/serving tests/unit/test_config.py tests/unit/test_model_loader.py tests/unit/test_serving_imports.py
git commit -m "feat(serving): champion ModelProvider with pointer TTL and fail-closed loading"
```

---

### Task 4: Live board contract, cached reader and selection

**Files:**
- Create: `src/dbdelay/serving/board.py`, `tests/boards.py`
- Test: `tests/unit/test_board.py`, `tests/unit/test_serving_imports.py`

**Interfaces:**
- Produces: `BOARD_KEY = "live/boards/latest.json.gz"`; `BoardDeparture` (fields per spec §4, `.leaves_at -> datetime`); `LiveBoard(schema_version=1, generated_at, source, replayed_from, departures: tuple[BoardDeparture, ...])`; `board_to_bytes(LiveBoard) -> bytes`; `board_from_bytes(bytes) -> LiveBoard` (raises `DataValidationError`); `BoardSource(store, key=BOARD_KEY, *, ttl_s=60.0, clock=time.monotonic)` with `get() -> LiveBoard` (raises `ExternalServiceError`) and `generated_at_or_none() -> datetime | None`; `select_departures(board, eva, now, hours) -> list[BoardDeparture]`; `is_stale(board, now, stale_after_s) -> bool`. Test helpers `tests/boards.py`: `NOW`, `departure(minutes, **overrides) -> BoardDeparture`, `live_board(*departures, generated_at=NOW, source="sample") -> LiveBoard`.

- [ ] **Step 1: Write the test helpers and failing tests**

`tests/boards.py`:

```python
"""Live-board builders for serving/API tests."""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import pandas as pd

from dbdelay.data.silver import make_event_id
from dbdelay.serving.board import BoardDeparture, LiveBoard

NOW = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)  # Monday 15:00 in Berlin (CEST)
FRANKFURT = "8000105"
MUENCHEN = "8000261"


def departure(minutes: int, **overrides: Any) -> BoardDeparture:
    """A Frankfurt ICE departing ``minutes`` after ``NOW`` (on time unless overridden)."""
    planned: datetime = overrides.pop("planned_departure_utc", NOW + timedelta(minutes=minutes))
    eva: str = overrides.get("eva", FRANKFURT)
    ride: str = overrides.pop("ride_id", f"r{minutes + 1000}-2610050000")
    row: dict[str, Any] = {
        "event_id": make_event_id(eva, ride, pd.Timestamp(planned)),
        "eva": eva,
        "station_name": "Frankfurt (Main) Hbf",
        "ride_id": ride,
        "stop_index": 3,
        "train_type": "ICE",
        "train_number": "1602",
        "line_number": None,
        "final_destination": "Berlin Hbf",
        "planned_departure_utc": planned,
        "changed_departure_utc": planned,
        "delay_min": 0,
        "is_cancelled": False,
        "platform": "7",
    }
    row.update(overrides)
    return BoardDeparture(**row)


def live_board(
    *departures: BoardDeparture,
    generated_at: datetime = NOW,
    source: Literal["sample", "live"] = "sample",
) -> LiveBoard:
    return LiveBoard(
        generated_at=generated_at,
        source=source,
        replayed_from=date(2026, 8, 24) if source == "sample" else None,
        departures=departures,
    )
```

`tests/unit/test_board.py`:

```python
import gzip
import json
from datetime import UTC, timedelta

import pytest

from dbdelay.errors import DataValidationError, ExternalServiceError
from dbdelay.serving.board import (
    BOARD_KEY,
    BoardSource,
    board_from_bytes,
    board_to_bytes,
    is_stale,
    select_departures,
)
from dbdelay.storage import ObjectStore
from tests.boards import FRANKFURT, MUENCHEN, NOW, departure, live_board


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _raw(board_json: dict[str, object]) -> bytes:
    return gzip.compress(json.dumps(board_json).encode())


def test_round_trip_is_deterministic() -> None:
    board = live_board(departure(5), departure(10, delay_min=3))
    data = board_to_bytes(board)
    assert board_from_bytes(data) == board
    assert board_to_bytes(board) == data  # gzip mtime fixed


def test_times_are_normalised_to_utc() -> None:
    raw = json.loads(live_board(departure(5)).model_dump_json())
    raw["generated_at"] = "2026-10-05T15:00:00+02:00"
    raw["departures"][0]["planned_departure_utc"] = "2026-10-05T15:05:00+02:00"
    board = board_from_bytes(_raw(raw))
    assert board.generated_at == NOW
    assert board.departures[0].planned_departure_utc.tzinfo == UTC


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("generated_at",), "2026-10-05T13:00:00"),  # naive
        (("schema_version",), 2),
        (("departures", 0, "surprise"), 1),
        (("departures", 0, "eva"), "123"),
        (("departures", 0, "stop_index"), 0),
    ],
)
def test_contract_violations_are_rejected(path: tuple[object, ...], value: object) -> None:
    raw = json.loads(live_board(departure(5)).model_dump_json())
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(DataValidationError):
        board_from_bytes(_raw(raw))


def test_not_gzip_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="gzip"):
        board_from_bytes(b"{}")


def test_select_window_edges_and_order() -> None:
    board = live_board(
        departure(180),  # exactly now + 3 h → in
        departure(181),  # out
        departure(0),  # exactly now → in
        departure(-10, changed_departure_utc=None),  # left already (no change) → out
        departure(-10, ride_id="late", changed_departure_utc=NOW + timedelta(minutes=5), delay_min=15),
        departure(30, eva=MUENCHEN),  # other station
    )
    rows = select_departures(board, FRANKFURT, NOW, 3)
    minutes = [int((r.planned_departure_utc - NOW).total_seconds() // 60) for r in rows]
    assert minutes == [-10, 0, 180]
    assert rows[0].delay_min == 15


def test_cancelled_departure_stays_until_planned_time() -> None:
    board = live_board(departure(10, is_cancelled=True, changed_departure_utc=None, delay_min=None))
    assert len(select_departures(board, FRANKFURT, NOW, 1)) == 1


def test_stale_after_twenty_minutes() -> None:
    board = live_board(generated_at=NOW - timedelta(minutes=20))
    assert not is_stale(board, NOW, 1200)
    assert is_stale(board, NOW + timedelta(seconds=1), 1200)


def test_board_source_caches_for_ttl(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(BOARD_KEY, board_to_bytes(live_board(departure(5))))
    clock = FakeClock()
    source = BoardSource(s3_store, ttl_s=60, clock=clock)
    first = source.get()
    s3_store.put_bytes(BOARD_KEY, board_to_bytes(live_board(departure(5), departure(9))))
    clock.t = 59
    assert source.get() is first
    clock.t = 60
    assert len(source.get().departures) == 2


def test_missing_board_is_unavailable(s3_store: ObjectStore) -> None:
    source = BoardSource(s3_store, clock=FakeClock())
    with pytest.raises(ExternalServiceError, match="live board unavailable"):
        source.get()
    assert source.generated_at_or_none() is None


def test_board_source_serves_cached_board_when_refresh_fails(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(BOARD_KEY, board_to_bytes(live_board(departure(5))))
    clock = FakeClock()
    source = BoardSource(s3_store, clock=clock)
    good = source.get()
    s3_store.put_bytes(BOARD_KEY, b"corrupt")
    clock.t = 60
    assert source.get() is good
    assert source.generated_at_or_none() == NOW
```

Append `"dbdelay.serving.board"` to `SERVING_MODULES`.

- [ ] **Step 2: Run — expect FAIL** (module missing): `uv run pytest tests/unit/test_board.py -q`

- [ ] **Step 3: Implement** `src/dbdelay/serving/board.py`:

```python
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
    """Departures of one station still to leave (planned or changed time ≥ ``now``) and planned
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
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_board.py tests/unit/test_serving_imports.py -q && uv run mypy`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/serving/board.py tests/boards.py tests/unit/test_board.py tests/unit/test_serving_imports.py
git commit -m "feat(serving): live board contract, cached reader and departure selection"
```

---
### Task 5: Plain-language explanations (`explain.py`)

**Files:**
- Create: `src/dbdelay/serving/explain.py`
- Test: `tests/unit/test_explain.py`, `tests/unit/test_serving_imports.py`

**Interfaces:**
- Consumes: a parsed `ModelBundle` (`parse_bundle(manifest, files)`), `build_features(df, spec)`.
- Produces: `Direction = Literal["up", "down"]`; `TOP_K = 3`; `@dataclass(frozen=True) Factor(feature: str, direction: Direction, text: str)`; `FACTOR_TEXT: dict[str, tuple[str, str]]`; `factor_text(feature, direction, value) -> str`; `top_factors(booster, features, k=TOP_K) -> list[tuple[Factor, ...]]`.

- [ ] **Step 1: Use the `design:ux-copy` skill** to review the reason texts below (short, plain, friendly English, "usually"/"often", no jargon; design.md §9). Keep the `{value}` placeholders and the (raises, lowers) pairs; change only wording.

- [ ] **Step 2: Write failing tests** `tests/unit/test_explain.py`:

```python
import numpy as np
import pytest

from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS
from dbdelay.registry.artifacts import ModelBundle, parse_bundle
from dbdelay.serving.explain import FACTOR_TEXT, TOP_K, factor_text, top_factors
from tests.builders import signal_frames
from tests.bundles import bundle_manifest

SAMPLE_VALUES: dict[str, object] = {
    "eva": "8000105",
    "train_type": "ICE",
    "line_key": "RE:RE1",
    "destination_key": "Berlin Hbf",
    "stop_index": 7,
    "hour_local": 8,
    "minute_of_day": 490,
    "weekday": 4,
    "is_weekend": False,
    "month": 3,
    "is_public_holiday": True,
}


@pytest.fixture(scope="module")
def bundle(bundle_files: dict[str, bytes]) -> ModelBundle:
    return parse_bundle(bundle_manifest(bundle_files), bundle_files)


def test_every_feature_has_text_for_both_directions() -> None:
    assert set(FACTOR_TEXT) == set(FEATURE_COLUMNS)
    for feature, value in SAMPLE_VALUES.items():
        for direction in ("up", "down"):
            text = factor_text(feature, direction, value)
            assert text
            assert "{" not in text


def test_values_are_human_readable() -> None:
    assert "Fridays" in factor_text("weekday", "up", 4)
    assert "08:00" in factor_text("hour_local", "down", 8)
    assert "March" in factor_text("month", "up", 3)
    assert "ICE" in factor_text("train_type", "up", "ICE")
    assert "OTHER" not in factor_text("train_type", "up", "OTHER")


def test_top_factors_follow_contribution_size_and_sign(bundle: ModelBundle) -> None:
    features = build_features(signal_frames()["test"].head(25), bundle.spec)
    factors = top_factors(bundle.booster, features)
    contrib = np.asarray(bundle.booster.predict(features, pred_contrib=True))[:, :-1]
    names = [str(n) for n in bundle.booster.feature_name()]
    assert len(factors) == len(features)
    for row, row_factors in zip(contrib, factors, strict=True):
        assert 1 <= len(row_factors) <= TOP_K
        sizes = [abs(row[names.index(f.feature)]) for f in row_factors]
        assert sizes == sorted(sizes, reverse=True)
        assert sizes[0] == pytest.approx(np.abs(row).max())
        for f in row_factors:
            assert (row[names.index(f.feature)] > 0) == (f.direction == "up")


def test_signal_model_explains_with_stop_index(bundle: ModelBundle) -> None:
    # The signal data's late risk is driven by stop_index (tests/builders.signal_day).
    features = build_features(signal_frames()["test"].head(25), bundle.spec)
    top = [row[0].feature for row in top_factors(bundle.booster, features)]
    assert top.count("stop_index") > len(top) / 2


def test_no_rows_no_factors(bundle: ModelBundle) -> None:
    features = build_features(signal_frames()["test"].head(0), bundle.spec)
    assert top_factors(bundle.booster, features) == []
```

Append `"dbdelay.serving.explain"` to `SERVING_MODULES`.

- [ ] **Step 3: Run — expect FAIL**: `uv run pytest tests/unit/test_explain.py -q`

- [ ] **Step 4: Implement** `src/dbdelay/serving/explain.py` (texts as reviewed in Step 1):

```python
"""Plain-language reasons: the top LightGBM feature contributions per departure (architecture §7).

Contributions are in raw log-odds space; isotonic calibration is monotone, so their direction
(raises / lowers the risk) holds for the calibrated ``p_late`` too.
"""

from dataclasses import dataclass
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd

from dbdelay.features.spec import OTHER

Direction = Literal["up", "down"]
TOP_K = 3
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_UNKNOWN = {OTHER, "none"}

# feature → (text when it raises the risk, text when it lowers it); {value} from _value_text.
FACTOR_TEXT: dict[str, tuple[str, str]] = {
    "eva": (
        "Departures from this station are often late",
        "Departures from this station are usually on time",
    ),
    "train_type": ("{value} trains are often late here", "{value} trains are usually on time here"),
    "line_key": ("This line often runs late", "This line usually runs on time"),
    "destination_key": (
        "Trains to this destination are often late",
        "Trains to this destination are usually on time",
    ),
    "stop_index": (
        "Late stop in a long journey, so delays have had time to build up",
        "Early stop in the journey, so there is little delay to pick up yet",
    ),
    "hour_local": (
        "Departures around {value} are often late",
        "Departures around {value} are usually on time",
    ),
    "minute_of_day": ("This time of day is usually busy", "This time of day is usually quiet"),
    "weekday": ("{value}s are busier", "{value}s are usually calmer"),
    "is_weekend": ("{value} traffic raises the risk", "{value} traffic lowers the risk"),
    "month": ("Delays are more common in {value}", "Delays are less common in {value}"),
    "is_public_holiday": (
        "{value} timetable raises the risk",
        "{value} timetable lowers the risk",
    ),
}


@dataclass(frozen=True)
class Factor:
    feature: str
    direction: Direction
    text: str


def _value_text(feature: str, value: object) -> str:  # noqa: PLR0911 - one case per feature
    if feature == "train_type":
        return "These" if str(value) in _UNKNOWN else str(value)
    if feature == "hour_local":
        return f"{int(str(value)):02d}:00"
    if feature == "weekday":
        return _WEEKDAYS[int(str(value))]
    if feature == "month":
        return _MONTHS[int(str(value)) - 1]
    if feature == "is_weekend":
        return "Weekend" if bool(value) else "Weekday"
    if feature == "is_public_holiday":
        return "Holiday" if bool(value) else "Working-day"
    return str(value)


def factor_text(feature: str, direction: Direction, value: object) -> str:
    """Reason text for one feature of one departure."""
    raises, lowers = FACTOR_TEXT[feature]
    template = raises if direction == "up" else lowers
    return template.format(value=_value_text(feature, value))


def top_factors(
    booster: lgb.Booster, features: pd.DataFrame, k: int = TOP_K
) -> list[tuple[Factor, ...]]:
    """Per row, the ``k`` features with the largest absolute contribution (zero ones skipped)."""
    if features.empty:
        return []
    contrib = np.asarray(booster.predict(features, pred_contrib=True), dtype=np.float64)[:, :-1]
    names = [str(name) for name in booster.feature_name()]
    result: list[tuple[Factor, ...]] = []
    for i, row in enumerate(contrib):
        order = np.argsort(-np.abs(row), kind="stable")[:k]
        factors: list[Factor] = []
        for j in order:
            if row[j] == 0.0:
                continue
            direction: Direction = "up" if row[j] > 0 else "down"
            value = features.iloc[i][names[j]]
            factors.append(Factor(names[j], direction, factor_text(names[j], direction, value)))
        result.append(tuple(factors))
    return result
```

- [ ] **Step 5: Run — expect PASS**: `uv run pytest tests/unit/test_explain.py tests/unit/test_serving_imports.py -q && uv run mypy`

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/serving/explain.py tests/unit/test_explain.py tests/unit/test_serving_imports.py
git commit -m "feat(serving): plain-language top-3 reasons from LightGBM contributions"
```

---

### Task 6: Scoring — risk level, board scoring, single prediction, prediction log

**Files:**
- Create: `src/dbdelay/serving/scoring.py`
- Test: `tests/unit/test_scoring.py`, `tests/unit/test_serving_imports.py`

**Interfaces:**
- Consumes: `BoardDeparture`, `top_factors`, `Factor`, `ModelBundle`, `build_features`, `predict_raw`.
- Produces: `RiskLevel = Literal["low", "medium", "high"]`; `ChampionSource` protocol (`get() -> ModelBundle`); `@dataclass(frozen=True) ScheduleInput(eva, train_type, line_number, final_destination, stop_index, planned_departure_utc)` with `ScheduleInput.from_board(BoardDeparture)`; `@dataclass(frozen=True) Prediction(p_late: float, risk_level: RiskLevel, factors: tuple[Factor, ...])`; `risk_level(p_late, thresholds) -> RiskLevel`; `schedule_frame(inputs) -> pd.DataFrame`; `score(bundle, inputs) -> list[Prediction]`; `@dataclass(frozen=True) BoardScores(model_version: str | None, predictions: list[Prediction | None])`; `score_board(models, rows, *, request_id) -> BoardScores`; `predict_one(models, item, *, request_id) -> tuple[str, Prediction]`.

- [ ] **Step 1: Write failing tests** `tests/unit/test_scoring.py`:

```python
import json
from datetime import timedelta

import pandas as pd
import pytest

from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.spec import RiskThresholds, require_columns
from dbdelay.registry.artifacts import ModelBundle, parse_bundle
from dbdelay.serving.scoring import (
    ScheduleInput,
    predict_one,
    risk_level,
    schedule_frame,
    score,
    score_board,
)
from tests.boards import NOW, departure
from tests.builders import signal_frames
from tests.bundles import bundle_manifest

THRESHOLDS = RiskThresholds(medium=0.2, high=0.45)


class StaticModels:
    def __init__(self, bundle: ModelBundle | None) -> None:
        self.bundle = bundle

    def get(self) -> ModelBundle:
        if self.bundle is None:
            raise ModelNotAvailableError("no champion model released yet")
        return self.bundle


@pytest.fixture(scope="module")
def bundle(bundle_files: dict[str, bytes]) -> ModelBundle:
    return parse_bundle(bundle_manifest(bundle_files), bundle_files)


def _inputs(n: int = 20) -> list[ScheduleInput]:
    rows = signal_frames()["test"].head(n)
    return [
        ScheduleInput(
            eva=r.eva,
            train_type=r.train_type,
            line_number=r.line_number,
            final_destination=r.final_destination,
            stop_index=int(r.stop_index),
            planned_departure_utc=r.planned_departure_utc.to_pydatetime(),
        )
        for r in rows.itertuples()
    ]


@pytest.mark.parametrize(
    ("p", "level"),
    [(0.0, "low"), (0.1999, "low"), (0.2, "medium"), (0.4499, "medium"), (0.45, "high"), (1.0, "high")],
)
def test_risk_level_boundaries(p: float, level: str) -> None:
    assert risk_level(p, THRESHOLDS) == level


def test_schedule_frame_meets_feature_contract() -> None:
    frame = schedule_frame(_inputs(3))
    require_columns(frame)
    assert str(frame["planned_departure_utc"].dt.tz) == "UTC"
    assert frame["stop_index"].dtype == "int16"


def test_score_matches_bundle_predict(bundle: ModelBundle) -> None:
    inputs = _inputs()
    predictions = score(bundle, inputs)
    expected = bundle.predict(schedule_frame(inputs))
    assert [p.p_late for p in predictions] == pytest.approx(expected.tolist(), abs=1e-4)
    assert all(1 <= len(p.factors) <= 3 for p in predictions)


def test_score_without_thresholds_is_unavailable(bundle: ModelBundle) -> None:
    spec = bundle.spec.model_copy(update={"risk_thresholds": None})
    no_thresholds = ModelBundle(bundle.manifest, bundle.booster, bundle.calibrator, spec, bundle.report)
    with pytest.raises(ModelNotAvailableError, match="risk thresholds"):
        score(no_thresholds, _inputs(1))


def test_board_without_model_keeps_rows(bundle: ModelBundle) -> None:
    rows = [departure(5), departure(9)]
    result = score_board(StaticModels(None), rows, request_id="r1")
    assert result.model_version is None
    assert result.predictions == [None, None]


def test_board_skips_cancelled_and_logs_each_prediction(
    bundle: ModelBundle, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        departure(5),
        departure(9, is_cancelled=True, changed_departure_utc=None, delay_min=None),
        departure(12),
    ]
    result = score_board(StaticModels(bundle), rows, request_id="r1")
    assert result.model_version == "1"
    assert result.predictions[1] is None
    assert result.predictions[0] is not None
    assert result.predictions[2] is not None
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    lines = [entry for entry in logs if entry.get("message") == "prediction"]
    assert [entry["event_id"] for entry in lines] == [rows[0].event_id, rows[2].event_id]
    assert {entry["request_id"] for entry in lines} == {"r1"}
    assert all("latency_ms" in entry and entry["model_version"] == "1" for entry in lines)


def test_predict_one_needs_a_model(bundle: ModelBundle) -> None:
    item = ScheduleInput.from_board(departure(5))
    version, prediction = predict_one(StaticModels(bundle), item, request_id="r2")
    assert version == "1"
    assert 0.0 <= prediction.p_late <= 1.0
    with pytest.raises(ModelNotAvailableError):
        predict_one(StaticModels(None), item, request_id="r2")


def test_from_board_keeps_only_timetable_fields() -> None:
    row = departure(5, delay_min=12, changed_departure_utc=NOW + timedelta(minutes=17))
    item = ScheduleInput.from_board(row)
    assert item.planned_departure_utc == row.planned_departure_utc
    assert not hasattr(item, "delay_min")
    assert isinstance(schedule_frame([item]), pd.DataFrame)
```

Append `"dbdelay.serving.scoring"` to `SERVING_MODULES`.

- [ ] **Step 2: Run — expect FAIL**: `uv run pytest tests/unit/test_scoring.py -q`

- [ ] **Step 3: Implement** `src/dbdelay/serving/scoring.py`:

```python
"""Score departures with the champion: calibrated ``p_late``, risk level, top reasons, and one
prediction log line per scored departure (architecture §7)."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import pandas as pd

from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.build import build_features
from dbdelay.features.spec import RiskThresholds
from dbdelay.logging import get_logger
from dbdelay.registry.artifacts import ModelBundle
from dbdelay.serving.board import BoardDeparture
from dbdelay.serving.explain import Factor, top_factors
from dbdelay.training.train import predict_raw

RiskLevel = Literal["low", "medium", "high"]


class ChampionSource(Protocol):
    def get(self) -> ModelBundle: ...


@dataclass(frozen=True)
class ScheduleInput:
    """What the model may know before departure: timetable fields only (leakage policy)."""

    eva: str
    train_type: str
    line_number: str | None
    final_destination: str | None
    stop_index: int
    planned_departure_utc: datetime

    @classmethod
    def from_board(cls, row: BoardDeparture) -> "ScheduleInput":
        return cls(
            eva=row.eva,
            train_type=row.train_type,
            line_number=row.line_number,
            final_destination=row.final_destination,
            stop_index=row.stop_index,
            planned_departure_utc=row.planned_departure_utc,
        )


@dataclass(frozen=True)
class Prediction:
    p_late: float
    risk_level: RiskLevel
    factors: tuple[Factor, ...]


@dataclass(frozen=True)
class BoardScores:
    model_version: str | None
    predictions: list[Prediction | None]


def risk_level(p_late: float, thresholds: RiskThresholds) -> RiskLevel:
    """Low below ``medium``, Medium below ``high``, else High (prd §5)."""
    if p_late < thresholds.medium:
        return "low"
    if p_late < thresholds.high:
        return "medium"
    return "high"


def schedule_frame(inputs: Sequence[ScheduleInput]) -> pd.DataFrame:
    """Rows in the shape ``build_features`` reads (``REQUIRED_COLUMNS``)."""
    return pd.DataFrame(
        {
            "eva": pd.Series([i.eva for i in inputs], dtype="object"),
            "train_type": pd.Series([i.train_type for i in inputs], dtype="object"),
            "line_number": pd.Series([i.line_number for i in inputs], dtype="object"),
            "final_destination": pd.Series([i.final_destination for i in inputs], dtype="object"),
            "stop_index": pd.Series([i.stop_index for i in inputs], dtype="int16"),
            "planned_departure_utc": pd.Series(
                pd.to_datetime([i.planned_departure_utc for i in inputs], utc=True)
            ),
        }
    )


def score(bundle: ModelBundle, inputs: Sequence[ScheduleInput]) -> list[Prediction]:
    """Predictions in input order.

    Raises:
        ModelNotAvailableError: if the bundle has no risk thresholds.
    """
    if not inputs:
        return []
    thresholds = bundle.spec.risk_thresholds
    if thresholds is None:
        raise ModelNotAvailableError(f"model v{bundle.manifest.version} has no risk thresholds")
    features = build_features(schedule_frame(inputs), bundle.spec)
    p_late = bundle.calibrator.apply(predict_raw(bundle.booster, features))
    factors = top_factors(bundle.booster, features)
    return [
        Prediction(round(float(p), 4), risk_level(float(p), thresholds), f)
        for p, f in zip(p_late, factors, strict=True)
    ]


def _log_prediction(  # noqa: PLR0913 - one structured log line, all fields named
    *,
    event_id: str | None,
    eva: str,
    model_version: str,
    prediction: Prediction,
    latency_ms: float,
    request_id: str,
) -> None:
    get_logger("api").info(
        "prediction",
        extra={
            "event_id": event_id,
            "eva": eva,
            "model_version": model_version,
            "p_late": prediction.p_late,
            "risk_level": prediction.risk_level,
            "latency_ms": round(latency_ms, 2),
            "request_id": request_id,
        },
    )


def score_board(
    models: ChampionSource, rows: Sequence[BoardDeparture], *, request_id: str
) -> BoardScores:
    """Predictions for board rows: ``None`` for cancelled rows, and for every row when no
    champion is available (the board still shows live data — rules.md §6.3)."""
    started = time.perf_counter()
    active = [i for i, row in enumerate(rows) if not row.is_cancelled]
    try:
        bundle = models.get()
        scored = score(bundle, [ScheduleInput.from_board(rows[i]) for i in active])
    except ModelNotAvailableError:
        return BoardScores(None, [None] * len(rows))
    latency_ms = (time.perf_counter() - started) * 1000
    version = bundle.manifest.version
    predictions: list[Prediction | None] = [None] * len(rows)
    for i, prediction in zip(active, scored, strict=True):
        predictions[i] = prediction
        _log_prediction(
            event_id=rows[i].event_id,
            eva=rows[i].eva,
            model_version=version,
            prediction=prediction,
            latency_ms=latency_ms,
            request_id=request_id,
        )
    return BoardScores(version, predictions)


def predict_one(
    models: ChampionSource, item: ScheduleInput, *, request_id: str
) -> tuple[str, Prediction]:
    """Score one departure (`POST /predict`).

    Raises:
        ModelNotAvailableError: no champion is available.
    """
    started = time.perf_counter()
    bundle = models.get()
    [prediction] = score(bundle, [item])
    _log_prediction(
        event_id=None,
        eva=item.eva,
        model_version=bundle.manifest.version,
        prediction=prediction,
        latency_ms=(time.perf_counter() - started) * 1000,
        request_id=request_id,
    )
    return bundle.manifest.version, prediction
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_scoring.py tests/unit/test_serving_imports.py -q && uv run mypy`. If Powertools writes to stderr instead of stdout in tests, read `capsys.readouterr().err` — check `Logger` output stream once and fix the test, not the code.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/serving/scoring.py tests/unit/test_scoring.py tests/unit/test_serving_imports.py
git commit -m "feat(serving): score board rows and single departures with risk levels and reasons"
```

---

### Task 7: Sample board seed (`make seed`)

**Files:**
- Create: `src/dbdelay/serving/seed.py`, `scripts/seed_sample_data.py`
- Modify: `Makefile` (`seed` target, `.PHONY`)
- Test: `tests/unit/test_seed.py`

**Interfaces:**
- Consumes: `silver_key(day, root)`, `make_event_id`, `latest_silver_day(store, root)`, `BERLIN`, `BoardDeparture`, `LiveBoard`, `board_to_bytes`, `BOARD_KEY`.
- Produces: `pick_source_day(latest: date, today: date) -> date`; `read_source_rows(store, source_day, root="") -> pd.DataFrame`; `replay_board(rows, *, source_day, now) -> LiveBoard`; `seed_board(store, *, now, board_key=BOARD_KEY, source_day=None, silver_root="") -> LiveBoard`; `seed_main(argv=None) -> int`.

- [ ] **Step 1: Write failing tests** `tests/unit/test_seed.py`:

```python
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from dbdelay.config import get_settings
from dbdelay.data.silver import SILVER_ARROW_SCHEMA, make_event_id, silver_key, to_parquet_bytes
from dbdelay.serving.board import BOARD_KEY, board_from_bytes
from dbdelay.serving.seed import (
    pick_source_day,
    read_source_rows,
    replay_board,
    seed_board,
    seed_main,
)
from dbdelay.storage import ObjectStore
from tests.builders import silver_frame
from tests.unit.conftest import TEST_BUCKET


def _silver(planned_utc: list[str], *, cancelled: int | None = None) -> pd.DataFrame:
    """Silver rows at the given UTC times, each 4 min late (row ``cancelled`` cancelled)."""
    n = len(planned_utc)
    df = silver_frame(n)
    planned = pd.Series(pd.to_datetime(planned_utc, utc=True), dtype="datetime64[us, UTC]")
    df["ride_id"] = [f"{500 + i}-2603280600" for i in range(n)]
    df["planned_departure_utc"] = planned
    df["changed_departure_utc"] = planned + pd.Timedelta(minutes=4)
    df["delay_min"] = pd.Series([4] * n, dtype="Int16")
    df["is_late"] = pd.Series([False] * n, dtype="boolean")
    if cancelled is not None:
        df.loc[cancelled, "is_cancelled"] = True
        df.loc[cancelled, "changed_departure_utc"] = pd.NaT
        df.loc[cancelled, "delay_min"] = pd.NA
        df.loc[cancelled, "is_late"] = pd.NA
    return df


def _put(store: ObjectStore, df: pd.DataFrame) -> None:
    for day, part in df.groupby(df["planned_departure_utc"].dt.date):
        store.put_bytes(silver_key(day), to_parquet_bytes(part, SILVER_ARROW_SCHEMA))


def test_pick_source_day_matches_weekday_and_leaves_a_next_day() -> None:
    latest = date(2026, 8, 31)  # Monday
    assert pick_source_day(latest, date(2026, 10, 5)) == date(2026, 8, 24)  # Monday
    assert pick_source_day(latest, date(2026, 10, 6)) == date(2026, 8, 25)  # Tuesday
    assert pick_source_day(latest, date(2026, 10, 4)) == date(2026, 8, 30)  # Sunday


def test_replay_keeps_wall_clock_across_dst() -> None:
    rows = _silver(["2026-03-28T09:00:00Z"])  # Sat 10:00 CET
    now = datetime(2026, 4, 4, 8, 0, tzinfo=UTC)  # Sat 10:00 CEST
    board = replay_board(rows, source_day=date(2026, 3, 28), now=now)
    [row] = board.departures
    assert row.planned_departure_utc == datetime(2026, 4, 4, 8, 0, tzinfo=UTC)
    assert row.changed_departure_utc == row.planned_departure_utc + timedelta(minutes=4)
    assert row.event_id == make_event_id(row.eva, row.ride_id, pd.Timestamp(row.planned_departure_utc))
    assert (board.source, board.replayed_from, board.generated_at) == ("sample", date(2026, 3, 28), now)
    assert row.platform is None


def test_replay_window_is_minus_30_min_to_plus_6_h() -> None:
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    week_ago = now - timedelta(days=7)
    offsets = [-31, -30, 360, 361]
    rows = _silver([(week_ago + timedelta(minutes=m)).isoformat() for m in offsets])
    board = replay_board(rows, source_day=date(2026, 9, 28), now=now)
    kept = [int((d.planned_departure_utc - now).total_seconds() // 60) for d in board.departures]
    assert kept == [-30, 360]


def test_next_source_day_lands_tomorrow() -> None:
    rows = _silver(["2026-08-24T23:00:00Z"])  # Tue 2026-08-25 01:00 CEST
    now = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)  # Mon 23:00 CEST
    board = replay_board(rows, source_day=date(2026, 8, 24), now=now)
    [row] = board.departures
    assert row.planned_departure_utc == datetime(2026, 10, 5, 23, 0, tzinfo=UTC)


def test_cancelled_rows_stay_cancelled() -> None:
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    rows = _silver([(now - timedelta(days=7, minutes=-10)).isoformat()], cancelled=0)
    [row] = replay_board(rows, source_day=date(2026, 9, 28), now=now).departures
    assert row.is_cancelled
    assert row.changed_departure_utc is None
    assert row.delay_min is None


def test_read_source_rows_uses_berlin_dates(s3_store: ObjectStore) -> None:
    _put(
        s3_store,
        _silver(
            [
                "2026-08-23T21:59:00Z",  # 23:59 on the 23rd local → out
                "2026-08-23T22:00:00Z",  # 00:00 on the 24th local → in
                "2026-08-25T21:59:00Z",  # 23:59 on the 25th local → in
                "2026-08-25T22:00:00Z",  # 00:00 on the 26th local → out
            ]
        ),
    )
    rows = read_source_rows(s3_store, date(2026, 8, 24))
    assert len(rows) == 2


def test_seed_board_writes_the_board(s3_store: ObjectStore) -> None:
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    _put(s3_store, _silver(["2026-09-28T13:30:00Z", "2026-09-29T08:00:00Z"]))
    board = seed_board(s3_store, now=now, source_day=date(2026, 9, 28))
    assert board_from_bytes(s3_store.get_bytes(BOARD_KEY)) == board
    assert len(board.departures) == 1


def test_seed_main_writes_board_and_reports(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    get_settings.cache_clear()
    start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    _put(s3_store, _silver([(start + timedelta(days=d)).isoformat() for d in range(15)]))
    assert seed_main([]) == 0
    board = board_from_bytes(s3_store.get_bytes(BOARD_KEY))
    assert board.replayed_from is not None
    today = datetime.now(UTC).astimezone(ZoneInfo("Europe/Berlin")).date()
    assert board.replayed_from.weekday() == today.weekday()
    assert "sample board:" in capsys.readouterr().out


def test_seed_main_without_silver_fails_cleanly(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    get_settings.cache_clear()
    assert seed_main([]) == 1
    assert "seed failed" in capsys.readouterr().err
```

- [ ] **Step 2: Run — expect FAIL**: `uv run pytest tests/unit/test_seed.py -q`

- [ ] **Step 3: Implement** `src/dbdelay/serving/seed.py`:

```python
"""Sample live board for local serving (Phase 5): a real silver day replayed onto today.

Boards are labelled ``source="sample"`` with ``replayed_from=<day>``; the UI says so. Delay and
cancellation values are the real ones of that day. Phase 7 ingestion writes live boards instead.
"""

import argparse
import io
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dbdelay.config import get_settings
from dbdelay.data.silver import make_event_id, silver_key
from dbdelay.errors import DataValidationError, DbDelayError, NotFoundError
from dbdelay.features.calendar import BERLIN
from dbdelay.serving.board import BOARD_KEY, BoardDeparture, LiveBoard, board_to_bytes
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.split import latest_silver_day

BEFORE = timedelta(minutes=30)
AFTER = timedelta(hours=6)


def _berlin_today(now: datetime) -> date:
    return now.astimezone(ZoneInfo(BERLIN)).date()


def pick_source_day(latest: date, today: date) -> date:
    """Newest day before ``latest`` (so its next day exists too) with ``today``'s weekday."""
    day = latest - timedelta(days=1)
    while day.weekday() != today.weekday():
        day -= timedelta(days=1)
    return day


def read_source_rows(store: ObjectStore, source_day: date, root: str = "") -> pd.DataFrame:
    """Silver rows planned on ``source_day`` or the day after (Berlin dates).

    Raises:
        DataValidationError: if none of the surrounding UTC partitions exist.
    """
    frames = []
    for offset in range(-1, 3):
        try:
            data = store.get_bytes(silver_key(source_day + timedelta(days=offset), root))
        except NotFoundError:
            continue
        frames.append(pd.read_parquet(io.BytesIO(data)))
    if not frames:
        raise DataValidationError(f"no silver partitions around {source_day}")
    rows = pd.concat(frames, ignore_index=True)
    local_day = rows["planned_departure_utc"].dt.tz_convert(BERLIN).dt.date
    keep = local_day.isin({source_day, source_day + timedelta(days=1)})
    return rows[keep].reset_index(drop=True)


def _optional(value: object) -> str | None:
    return None if pd.isna(value) else str(value)  # type: ignore[arg-type]


def replay_board(rows: pd.DataFrame, *, source_day: date, now: datetime) -> LiveBoard:
    """Move ``rows`` from ``source_day`` onto today keeping Berlin wall-clock times (a calendar
    shift, so DST is handled), recompute ``event_id``, keep ``[now − 30 min, now + 6 h]``."""
    days = (_berlin_today(now) - source_day).days
    local = rows["planned_departure_utc"].dt.tz_convert(BERLIN).dt.tz_localize(None)
    planned = (
        (local + pd.Timedelta(days=days))
        .dt.tz_localize(BERLIN, ambiguous="NaT", nonexistent="NaT")
        .dt.tz_convert("UTC")
    )
    changed = planned + (rows["changed_departure_utc"] - rows["planned_departure_utc"])
    window = planned.notna() & (planned >= now - BEFORE) & (planned <= now + AFTER)
    departures = []
    for i in np.flatnonzero(window.to_numpy()):
        row = rows.iloc[i]
        when = planned.iloc[i]
        departures.append(
            BoardDeparture(
                event_id=make_event_id(str(row["eva"]), str(row["ride_id"]), when),
                eva=str(row["eva"]),
                station_name=str(row["station_name"]),
                ride_id=str(row["ride_id"]),
                stop_index=int(row["stop_index"]),
                train_type=str(row["train_type"]),
                train_number=_optional(row["train_number"]),
                line_number=_optional(row["line_number"]),
                final_destination=_optional(row["final_destination"]),
                planned_departure_utc=when.to_pydatetime(),
                changed_departure_utc=(
                    None if pd.isna(changed.iloc[i]) else changed.iloc[i].to_pydatetime()
                ),
                delay_min=None if pd.isna(row["delay_min"]) else int(row["delay_min"]),
                is_cancelled=bool(row["is_cancelled"]),
                platform=None,  # silver has no platform column
            )
        )
    departures.sort(key=lambda d: (d.planned_departure_utc, d.event_id))
    return LiveBoard(
        generated_at=now, source="sample", replayed_from=source_day, departures=tuple(departures)
    )


def seed_board(
    store: ObjectStore,
    *,
    now: datetime,
    board_key: str = BOARD_KEY,
    source_day: date | None = None,
    silver_root: str = "",
) -> LiveBoard:
    """Build a sample board from silver and write it to ``board_key``."""
    if source_day is None:
        source_day = pick_source_day(latest_silver_day(store, silver_root), _berlin_today(now))
    rows = read_source_rows(store, source_day, silver_root)
    board = replay_board(rows, source_day=source_day, now=now)
    store.put_bytes(board_key, board_to_bytes(board), "application/gzip")
    return board


def seed_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make seed`."""
    parser = argparse.ArgumentParser(
        description="Write a sample live board: a real silver day replayed onto today."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="source day YYYY-MM-DD (default: newest silver day with today's weekday)",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    store = ObjectStore(make_s3_client(settings), settings.data_bucket)
    try:
        board = seed_board(
            store, now=datetime.now(UTC), board_key=settings.board_key, source_day=args.date
        )
    except DbDelayError as exc:
        sys.stderr.write(f"seed failed: {exc}\n")
        return 1
    stations = len({d.eva for d in board.departures})
    sys.stdout.write(
        f"sample board: {len(board.departures)} departures at {stations} stations, "
        f"replayed from {board.replayed_from}\n"
    )
    return 0
```

`scripts/seed_sample_data.py`:

```python
"""`make seed`: write a sample live board (a real silver day replayed onto today) into MinIO."""

from dbdelay.serving.seed import seed_main

if __name__ == "__main__":
    raise SystemExit(seed_main())
```

`Makefile` (add `seed` to `.PHONY`):

```make
seed: ## Write a sample live board into MinIO (real silver day replayed onto today; Phase 5)
	uv run python scripts/seed_sample_data.py
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_seed.py -q && uv run mypy`. Then a real run against MinIO (`make up` first): `make seed` → prints `sample board: N departures at ~30 stations, replayed from 2026-08-xx`. Record N.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/serving/seed.py scripts/seed_sample_data.py Makefile tests/unit/test_seed.py
git commit -m "feat(serving): make seed replays a real silver day as the sample live board"
```

---
### Task 8: API skeleton — deps, problem+json, request ids, health and station search

**Files:**
- Modify: `pyproject.toml` (deps, pytest/mypy/ruff paths), `uv.lock`
- Create: `src/dbdelay/serving/stations.py`
- Create: `services/api/app/__init__.py`, `app/main.py`, `app/deps.py`, `app/errors.py`, `app/schemas.py`, `app/routers/__init__.py`, `app/routers/health.py`, `app/routers/stations.py`
- Create: `tests/api_support.py`
- Test: `tests/unit/test_station_search.py`, `tests/unit/test_api_basics.py`, `tests/unit/test_serving_imports.py`

**Interfaces:**
- Consumes: `ModelProvider`, `BoardSource`, `Settings`, `load_stations`, `Station`.
- Produces: `search_stations(stations, query) -> list[Station]`; `ServingDeps(settings, stations, models, board, clock)` with `.station(eva) -> Station` (raises `NotFoundError`) and `ServingDeps.from_settings(settings)`; `get_deps(request)`, `Deps = Annotated[ServingDeps, Depends(get_deps)]`; `create_app(deps: ServingDeps | None = None) -> FastAPI`; module `app.main.app`; `app.errors`: `PROBLEM_JSON`, `REQUEST_ID_HEADER`, `MAX_BODY_BYTES = 4096`, `problem(request, kind, detail)`, problem kinds; `EVA_PATTERN = r"^[1-9][0-9]{6}$"` in `app.schemas`. Test helper `tests/api_support.py::make_deps(store, *, now=NOW, bundle_files=None, board=None, cors_origins=())` and `client(deps, **kw) -> TestClient`.

- [ ] **Step 1: Add the approved dependencies**

```bash
uv add --optional api "fastapi>=0.120" "mangum>=0.19" "lightgbm>=4.7.0"
uv add --group api-local "uvicorn>=0.38"
uv add --dev "fastapi>=0.120" "mangum>=0.19" "httpx>=0.28"
```

(Use the newest releases uv resolves; lower bounds above are floors, adjust them to the resolved major/minor.) Then edit `pyproject.toml` so the dev group includes the local server group, and register the API sources:

```toml
[dependency-groups]
api-local = ["uvicorn>=0.38"]   # as written by uv
dev = [
    {include-group = "api-local"},
    # ... existing entries + fastapi, mangum, httpx
]

[tool.ruff]
src = ["src", "tests", "services/api"]

[tool.mypy]
files = ["src", "tests", "services/api"]
mypy_path = "src,services/api"

[tool.pytest.ini_options]
pythonpath = ["services/api"]
```

Add a comment above `[project.optional-dependencies]`' new line: `# API image (Phase 5): FastAPI + Mangum (Lambda adapter) + LightGBM; no scikit-learn.`
Run `uv sync` and `uv run python -c "import fastapi, mangum, uvicorn, httpx"`.

- [ ] **Step 2: Write failing tests**

`tests/unit/test_station_search.py`:

```python
from pathlib import Path

import pytest

from dbdelay.data.stations import load_stations
from dbdelay.serving.stations import search_stations

STATIONS = load_stations(Path(__file__).resolve().parents[2] / "configs" / "stations.yaml")


def names(query: str) -> list[str]:
    return [s.name for s in search_stations(STATIONS, query)]


def test_empty_query_lists_all_sorted() -> None:
    result = names("")
    assert len(result) == len(STATIONS)
    assert result == sorted(result)


@pytest.mark.parametrize("query", ["München", "munchen", "MUENCHEN", "  münchen  "])
def test_station_search_umlaut_spellings(query: str) -> None:
    assert names(query) == ["München Hbf"]


def test_prefix_matches_come_first() -> None:
    result = names("hbf")
    assert "Hamburg Hbf" in result
    assert names("ham")[0] == "Hamburg Hbf"


def test_punctuation_is_ignored() -> None:
    assert names("frankfurt main") == ["Frankfurt (Main) Hbf"]


@pytest.mark.parametrize("query", ["(", "%", "🚆", "zzz"])
def test_station_search_odd_input(query: str) -> None:
    assert names(query) in ([], names(""))
```

`tests/api_support.py`:

```python
"""Build the FastAPI app against moto storage with a fixed clock (no network)."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.deps import ServingDeps
from app.main import create_app
from dbdelay.config import Settings
from dbdelay.data.stations import load_stations
from dbdelay.registry.artifacts import write_bundle
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.serving.board import BOARD_KEY, BoardSource, LiveBoard, board_to_bytes
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.storage import ObjectStore
from tests.boards import NOW
from tests.bundles import bundle_manifest

STATIONS = tuple(load_stations(Path(__file__).resolve().parents[1] / "configs" / "stations.yaml"))


def make_deps(
    store: ObjectStore,
    *,
    now: datetime = NOW,
    bundle_files: dict[str, bytes] | None = None,
    board: LiveBoard | None = None,
    cors_origins: Sequence[str] = (),
) -> ServingDeps:
    """Deps on one moto bucket; ``bundle_files`` releases them as champion v1."""
    if bundle_files is not None:
        write_bundle(store, bundle_manifest(bundle_files, version="1"), bundle_files)
        ObjectStorePointer(store).set("1", None)
    if board is not None:
        store.put_bytes(BOARD_KEY, board_to_bytes(board))
    settings = Settings(_env_file=None, cors_origins=list(cors_origins))  # type: ignore[call-arg]
    return ServingDeps(
        settings=settings,
        stations=STATIONS,
        models=ModelProvider(store, ObjectStorePointer(store)),
        board=BoardSource(store),
        clock=lambda: now,
    )


def client(deps: ServingDeps, **kwargs: Any) -> TestClient:
    return TestClient(create_app(deps), **kwargs)
```

`tests/unit/test_api_basics.py`:

```python
import re

import pytest
from httpx import Response

from dbdelay.storage import ObjectStore
from tests.api_support import client, make_deps
from tests.boards import NOW, departure, live_board

PROBLEM = "application/problem+json"


def assert_problem(response: Response, status: int, type_: str) -> dict[str, object]:
    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM)
    body = response.json()
    assert body["type"] == type_
    assert body["status"] == status
    assert body["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    return body


def test_health_without_board_or_model(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_version": None, "board_generated_at": None}


def test_health_reports_model_and_board(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    deps = make_deps(s3_store, bundle_files=bundle_files, board=live_board(departure(5)))
    body = client(deps).get("/api/v1/health").json()
    assert body["model_version"] == "1"
    assert body["board_generated_at"].startswith("2026-10-05T13:00:00")


def test_stations_search(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    assert len(api.get("/api/v1/stations").json()) == 30
    assert api.get("/api/v1/stations", params={"q": "muenchen"}).json() == [
        {"eva": "8000261", "name": "München Hbf", "state": "BY"}
    ]


def test_long_query_is_a_validation_problem(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).get("/api/v1/stations", params={"q": "x" * 51})
    body = assert_problem(response, 422, "/errors/validation")
    assert "q" in str(body["detail"])


def test_request_id_is_echoed_or_replaced(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    assert api.get("/api/v1/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    replaced = api.get("/api/v1/health", headers={"X-Request-ID": "bad id\nInjected: 1"})
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["x-request-id"])


def test_unknown_route_is_a_problem(s3_store: ObjectStore) -> None:
    assert_problem(client(make_deps(s3_store)).get("/api/v1/nope"), 404, "/errors/http")


def test_post_body_limits(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    assert_problem(api.post("/api/v1/predict", content=b"x" * 4097), 413, "/errors/payload-too-large")
    chunked = api.post("/api/v1/predict", content=iter([b"{}"]))
    assert_problem(chunked, 411, "/errors/length-required")


def test_unexpected_error_is_a_500_problem_without_trace(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = make_deps(s3_store)

    def boom() -> str | None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(deps.models, "version_or_none", boom)
    response = client(deps, raise_server_exceptions=False).get("/api/v1/health")
    body = assert_problem(response, 500, "/errors/internal")
    assert "secret internals" not in response.text
    assert body["instance"] == "/api/v1/health"


def test_openapi_docs(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    assert api.get("/api/docs").status_code == 200
    assert "/api/v1/stations" in api.get("/api/openapi.json").json()["paths"]


def test_cors_only_when_configured(s3_store: ObjectStore) -> None:
    preflight = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
    }
    plain = client(make_deps(s3_store)).options("/api/v1/health", headers=preflight)
    assert "access-control-allow-origin" not in plain.headers
    dev = client(make_deps(s3_store, cors_origins=["http://localhost:5173"]))
    allowed = dev.options("/api/v1/health", headers=preflight)
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_clock_is_injected(s3_store: ObjectStore) -> None:
    assert make_deps(s3_store).clock() == NOW
```

Append `"dbdelay.serving.stations"` and `"app.main"` to `SERVING_MODULES`.

- [ ] **Step 3: Run — expect FAIL**: `uv run pytest tests/unit/test_station_search.py tests/unit/test_api_basics.py -q`

- [ ] **Step 4: Implement**

`src/dbdelay/serving/stations.py`:

```python
"""Station search for the UI: case-, accent- and umlaut-spelling-insensitive (München = muenchen)."""

import re
import unicodedata
from collections.abc import Sequence

from dbdelay.data.stations import Station

_UMLAUT_SPELLING = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
_NOT_ALNUM = re.compile(r"[^0-9a-z]+")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NOT_ALNUM.sub(" ", letters).strip()


def _keys(name: str) -> tuple[str, str]:
    folded = name.casefold()
    return _plain(folded), _plain(folded.translate(_UMLAUT_SPELLING))


def search_stations(stations: Sequence[Station], query: str) -> list[Station]:
    """Stations whose name contains ``query``; prefix matches first, then by name.
    An empty query returns all stations by name."""
    needle = _plain(query)
    if not needle:
        return sorted(stations, key=lambda s: s.name)
    hits = [s for s in stations if any(needle in key for key in _keys(s.name))]
    return sorted(
        hits, key=lambda s: (not any(k.startswith(needle) for k in _keys(s.name)), s.name)
    )
```

`services/api/app/__init__.py`:

```python
"""Pünktlich HTTP API (FastAPI). Thin layer over `dbdelay.serving` (architecture §7)."""
```

`services/api/app/schemas.py` (Task 9 appends the board/predict/model schemas):

```python
"""Request and response models (mirrored in frontend/src/api/types.ts — keep in sync)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EVA_PATTERN = r"^[1-9][0-9]{6}$"


class Problem(BaseModel):
    """RFC 9457 problem details (`application/problem+json`)."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str


class HealthOut(BaseModel):
    status: Literal["ok"]
    model_version: str | None
    board_generated_at: datetime | None


class StationOut(BaseModel):
    eva: str
    name: str
    state: str
```

`services/api/app/deps.py`:

```python
"""Collaborators of the API, built once per process (or injected by tests)."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import Depends, Request

from dbdelay.config import Settings, get_settings
from dbdelay.data.stations import Station, load_stations
from dbdelay.errors import NotFoundError
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.serving.board import BoardSource
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.storage import ObjectStore, make_s3_client

_BUILD_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ServingDeps:
    settings: Settings
    stations: tuple[Station, ...]
    models: ModelProvider
    board: BoardSource
    clock: Callable[[], datetime] = field(default=_utc_now)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ServingDeps":
        client = make_s3_client(settings)
        model_store = ObjectStore(client, settings.models_bucket)
        data_store = ObjectStore(client, settings.data_bucket)
        return cls(
            settings=settings,
            stations=tuple(load_stations(settings.stations_file)),
            models=ModelProvider(
                model_store, ObjectStorePointer(model_store), ttl_s=settings.model_pointer_ttl_s
            ),
            board=BoardSource(data_store, settings.board_key, ttl_s=settings.board_ttl_s),
        )

    def station(self, eva: str) -> Station:
        """Raises ``NotFoundError`` for an EVA outside the supported list."""
        for station in self.stations:
            if station.eva == eva:
                return station
        raise NotFoundError(f"EVA {eva} is not in the supported station list.")


def get_deps(request: Request) -> ServingDeps:
    """Injected deps, or deps from the environment on first use (Lambda/uvicorn)."""
    state = request.app.state
    if state.deps is None:
        with _BUILD_LOCK:
            if state.deps is None:
                state.deps = ServingDeps.from_settings(get_settings())
    return cast(ServingDeps, state.deps)


Deps = Annotated[ServingDeps, Depends(get_deps)]
```

`services/api/app/errors.py`:

```python
"""Problem details (RFC 9457), request ids and body-size limits (rules.md §6.3, §7)."""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dbdelay.errors import ExternalServiceError, ModelNotAvailableError, NotFoundError
from dbdelay.logging import get_logger

PROBLEM_JSON = "application/problem+json"
REQUEST_ID_HEADER = "X-Request-ID"
MAX_BODY_BYTES = 4096
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass(frozen=True)
class ProblemKind:
    type: str
    title: str
    status: int


STATION_NOT_SUPPORTED = ProblemKind("/errors/station-not-supported", "Station not supported", 404)
VALIDATION = ProblemKind("/errors/validation", "Invalid request", 422)
LENGTH_REQUIRED = ProblemKind("/errors/length-required", "Content-Length required", 411)
PAYLOAD_TOO_LARGE = ProblemKind("/errors/payload-too-large", "Request body too large", 413)
MODEL_UNAVAILABLE = ProblemKind(
    "/errors/model-unavailable", "Forecasts are temporarily unavailable", 503
)
BOARD_UNAVAILABLE = ProblemKind(
    "/errors/board-unavailable", "Live data is temporarily unavailable", 503
)
INTERNAL = ProblemKind("/errors/internal", "Internal error", 500)


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else uuid.uuid4().hex


def problem(request: Request, kind: ProblemKind, detail: str) -> JSONResponse:
    rid = request_id_of(request)
    body = {
        "type": kind.type,
        "title": kind.title,
        "status": kind.status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": rid,
    }
    return JSONResponse(
        body, status_code=kind.status, media_type=PROBLEM_JSON, headers={REQUEST_ID_HEADER: rid}
    )


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Assign a request id (a safe incoming one is reused) and enforce the body limit."""
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request.state.request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
    if request.method in _BODY_METHODS:
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return problem(request, LENGTH_REQUIRED, "Send the body with a Content-Length header.")
        if int(length) > MAX_BODY_BYTES:
            return problem(
                request, PAYLOAD_TOO_LARGE, f"The body must be at most {MAX_BODY_BYTES} bytes."
            )
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request.state.request_id
    return response


def _validation_detail(exc: RequestValidationError) -> str:
    # Field paths and messages only — never the submitted values.
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    )


def install_error_handlers(app: FastAPI) -> None:
    """Map project exceptions to problem responses (rules.md §6.3)."""

    async def not_found(request: Request, exc: Exception) -> Response:
        return problem(request, STATION_NOT_SUPPORTED, str(exc))

    async def invalid(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, RequestValidationError):  # registered for this type only
            raise exc
        return problem(request, VALIDATION, _validation_detail(exc))

    async def no_model(request: Request, exc: Exception) -> Response:
        get_logger("api").warning(
            "model unavailable", extra={"request_id": request_id_of(request), "error": str(exc)}
        )
        return problem(
            request, MODEL_UNAVAILABLE, "No forecast model is available right now."
        )

    async def no_board(request: Request, exc: Exception) -> Response:
        get_logger("api").warning(
            "live board unavailable", extra={"request_id": request_id_of(request), "error": str(exc)}
        )
        return problem(
            request, BOARD_UNAVAILABLE, "Live departures could not be loaded. Try again in a minute."
        )

    async def http_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, StarletteHTTPException):  # registered for this type only
            raise exc
        kind = ProblemKind("/errors/http", HTTPStatus(exc.status_code).phrase, exc.status_code)
        return problem(request, kind, str(exc.detail))

    async def unexpected(request: Request, exc: Exception) -> Response:
        get_logger("api").exception(
            "unhandled error", extra={"request_id": request_id_of(request)}
        )
        return problem(request, INTERNAL, "Something went wrong on our side.")

    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(RequestValidationError, invalid)
    app.add_exception_handler(ModelNotAvailableError, no_model)
    app.add_exception_handler(ExternalServiceError, no_board)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(Exception, unexpected)
```

`services/api/app/routers/__init__.py`: `"""API v1 routers."""`

`services/api/app/routers/health.py`:

```python
from fastapi import APIRouter

from app.deps import Deps
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(deps: Deps) -> HealthOut:
    """Liveness plus the loaded model version and board age (never fails on missing data)."""
    return HealthOut(
        status="ok",
        model_version=deps.models.version_or_none(),
        board_generated_at=deps.board.generated_at_or_none(),
    )
```

`services/api/app/routers/stations.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import Deps
from app.schemas import StationOut
from dbdelay.serving.stations import search_stations

router = APIRouter(tags=["stations"])


@router.get("/stations", response_model=list[StationOut])
def list_stations(deps: Deps, q: Annotated[str, Query(max_length=50)] = "") -> list[StationOut]:
    """Supported stations matching ``q`` (all when empty)."""
    return [
        StationOut(eva=s.eva, name=s.name, state=s.state) for s in search_stations(deps.stations, q)
    ]
```

`services/api/app/main.py`:

```python
"""FastAPI app factory (architecture §7). `app` is the uvicorn/Mangum entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.deps import ServingDeps
from app.errors import REQUEST_ID_HEADER, install_error_handlers, request_context
from app.routers import health, stations
from dbdelay.config import get_settings

API_PREFIX = "/api/v1"


def create_app(deps: ServingDeps | None = None) -> FastAPI:
    """Build the app; ``deps=None`` builds them from the environment on first request."""
    app = FastAPI(
        title="Pünktlich API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.deps = deps
    origins = deps.settings.cors_origins if deps is not None else get_settings().cors_origins
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )
    app.middleware("http")(request_context)
    install_error_handlers(app)
    for module in (health, stations):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
```

- [ ] **Step 5: Run — expect PASS**: `uv run pytest tests/unit/test_station_search.py tests/unit/test_api_basics.py tests/unit/test_serving_imports.py -q && uv run mypy && uv run ruff check .`. The `POST /api/v1/predict` checks pass before the route exists because the middleware runs before routing.

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add pyproject.toml uv.lock src/dbdelay/serving/stations.py services/api/app tests/api_support.py tests/unit/test_station_search.py tests/unit/test_api_basics.py tests/unit/test_serving_imports.py
git commit -m "feat(api): FastAPI app with problem+json, request ids, health and station search"
```

---

### Task 9: Departures, predict and model routes

**Files:**
- Modify: `services/api/app/schemas.py` (append), `services/api/app/main.py` (routers)
- Create: `services/api/app/routers/departures.py`, `predict.py`, `model.py`
- Test: `tests/unit/test_api_routes.py`

**Interfaces:**
- Consumes: `select_departures`, `is_stale`, `score_board`, `predict_one`, `ScheduleInput`, `Prediction`, `ServingDeps`, `ModelBundle.report`.
- Produces: JSON shapes of spec §6 (mirrored by `frontend/src/api/types.ts` in Task 12): `DeparturesOut`, `DepartureOut`, `TrainOut`, `PredictionOut`, `FactorOut`, `StationRef`, `PredictIn`, `PredictOut`, `ModelOut`, `MetricsOut`, `RiskThresholdsOut`; helpers `berlin(dt)`, `departure_out(row, prediction)`, `prediction_out(prediction)`, `model_out(bundle, previous_version)`.

- [ ] **Step 1: Write failing tests** `tests/unit/test_api_routes.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from dbdelay.registry.artifacts import models_prefix
from dbdelay.serving.board import LiveBoard
from dbdelay.storage import ObjectStore
from tests.api_support import client, make_deps
from tests.boards import FRANKFURT, MUENCHEN, NOW, departure, live_board
from tests.unit.test_api_basics import assert_problem

URL = f"/api/v1/stations/{FRANKFURT}/departures"
BODY = {
    "eva": FRANKFURT,
    "train_type": "ICE",
    "train_number": "1602",
    "line_number": None,
    "final_destination": "Berlin Hbf",
    "stop_index": 7,
    "planned_departure": "2026-10-05T17:10:00+02:00",
}


def _board() -> LiveBoard:
    return live_board(
        departure(5),
        departure(20, delay_min=8, changed_departure_utc=NOW + timedelta(minutes=28), stop_index=8),
        departure(40, is_cancelled=True, changed_departure_utc=None, delay_min=None),
        departure(15, eva=MUENCHEN),
    )


def test_board_with_predictions(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=_board()))
    body = api.get(URL).json()
    assert body["station"] == {"eva": FRANKFURT, "name": "Frankfurt (Main) Hbf"}
    assert (body["model_version"], body["data_source"], body["stale"]) == ("1", "sample", False)
    assert body["replayed_from"] == "2026-08-24"
    assert body["data_as_of"] == "2026-10-05T15:00:00+02:00"
    first, delayed, cancelled = body["departures"]
    assert first["planned_departure"] == "2026-10-05T15:05:00+02:00"
    assert first["train"] == {"type": "ICE", "number": "1602", "line": None, "destination": "Berlin Hbf"}
    assert first["platform"] == "7"
    assert 0 <= first["prediction"]["p_late"] <= 1
    assert first["prediction"]["risk_level"] in {"low", "medium", "high"}
    assert 1 <= len(first["prediction"]["top_factors"]) <= 3
    assert set(first["prediction"]["top_factors"][0]) == {"feature", "direction", "text"}
    assert delayed["live_delay_min"] == 8
    assert delayed["live_departure"] == "2026-10-05T15:28:00+02:00"
    assert cancelled["cancelled"] is True
    assert cancelled["prediction"] is None


def test_hours_filter(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    board = live_board(departure(30), departure(90))
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=board))
    assert len(api.get(URL, params={"hours": 1}).json()["departures"]) == 1
    assert len(api.get(URL, params={"hours": 6}).json()["departures"]) == 2


def test_board_without_model_still_lists_departures(s3_store: ObjectStore) -> None:
    body = client(make_deps(s3_store, board=_board())).get(URL).json()
    assert body["model_version"] is None
    assert len(body["departures"]) == 3
    assert all(d["prediction"] is None for d in body["departures"])


def test_board_with_tampered_model_degrades(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    deps = make_deps(s3_store, bundle_files=bundle_files, board=_board())
    s3_store.put_bytes(models_prefix("1") + "model.txt", b"tampered")
    body = client(deps).get(URL).json()
    assert body["model_version"] is None


def test_stale_board(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    board = live_board(departure(5), generated_at=NOW - timedelta(minutes=21))
    body = client(make_deps(s3_store, bundle_files=bundle_files, board=board)).get(URL).json()
    assert body["stale"] is True


def test_station_without_departures_is_empty(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=_board()))
    body = api.get("/api/v1/stations/8010101/departures").json()  # Erfurt: supported, no rows
    assert body["departures"] == []


def test_departure_times_use_berlin_offset_in_winter(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    winter = datetime(2026, 12, 7, 13, 0, tzinfo=UTC)
    board = live_board(departure(0, planned_departure_utc=winter + timedelta(minutes=5)), generated_at=winter)
    api = client(make_deps(s3_store, now=winter, bundle_files=bundle_files, board=board))
    assert api.get(URL).json()["departures"][0]["planned_departure"] == "2026-12-07T14:05:00+01:00"


@pytest.mark.parametrize(
    ("url", "status", "type_"),
    [
        ("/api/v1/stations/1234567/departures", 404, "/errors/station-not-supported"),
        ("/api/v1/stations/abc/departures", 422, "/errors/validation"),
        (URL + "?hours=0", 422, "/errors/validation"),
        (URL + "?hours=7", 422, "/errors/validation"),
    ],
)
def test_board_input_problems(s3_store: ObjectStore, url: str, status: int, type_: str) -> None:
    assert_problem(client(make_deps(s3_store, board=_board())).get(url), status, type_)


def test_missing_board_is_503(s3_store: ObjectStore) -> None:
    assert_problem(client(make_deps(s3_store)).get(URL), 503, "/errors/board-unavailable")


def test_predict(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    body = client(make_deps(s3_store, bundle_files=bundle_files)).post("/api/v1/predict", json=BODY).json()
    assert body["model_version"] == "1"
    assert 0 <= body["p_late"] <= 1
    assert body["risk_level"] in {"low", "medium", "high"}
    assert 1 <= len(body["top_factors"]) <= 3


def test_predict_station_unseen_by_model(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    # The test model only saw Frankfurt and München; Erfurt and train type "FLX" map to OTHER.
    body = BODY | {"eva": "8010101", "train_type": "flx"}
    response = client(make_deps(s3_store, bundle_files=bundle_files)).post("/api/v1/predict", json=body)
    assert response.status_code == 200
    assert 0 <= response.json()["p_late"] <= 1


def test_predict_matches_board(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    row = departure(5)
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=live_board(row)))
    board_p = api.get(URL).json()["departures"][0]["prediction"]["p_late"]
    single = BODY | {
        "stop_index": row.stop_index,
        "planned_departure": row.planned_departure_utc.isoformat(),
    }
    assert api.post("/api/v1/predict", json=single).json()["p_late"] == board_p


@pytest.mark.parametrize(
    ("change", "status", "type_"),
    [
        ({"planned_departure": "2026-10-05T17:10:00"}, 422, "/errors/validation"),  # naive
        ({"surprise": 1}, 422, "/errors/validation"),
        ({"stop_index": 0}, 422, "/errors/validation"),
        ({"eva": "1234567"}, 404, "/errors/station-not-supported"),
    ],
)
def test_predict_input_problems(
    s3_store: ObjectStore, bundle_files: dict[str, bytes], change: dict[str, object], status: int, type_: str
) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files))
    assert_problem(api.post("/api/v1/predict", json=BODY | change), status, type_)


def test_predict_without_model_is_503(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).post("/api/v1/predict", json=BODY)
    assert_problem(response, 503, "/errors/model-unavailable")


def test_model_info(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    body = client(make_deps(s3_store, bundle_files=bundle_files)).get("/api/v1/model").json()
    assert body["version"] == "1"
    assert body["previous_version"] is None
    assert body["model_name"] == "m"
    assert body["data_snapshot_id"] == "2026-03-31_abcdef12"
    assert body["metrics"]["test_brier"] == 0.14
    assert body["metrics"]["baseline_brier"] == 0.16
    assert body["risk_thresholds"] == {"medium": 0.2, "high": 0.45}
    assert body["train_window"] == {"start": "2026-03-01", "end": "2026-03-17"}


def test_model_info_without_model_is_503(s3_store: ObjectStore) -> None:
    assert_problem(client(make_deps(s3_store)).get("/api/v1/model"), 503, "/errors/model-unavailable")
```

- [ ] **Step 2: Run — expect FAIL** (404 for the new routes): `uv run pytest tests/unit/test_api_routes.py -q`

- [ ] **Step 3: Implement**

Append to `services/api/app/schemas.py` (extend the imports: `from datetime import date, datetime`, `from zoneinfo import ZoneInfo`, `from pydantic import AwareDatetime, BaseModel, ConfigDict, Field`, plus the dbdelay imports below):

```python
from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.calendar import BERLIN
from dbdelay.registry.artifacts import ModelBundle
from dbdelay.serving.board import BoardDeparture
from dbdelay.serving.scoring import Prediction

_BERLIN = ZoneInfo(BERLIN)
RiskLevelOut = Literal["low", "medium", "high"]


def berlin(value: datetime) -> datetime:
    """Display time zone of the API (architecture §7 examples use the Berlin offset)."""
    return value.astimezone(_BERLIN)


class FactorOut(BaseModel):
    feature: str
    direction: Literal["up", "down"]
    text: str


class PredictionOut(BaseModel):
    p_late: float
    risk_level: RiskLevelOut
    top_factors: list[FactorOut]


class TrainOut(BaseModel):
    type: str
    number: str | None
    line: str | None
    destination: str | None


class DepartureOut(BaseModel):
    event_id: str
    planned_departure: datetime
    live_departure: datetime | None
    live_delay_min: int | None
    cancelled: bool
    platform: str | None
    train: TrainOut
    prediction: PredictionOut | None


class StationRef(BaseModel):
    eva: str
    name: str


class DeparturesOut(BaseModel):
    station: StationRef
    data_as_of: datetime
    stale: bool
    data_source: Literal["sample", "live"]
    replayed_from: date | None
    model_version: str | None
    departures: list[DepartureOut]


class PredictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eva: str = Field(pattern=EVA_PATTERN)
    train_type: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9]+$")
    train_number: str | None = Field(default=None, max_length=10)
    line_number: str | None = Field(default=None, max_length=10)
    final_destination: str | None = Field(default=None, max_length=100)
    stop_index: int = Field(ge=1, le=200)
    planned_departure: AwareDatetime


class PredictOut(BaseModel):
    p_late: float
    risk_level: RiskLevelOut
    model_version: str
    top_factors: list[FactorOut]


class MetricsOut(BaseModel):
    test_brier: float | None
    test_auc: float | None
    test_pr_auc: float | None
    test_log_loss: float | None
    test_ece: float | None
    baseline_brier: float | None


class RiskThresholdsOut(BaseModel):
    medium: float
    high: float


class ModelOut(BaseModel):
    model_name: str
    version: str
    previous_version: str | None
    trained_at: datetime
    data_snapshot_id: str
    git_sha: str
    train_window: dict[str, date]
    metrics: MetricsOut
    risk_thresholds: RiskThresholdsOut


def prediction_out(prediction: Prediction) -> PredictionOut:
    return PredictionOut(
        p_late=prediction.p_late,
        risk_level=prediction.risk_level,
        top_factors=[
            FactorOut(feature=f.feature, direction=f.direction, text=f.text)
            for f in prediction.factors
        ],
    )


def departure_out(row: BoardDeparture, prediction: Prediction | None) -> DepartureOut:
    changed = row.changed_departure_utc
    return DepartureOut(
        event_id=row.event_id,
        planned_departure=berlin(row.planned_departure_utc),
        live_departure=None if changed is None else berlin(changed),
        live_delay_min=row.delay_min,
        cancelled=row.is_cancelled,
        platform=row.platform,
        train=TrainOut(
            type=row.train_type,
            number=row.train_number,
            line=row.line_number,
            destination=row.final_destination,
        ),
        prediction=None if prediction is None else prediction_out(prediction),
    )


def model_out(bundle: ModelBundle, previous_version: str | None) -> ModelOut:
    """Champion metadata for `/model` and the Health page.

    Raises:
        ModelNotAvailableError: if the bundle has no risk thresholds.
    """
    thresholds = bundle.spec.risk_thresholds
    if thresholds is None:
        raise ModelNotAvailableError(f"model v{bundle.manifest.version} has no risk thresholds")
    manifest, test = bundle.manifest, bundle.report.challenger_test.overall
    return ModelOut(
        model_name=manifest.model_name,
        version=manifest.version,
        previous_version=previous_version,
        trained_at=manifest.trained_at,
        data_snapshot_id=manifest.data_snapshot_id,
        git_sha=manifest.git_sha,
        train_window=manifest.train_window,
        metrics=MetricsOut(
            test_brier=test.brier,
            test_auc=test.roc_auc,
            test_pr_auc=test.pr_auc,
            test_log_loss=test.log_loss,
            test_ece=test.ece,
            baseline_brier=bundle.report.baseline_test.overall.brier,
        ),
        risk_thresholds=RiskThresholdsOut(medium=thresholds.medium, high=thresholds.high),
    )
```

`services/api/app/routers/departures.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from app.deps import Deps
from app.errors import request_id_of
from app.schemas import EVA_PATTERN, DeparturesOut, StationRef, berlin, departure_out
from dbdelay.serving.board import is_stale, select_departures
from dbdelay.serving.scoring import score_board

router = APIRouter(tags=["departures"])


@router.get("/stations/{eva}/departures", response_model=DeparturesOut)
def station_departures(
    request: Request,
    deps: Deps,
    eva: Annotated[str, Path(pattern=EVA_PATTERN)],
    hours: Annotated[int, Query(ge=1, le=6)] = 3,
) -> DeparturesOut:
    """Live board of one station for the next ``hours`` with delay-risk forecasts."""
    station = deps.station(eva)
    board = deps.board.get()
    now = deps.clock()
    rows = select_departures(board, eva, now, hours)
    scores = score_board(deps.models, rows, request_id=request_id_of(request))
    return DeparturesOut(
        station=StationRef(eva=station.eva, name=station.name),
        data_as_of=berlin(board.generated_at),
        stale=is_stale(board, now, deps.settings.board_stale_after_s),
        data_source=board.source,
        replayed_from=board.replayed_from,
        model_version=scores.model_version,
        departures=[
            departure_out(row, prediction)
            for row, prediction in zip(rows, scores.predictions, strict=True)
        ],
    )
```

`services/api/app/routers/predict.py`:

```python
from datetime import UTC

from fastapi import APIRouter, Request

from app.deps import Deps
from app.errors import request_id_of
from app.schemas import PredictIn, PredictOut, prediction_out
from dbdelay.serving.scoring import ScheduleInput, predict_one

router = APIRouter(tags=["predict"])


@router.post("/predict", response_model=PredictOut)
def predict(body: PredictIn, request: Request, deps: Deps) -> PredictOut:
    """Score one departure described by its timetable fields (for developers)."""
    deps.station(body.eva)
    item = ScheduleInput(
        eva=body.eva,
        train_type=body.train_type.upper(),  # silver stores train types upper-case
        line_number=body.line_number,
        final_destination=body.final_destination,
        stop_index=body.stop_index,
        planned_departure_utc=body.planned_departure.astimezone(UTC),
    )
    version, prediction = predict_one(deps.models, item, request_id=request_id_of(request))
    out = prediction_out(prediction)
    return PredictOut(
        p_late=out.p_late,
        risk_level=out.risk_level,
        model_version=version,
        top_factors=out.top_factors,
    )
```

`services/api/app/routers/model.py`:

```python
from fastapi import APIRouter

from app.deps import Deps
from app.schemas import ModelOut, model_out

router = APIRouter(tags=["model"])


@router.get("/model", response_model=ModelOut)
def model_info(deps: Deps) -> ModelOut:
    """Champion metadata: version, training window and test metrics."""
    bundle = deps.models.get()
    state = deps.models.pointer_state()
    return model_out(bundle, None if state is None else state.previous_version)
```

`main.py`: `from app.routers import departures, health, model, predict, stations` and `for module in (health, stations, departures, predict, model):`.

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit -q && uv run mypy && uv run ruff check .`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/api/app tests/unit/test_api_routes.py
git commit -m "feat(api): departures board, predict and model routes"
```

---

### Task 10: API image (Lambda + local), compose `api`, make targets

**Files:**
- Create: `services/api/lambda_handler.py`, `services/api/Dockerfile`, `services/api/requirements.txt`, `services/api/requirements-local.txt`
- Modify: `docker-compose.yml` (service `api`, profile `app`), `Makefile` (`app-up`, `app-down`, `api-dev`, `api-requirements`), `.dockerignore` (`frontend/node_modules`, `frontend/dist`)
- Test: `tests/unit/test_lambda_handler.py`

**Interfaces:**
- Produces: `lambda_handler.handler` (Mangum); images `puenktlich/api:lambda` (target `lambda`) and `puenktlich/api:local` (target `local`, uvicorn :8000); compose service `api` on `127.0.0.1:8000`.

- [ ] **Step 1: Write the failing test** `tests/unit/test_lambda_handler.py`:

```python
import json
from types import SimpleNamespace

import lambda_handler
from dbdelay.storage import ObjectStore
from tests.api_support import make_deps

EVENT = {
    "version": "2.0",
    "routeKey": "$default",
    "rawPath": "/api/v1/health",
    "rawQueryString": "",
    "headers": {"host": "api.example", "x-request-id": "lambda-1"},
    "requestContext": {
        "accountId": "000000000000",
        "apiId": "api",
        "domainName": "api.example",
        "domainPrefix": "api",
        "http": {
            "method": "GET",
            "path": "/api/v1/health",
            "protocol": "HTTP/1.1",
            "sourceIp": "203.0.113.1",
            "userAgent": "pytest",
        },
        "requestId": "req",
        "routeKey": "$default",
        "stage": "$default",
        "time": "05/Oct/2026:13:00:00 +0000",
        "timeEpoch": 1791205200000,
    },
    "isBase64Encoded": False,
}


def test_lambda_handler_serves_the_app(s3_store: ObjectStore) -> None:
    lambda_handler.app.state.deps = make_deps(s3_store)
    try:
        result = lambda_handler.handler(EVENT, SimpleNamespace(aws_request_id="ctx"))
    finally:
        lambda_handler.app.state.deps = None
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == "ok"
    assert result["headers"]["x-request-id"] == "lambda-1"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: lambda_handler`).

- [ ] **Step 3: Implement**

`services/api/lambda_handler.py`:

```python
"""AWS Lambda entry point: API Gateway HTTP API events → the FastAPI app (Mangum)."""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
```

Requirements (hash-pinned from `uv.lock`) — add to `Makefile` and run it:

```make
api-requirements: ## Regenerate the API image's hash-pinned requirements from uv.lock
	uv export --frozen --no-dev --extra api --no-emit-project --format requirements-txt -o services/api/requirements.txt
	uv export --frozen --only-group api-local --no-emit-project --format requirements-txt -o services/api/requirements-local.txt
```

Pin the base image: `docker pull public.ecr.aws/lambda/python:3.12` then `docker inspect --format "{{index .RepoDigests 0}}" public.ecr.aws/lambda/python:3.12` → use that digest below.

`services/api/Dockerfile`:

```dockerfile
# API image (Phase 5). Build context: repo root. linux/amd64 like Lambda x86_64.
#   lambda (default for AWS): Mangum handler on the AWS Lambda Python 3.12 base image.
#   local:  the same image + uvicorn on :8000 (compose profile "app").
FROM public.ecr.aws/lambda/python:3.12@sha256:<digest-from-docker-inspect> AS lambda

# LightGBM wheels need the OpenMP runtime, which the base image lacks.
RUN dnf install -y libgomp && dnf clean all

COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY configs/stations.yaml ${LAMBDA_TASK_ROOT}/configs/stations.yaml
COPY src/dbdelay ${LAMBDA_TASK_ROOT}/dbdelay
COPY services/api/app ${LAMBDA_TASK_ROOT}/app
COPY services/api/lambda_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]

FROM lambda AS local
COPY services/api/requirements-local.txt /tmp/requirements-local.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-local.txt --target "${LAMBDA_TASK_ROOT}"
WORKDIR ${LAMBDA_TASK_ROOT}
USER 1000:1000
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD []
```

(If `dnf` is missing in the base image, use `microdnf install -y libgomp && microdnf clean all`. The `lambda` stage must stay the last stage named `lambda`; Phase 6/8 build with `--target lambda`.)

`.dockerignore`: append `frontend/node_modules` and `frontend/dist`.

`docker-compose.yml` — header comment mentions profile `app`; add under `services:`:

```yaml
  # ---------------- App (Phase 5) — `make app-up` (profile "app") ----------------
  api:
    profiles: ["app"]
    build:
      context: .
      dockerfile: services/api/Dockerfile
      target: local
    image: puenktlich/api:local
    depends_on:
      minio-init:
        condition: service_completed_successfully
    environment:
      APP_ENV: local
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      STORAGE_ENDPOINT_URL: http://minio:9000
      STORAGE_ACCESS_KEY_ID: ${STORAGE_ACCESS_KEY_ID}
      STORAGE_SECRET_ACCESS_KEY: ${STORAGE_SECRET_ACCESS_KEY}
      DATA_BUCKET: ${DATA_BUCKET:-puenktlich-local}
      MODELS_BUCKET: ${MODELS_BUCKET:-puenktlich-local}
      CORS_ORIGINS: '["http://localhost:5173"]'
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 15s
    restart: unless-stopped
```

`Makefile` (add to `.PHONY`: `seed app-up app-down api-dev api-requirements web-check`):

```make
app-up: ## Start the API (:8000) and web app (:5173) with the core stack (profile "app")
	$(COMPOSE) --profile app up -d --build --wait

app-down: ## Stop the app and the core stack (volumes kept)
	$(COMPOSE) --profile app down

api-dev: ## Run the API on the host with auto-reload (needs `make up`)
	uv run uvicorn app.main:app --app-dir services/api --reload --port 8000
```

- [ ] **Step 4: Verify**
  - `uv run pytest tests/unit/test_lambda_handler.py -q` → PASS.
  - `docker build -f services/api/Dockerfile --target lambda -t puenktlich/api:lambda .` → succeeds.
  - Lambda emulator (built into the base image): `docker run --rm -d -p 127.0.0.1:9002:8080 --name api-rie puenktlich/api:lambda`, then post the test event: `uv run python -c "import json,urllib.request,tests.unit.test_lambda_handler as t; r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:9002/2015-03-31/functions/function/invocations', data=json.dumps(t.EVENT).encode())); print(r.read()[:300])"` → `statusCode` 200 (model/board `null` without MinIO env is expected). `docker stop api-rie`.
  - `make up && docker compose --profile app up -d --build --wait api` → `curl -s localhost:8000/api/v1/health` shows `"model_version":"1"` (champion v1 in MinIO) and after `make seed` a `board_generated_at`.
  - `curl -s "localhost:8000/api/v1/stations/8000105/departures?hours=3"` → departures with predictions. Record the image size (`docker images puenktlich/api`).

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/api/lambda_handler.py services/api/Dockerfile services/api/requirements*.txt docker-compose.yml Makefile .dockerignore tests/unit/test_lambda_handler.py
git commit -m "build(api): Lambda and local API image, compose app profile, make targets"
```

---

### Task 11: Integration — seed and serve the real champion

**Files:**
- Create: `tests/integration/conftest.py` (shared `scratch` fixture), `tests/integration/test_serving_minio.py`
- Modify: `tests/integration/test_training_mlflow.py` (use the shared fixture; delete its local copy)

**Interfaces:**
- Consumes: `seed_board`, `ServingDeps`, `create_app`, real MinIO + champion v1.

- [ ] **Step 1: Move the fixture** — cut `scratch` (and its imports) from `test_training_mlflow.py` into `tests/integration/conftest.py` unchanged.

- [ ] **Step 2: Write the test** `tests/integration/test_serving_minio.py`:

```python
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import ServingDeps
from app.main import create_app
from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.serving.board import BOARD_KEY, BoardSource
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.serving.seed import seed_board
from dbdelay.storage import ObjectStore, make_s3_client

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


def test_seed_then_serve_real_champion(scratch: tuple[ObjectStore, str]) -> None:
    store, root = scratch
    settings = get_settings()
    models = ObjectStore(make_s3_client(settings), settings.models_bucket)
    if ObjectStorePointer(models).get() is None:
        pytest.skip("no champion in MinIO — run `make train` first")
    now = datetime.now(UTC)
    board = seed_board(store, now=now, board_key=root + BOARD_KEY)
    upcoming = [
        d.eva
        for d in board.departures
        if not d.is_cancelled and now <= d.planned_departure_utc <= now + timedelta(hours=3)
    ]
    assert upcoming, "seeded board has no departures in the next 3 h"
    busiest = Counter(upcoming).most_common(1)[0][0]
    deps = ServingDeps(
        settings=settings,
        stations=tuple(load_stations(REPO / "configs" / "stations.yaml")),
        models=ModelProvider(models, ObjectStorePointer(models)),
        board=BoardSource(store, root + BOARD_KEY),
        clock=lambda: now,
    )
    api = TestClient(create_app(deps))
    body = api.get(f"/api/v1/stations/{busiest}/departures", params={"hours": 3}).json()
    assert body["data_source"] == "sample"
    assert body["model_version"] is not None
    scored = [d["prediction"] for d in body["departures"] if d["prediction"] is not None]
    assert scored
    assert all(0 <= p["p_late"] <= 1 and 1 <= len(p["top_factors"]) <= 3 for p in scored)
    assert api.get("/api/v1/model").json()["version"] == body["model_version"]
```

- [ ] **Step 3: Run**: `make up` (Docker Desktop running), then `make test-integration` → all integration tests pass (7 old + 1 new). Note the run time.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add tests/integration
git commit -m "test(serving): seed and serve the real champion against MinIO"
```

---
### Task 12: Frontend scaffold — tooling, tokens, layout, API client, compose `frontend`

**Files:**
- Create: `frontend/package.json` (via npm), `frontend/package-lock.json`, `frontend/index.html`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/eslint.config.js`, `frontend/.prettierrc.json`, `frontend/.prettierignore`, `frontend/public/favicon.svg`
- Create: `frontend/src/main.tsx`, `src/App.tsx`, `src/styles/tokens.css`, `src/api/types.ts`, `src/api/client.ts`, `src/lib/format.ts`, `src/lib/theme.ts`, `src/components/Layout.tsx`, `src/components/TopBar.tsx`, `src/components/Footer.tsx`, `src/pages/AboutPage.tsx`, `src/pages/NotFoundPage.tsx`, `src/test/setup.ts`
- Test: `frontend/src/lib/format.test.ts`, `frontend/src/lib/theme.test.ts`
- Modify: `docker-compose.yml` (service `frontend`), `Makefile` (`web-check`), `.pre-commit-config.yaml` only if a hook trips on generated files

**Interfaces:**
- Produces: TS types mirroring `schemas.py` (`Station`, `Factor`, `Prediction`, `Train`, `Departure`, `DeparturesResponse`, `ModelInfo`, `Problem`, `RiskLevel`); `api.stations(q)`, `api.departures(eva, hours)`, `api.model()`, `ApiError(status, problem)`; `formatTime(iso)`, `formatDate(iso)`, `percent(p)`, `minutesOld(iso, now?)`; `Theme`, `readTheme()`, `applyTheme(theme)`, `nextTheme(theme)`; `<Layout>`; routes `/`, `/station/:eva`, `/health`, `/about`.

- [ ] **Step 1: Create the project and install the approved packages** (host Node 26; npm 11):

```bash
mkdir -p frontend && cd frontend
npm init -y
npm pkg set name=puenktlich-frontend private=true type=module version=0.1.0
npm pkg delete main keywords author license description
npm install react react-dom react-router @tanstack/react-query lucide-react @fontsource-variable/inter @fontsource/jetbrains-mono tailwindcss @tailwindcss/vite
npm install -D vite @vitejs/plugin-react typescript vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom eslint @eslint/js typescript-eslint eslint-plugin-react-hooks prettier @types/react @types/react-dom
npm pkg set scripts.dev="vite" scripts.build="tsc --noEmit && vite build" scripts.preview="vite preview" scripts.typecheck="tsc --noEmit" scripts.lint="eslint . && prettier --check ." scripts.format="prettier --write ." scripts.test="vitest run"
npm pkg set engines.node=">=24"
```

Check `npm ls react` shows 19.x and `npm ls tailwindcss` 4.x; run `npm audit --omit=dev` and note the result.

- [ ] **Step 2: Config files**

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src"]
}
```

`frontend/vite.config.ts`:

```ts
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In compose the API is http://api:8000; on the host http://localhost:8000.
const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";
// Docker Desktop bind mounts on Windows do not deliver file events → poll.
const polling = process.env.CHOKIDAR_USEPOLLING === "true";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: apiTarget, changeOrigin: true } },
    watch: polling ? { usePolling: true, interval: 300 } : undefined,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
```

`frontend/eslint.config.js`:

```js
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
);
```

`frontend/.prettierrc.json`: `{ "printWidth": 100 }` · `frontend/.prettierignore`: `dist`, `node_modules`, `package-lock.json`.

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="description" content="How likely is your train to leave 6+ minutes late?" />
    <title>Pünktlich?</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/public/favicon.svg` (design.md §10: primary rounded square, white clock, accent dot):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#2F5BEA"/>
  <circle cx="15" cy="17" r="9" fill="none" stroke="#FFFFFF" stroke-width="2.5"/>
  <path d="M15 12v5.5l3.5 2" fill="none" stroke="#FFFFFF" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="25" cy="7" r="3.5" fill="#F5B700"/>
</svg>
```

- [ ] **Step 3: Tokens** — `frontend/src/styles/tokens.css` = design.md §11 verbatim, with the `[data-theme="dark"]` block written out (same values as the media-query block) and these additions:

```css
:root { --card-shadow: 0 1px 2px rgb(14 23 38 / 0.06), 0 1px 1px rgb(14 23 38 / 0.04); --overlay: rgb(14 23 38 / 0.45); }
/* dark blocks: */ --card-shadow: none; --overlay: rgb(0 0 0 / 0.6);
@theme inline { /* … design.md entries … */ --shadow-card: var(--card-shadow); --color-overlay: var(--overlay); }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; animation: none !important; } }
:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.sr-only-focusable:not(:focus) { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
```

- [ ] **Step 4: Write failing tests**

`frontend/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  localStorage.clear();
});
```

`frontend/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatDate, formatTime, minutesOld, percent } from "./format";

describe("format", () => {
  it("shows 24-hour Berlin time", () => {
    expect(formatTime("2026-10-05T15:42:00+02:00")).toBe("15:42");
    expect(formatTime("2026-12-07T13:05:00Z")).toBe("14:05");
  });
  it("shows dates in Berlin", () => {
    expect(formatDate("2026-08-24")).toBe("24 Aug 2026");
  });
  it("rounds probabilities to whole percent", () => {
    expect(percent(0.344)).toBe("34%");
    expect(percent(0.996)).toBe("100%");
  });
  it("counts whole minutes since a time", () => {
    expect(minutesOld("2026-10-05T13:00:00Z", new Date("2026-10-05T13:27:30Z"))).toBe(27);
    expect(minutesOld("2026-10-05T13:00:00Z", new Date("2026-10-05T12:00:00Z"))).toBe(0);
  });
});
```

`frontend/src/lib/theme.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { applyTheme, nextTheme, readTheme } from "./theme";

describe("theme", () => {
  it("cycles system → light → dark → system", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });
  it("applies and remembers the choice", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(readTheme()).toBe("dark");
    applyTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(readTheme()).toBe("system");
  });
});
```

Run `npm --prefix frontend test` → FAIL (modules missing).

- [ ] **Step 5: Implement**

`frontend/src/api/types.ts`:

```ts
// Mirror of services/api/app/schemas.py — keep in sync (rules.md §4).
export type RiskLevel = "low" | "medium" | "high";
export type Direction = "up" | "down";

export interface Station {
  eva: string;
  name: string;
  state: string;
}
export interface Factor {
  feature: string;
  direction: Direction;
  text: string;
}
export interface Prediction {
  p_late: number;
  risk_level: RiskLevel;
  top_factors: Factor[];
}
export interface Train {
  type: string;
  number: string | null;
  line: string | null;
  destination: string | null;
}
export interface Departure {
  event_id: string;
  planned_departure: string;
  live_departure: string | null;
  live_delay_min: number | null;
  cancelled: boolean;
  platform: string | null;
  train: Train;
  prediction: Prediction | null;
}
export interface DeparturesResponse {
  station: { eva: string; name: string };
  data_as_of: string;
  stale: boolean;
  data_source: "sample" | "live";
  replayed_from: string | null;
  model_version: string | null;
  departures: Departure[];
}
export interface ModelInfo {
  model_name: string;
  version: string;
  previous_version: string | null;
  trained_at: string;
  data_snapshot_id: string;
  git_sha: string;
  train_window: { start: string; end: string };
  metrics: {
    test_brier: number | null;
    test_auc: number | null;
    test_pr_auc: number | null;
    test_log_loss: number | null;
    test_ece: number | null;
    baseline_brier: number | null;
  };
  risk_thresholds: { medium: number; high: number };
}
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  request_id: string;
}
```

`frontend/src/api/client.ts`:

```ts
import type { DeparturesResponse, ModelInfo, Problem, Station } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | null;

  constructor(status: number, problem: Problem | null) {
    super(problem?.title ?? `HTTP ${status}`);
    this.status = status;
    this.problem = problem;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    let problem: Problem | null = null;
    try {
      problem = (await response.json()) as Problem;
    } catch {
      problem = null;
    }
    throw new ApiError(response.status, problem);
  }
  return (await response.json()) as T;
}

export const api = {
  stations: (query: string, signal?: AbortSignal) =>
    getJson<Station[]>(`/api/v1/stations?q=${encodeURIComponent(query)}`, signal),
  departures: (eva: string, hours: number, signal?: AbortSignal) =>
    getJson<DeparturesResponse>(
      `/api/v1/stations/${encodeURIComponent(eva)}/departures?hours=${hours}`,
      signal,
    ),
  model: (signal?: AbortSignal) => getJson<ModelInfo>("/api/v1/model", signal),
};
```

`frontend/src/lib/format.ts`:

```ts
const BERLIN = "Europe/Berlin";
const TIME = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: BERLIN,
});
const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: BERLIN,
});

export const formatTime = (iso: string): string => TIME.format(new Date(iso));
export const formatDate = (iso: string): string => DATE.format(new Date(iso));
export const percent = (p: number): string => `${Math.round(p * 100)}%`;

export function minutesOld(iso: string, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
}
```

`frontend/src/lib/theme.ts`:

```ts
export type Theme = "system" | "light" | "dark";
const KEY = "puenktlich.theme";

export function readTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // Storage blocked (private mode): the choice lasts for this page view only.
  }
}

export const nextTheme = (theme: Theme): Theme =>
  theme === "system" ? "light" : theme === "light" ? "dark" : "system";
```

`frontend/src/components/TopBar.tsx`:

```tsx
import { Activity, Info, TrainFront, type LucideIcon } from "lucide-react";
import { Link, NavLink, useLocation } from "react-router";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}
const NAV: NavItem[] = [
  { to: "/", label: "Board", icon: TrainFront },
  { to: "/health", label: "Health", icon: Activity },
  { to: "/about", label: "About", icon: Info },
];

export function TopBar() {
  const { pathname } = useLocation();
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex h-16 max-w-[960px] items-center justify-between px-4 md:px-6">
        <Link to="/" className="rounded-md text-xl font-[650] text-text">
          Pünktlich<span className="text-primary">?</span>
        </Link>
        <nav aria-label="Main">
          <ul className="flex gap-1">
            {NAV.map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end
                  className={({ isActive }) => {
                    const active = isActive || (to === "/" && pathname.startsWith("/station/"));
                    return `flex min-h-11 min-w-11 flex-col items-center justify-center gap-0.5 rounded-md px-2 text-xs transition-colors duration-150 sm:flex-row sm:gap-2 sm:px-3 sm:text-sm ${
                      active ? "bg-primary-soft text-primary" : "text-muted hover:text-text"
                    }`;
                  }}
                >
                  <Icon aria-hidden="true" className="size-5" />
                  <span>{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
```

`frontend/src/components/Footer.tsx`:

```tsx
import { Monitor, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { applyTheme, nextTheme, readTheme, type Theme } from "../lib/theme";

const THEME_ICON = { system: Monitor, light: Sun, dark: Moon } as const;
const THEME_LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

export function Footer() {
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  const Icon = THEME_ICON[theme];
  function cycle() {
    const next = nextTheme(theme);
    applyTheme(next);
    setTheme(next);
  }
  return (
    <footer className="mt-12 border-t border-border">
      <div className="mx-auto max-w-[960px] space-y-3 px-4 py-6 text-xs font-medium text-muted md:px-6">
        <p>
          Data: Deutsche Bahn Timetables API &amp; piebro/deutsche-bahn-data (CC BY 4.0). Pünktlich
          is an independent student project and is <strong>not affiliated with Deutsche Bahn</strong>.
          Predictions are estimates.
        </p>
        <button
          type="button"
          onClick={cycle}
          className="inline-flex min-h-11 items-center gap-2 rounded-md px-2 text-muted hover:text-text"
        >
          <Icon aria-hidden="true" className="size-4" />
          Theme: {THEME_LABEL[theme]}
        </button>
      </div>
    </footer>
  );
}
```

`frontend/src/components/Layout.tsx`:

```tsx
import type { ReactNode } from "react";
import { Footer } from "./Footer";
import { TopBar } from "./TopBar";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <a
        href="#main"
        className="sr-only-focusable absolute left-2 top-2 z-50 rounded-md bg-primary px-3 py-2 text-primary-fg"
      >
        Skip to content
      </a>
      <TopBar />
      <main id="main" className="mx-auto w-full max-w-[960px] flex-1 px-4 py-6 md:px-6">
        {children}
      </main>
      <Footer />
    </div>
  );
}
```

`frontend/src/pages/AboutPage.tsx` — sections (use the `design:ux-copy` skill on this text):
- "What is Pünktlich?" — "An estimate of how likely a train is to leave **6 or more minutes late**, for about 30 large German stations, for the next few hours."
- "How it works" — ordered list: timetable data (station, train type, line, destination, stop number, time, weekday, holidays) → a LightGBM model trained on 9 months of history (Dec 2025 – Aug 2026) → calibrated probability → Low (under 20%), Medium (20–45%), High (45% or more).
- "Limitations" — estimates, not promises; uses the timetable only, not today's disruptions; trained without autumn months, so October–November may be less accurate; in this local version the board shows real departures from a past day replayed onto today.
- "Data and credits" — Deutsche Bahn Timetables API, piebro/deutsche-bahn-data (CC BY 4.0); link `https://github.com/piyal21/Puenktlich-mlops-project`; not affiliated with Deutsche Bahn.
Use `<h1 className="text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10">`, `<h2 className="text-xl font-semibold">`, body `text-base`, max line length via `max-w-prose`.

`frontend/src/pages/NotFoundPage.tsx`: H1 "Page not found" + `<Link to="/">Go to the board</Link>`.

Placeholders until Tasks 14–15 replace them: `frontend/src/pages/BoardPage.tsx` = `export function BoardPage() { return <p>Board coming next</p>; }` and `frontend/src/pages/HealthPage.tsx` = `export function HealthPage() { return <p>Health coming next</p>; }`.

`frontend/src/App.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { AboutPage } from "./pages/AboutPage";
import { BoardPage } from "./pages/BoardPage";
import { HealthPage } from "./pages/HealthPage";
import { NotFoundPage } from "./pages/NotFoundPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<BoardPage />} />
            <Route path="/station/:eva" element={<BoardPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/about" element={<AboutPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
```

`frontend/src/main.tsx`:

```tsx
import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./styles/tokens.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme, readTheme } from "./lib/theme";

applyTheme(readTheme());
const root = document.getElementById("root");
if (!root) throw new Error("#root element missing in index.html");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: compose + make**

Pin Node: `docker pull node:24-alpine && docker run --rm node:24-alpine node --version` → use `node:<that version>-alpine`.

`docker-compose.yml` (after `api`):

```yaml
  frontend:
    profiles: ["app"]
    image: node:24.X.Y-alpine   # pinned in Step 6
    working_dir: /app
    command: ["sh", "-c", "npm ci && npm run dev -- --host 0.0.0.0"]
    environment:
      VITE_API_PROXY: http://api:8000
      CHOKIDAR_USEPOLLING: "true"
    volumes:
      - ./frontend:/app
      - frontend-node-modules:/app/node_modules   # Linux binaries stay out of the Windows tree
    ports:
      - "127.0.0.1:5173:5173"
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://localhost:5173/"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 60s
    restart: unless-stopped
```

Add `frontend-node-modules:` under `volumes:`. `Makefile`:

```make
web-check: ## Frontend lint, typecheck and component tests (needs `npm --prefix frontend ci`)
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck
	npm --prefix frontend test
```

- [ ] **Step 7: Verify** — `npm --prefix frontend test` PASS (format, theme); `npm --prefix frontend run lint` and `typecheck` clean (with placeholder pages); `npm --prefix frontend run build` succeeds.

- [ ] **Step 8: Commit**

```bash
git add frontend docker-compose.yml Makefile
git commit -m "feat(web): Vite React app shell with design tokens, layout, API client and compose service"
```

---

### Task 13: Core components — RiskBadge, DepartureRow, Banner, ProbabilityBar, DepartureDetail

**Files:**
- Create: `frontend/src/components/RiskBadge.tsx`, `DepartureRow.tsx`, `Banner.tsx`, `ProbabilityBar.tsx`, `DepartureDetail.tsx`, `frontend/src/test/fixtures.ts`
- Test: `frontend/src/components/RiskBadge.test.tsx`, `DepartureRow.test.tsx`, `DepartureDetail.test.tsx`

**Interfaces:**
- Produces: `<RiskBadge prediction cancelled?>`; `<DepartureRow departure onOpen>` (renders an `<li>` with a button); `<Banner variant="info"|"stale"|"error" action?>`; `<ProbabilityBar prediction thresholds>`; `<DepartureDetail departure thresholds modelVersion trainedAt dataAsOf onClose>`; fixtures `makeDeparture(overrides)`, `makeBoard(overrides)`, `MODEL`.

- [ ] **Step 1: Fixtures** `frontend/src/test/fixtures.ts`:

```ts
import type { Departure, DeparturesResponse, ModelInfo } from "../api/types";

export function makeDeparture(overrides: Partial<Departure> = {}): Departure {
  return {
    event_id: "e1",
    planned_departure: "2026-10-05T17:42:00+02:00",
    live_departure: "2026-10-05T17:42:00+02:00",
    live_delay_min: 0,
    cancelled: false,
    platform: "3",
    train: { type: "RE", number: "4711", line: "RE1", destination: "Göttingen" },
    prediction: {
      p_late: 0.34,
      risk_level: "medium",
      top_factors: [
        { feature: "stop_index", direction: "up", text: "Late stop in a long journey" },
        { feature: "weekday", direction: "up", text: "Fridays are busier" },
        { feature: "hour_local", direction: "down", text: "Departures around 17:00 are usually on time" },
      ],
    },
    ...overrides,
  };
}

export function makeBoard(overrides: Partial<DeparturesResponse> = {}): DeparturesResponse {
  return {
    station: { eva: "8010101", name: "Erfurt Hbf" },
    data_as_of: "2026-10-05T15:30:00+02:00",
    stale: false,
    data_source: "live",
    replayed_from: null,
    model_version: "1",
    departures: [makeDeparture(), makeDeparture({ event_id: "e2" })],
    ...overrides,
  };
}

export const MODEL: ModelInfo = {
  model_name: "puenktlich-delay",
  version: "1",
  previous_version: null,
  trained_at: "2026-10-04T10:00:00Z",
  data_snapshot_id: "2026-08-31_38b45c7a",
  git_sha: "ad92422",
  train_window: { start: "2025-12-01", end: "2026-08-03" },
  metrics: {
    test_brier: 0.1408,
    test_auc: 0.8078,
    test_pr_auc: 0.5959,
    test_log_loss: null,
    test_ece: 0.0218,
    baseline_brier: 0.152,
  },
  risk_thresholds: { medium: 0.2, high: 0.45 },
};
```

(Use the real `MODEL` numbers from `models/1/metrics.json` / Phase 4 CHANGELOG; replace `train_window`, `test_log_loss` with the real values when writing the file.)

- [ ] **Step 2: Write failing tests**

`RiskBadge.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskBadge } from "./RiskBadge";

describe("RiskBadge", () => {
  it.each([
    ["low", 0.12, "Low · 12%", "Delay risk low, 12 percent"],
    ["medium", 0.34, "Medium · 34%", "Delay risk medium, 34 percent"],
    ["high", 0.61, "High · 61%", "Delay risk high, 61 percent"],
  ] as const)("%s shows icon, word and number", (level, p, text, name) => {
    render(<RiskBadge prediction={{ p_late: p, risk_level: level, top_factors: [] }} />);
    const badge = screen.getByRole("img", { name });
    expect(badge).toHaveTextContent(text);
    expect(badge.querySelector("svg")).not.toBeNull();
  });
  it("cancelled wins over a forecast", () => {
    render(<RiskBadge prediction={{ p_late: 0.5, risk_level: "high", top_factors: [] }} cancelled />);
    expect(screen.getByRole("img", { name: "Cancelled" })).toHaveTextContent("Cancelled");
  });
  it("no forecast", () => {
    render(<RiskBadge prediction={null} />);
    expect(screen.getByRole("img", { name: "No forecast available" })).toHaveTextContent("No forecast");
  });
});
```

`DepartureRow.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeDeparture } from "../test/fixtures";
import { DepartureRow } from "./DepartureRow";

const renderRow = (d = makeDeparture(), onOpen = vi.fn()) =>
  render(
    <ul>
      <DepartureRow departure={d} onOpen={onOpen} />
    </ul>,
  );

describe("DepartureRow", () => {
  it("shows time, train, destination, platform and risk", () => {
    renderRow();
    expect(screen.getByText("17:42")).toBeInTheDocument();
    expect(screen.getByText("Göttingen")).toHaveAttribute("lang", "de");
    expect(screen.getByText("Pl. 3")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Delay risk medium/ })).toBeInTheDocument();
  });
  it("shows the live delay", () => {
    renderRow(makeDeparture({ live_departure: "2026-10-05T17:50:00+02:00", live_delay_min: 8 }));
    expect(screen.getByText("17:50 +8")).toHaveClass("text-risk-high");
  });
  it("cancelled rows are struck through and dimmed", () => {
    renderRow(makeDeparture({ cancelled: true, prediction: null, live_departure: null, live_delay_min: null }));
    expect(screen.getByText("17:42")).toHaveClass("line-through");
    expect(screen.getByRole("button")).toHaveClass("opacity-70");
    expect(screen.getByRole("img", { name: "Cancelled" })).toBeInTheDocument();
  });
  it("opens the detail on click", async () => {
    const onOpen = vi.fn();
    const d = makeDeparture();
    renderRow(d, onOpen);
    await userEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith(d);
  });
});
```

`DepartureDetail.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MODEL, makeDeparture } from "../test/fixtures";
import { DepartureDetail } from "./DepartureDetail";

const open = (overrides = {}, onClose = vi.fn()) =>
  render(
    <DepartureDetail
      departure={makeDeparture(overrides)}
      thresholds={MODEL.risk_thresholds}
      modelVersion="1"
      trainedAt={MODEL.trained_at}
      dataAsOf="2026-10-05T15:30:00+02:00"
      onClose={onClose}
    />,
  );

describe("DepartureDetail", () => {
  it("is a labelled modal dialog with the close button focused", () => {
    open();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName(/17:42/);
    expect(screen.getByRole("button", { name: "Close details" })).toHaveFocus();
  });
  it("shows the probability and the reasons", () => {
    open();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "34");
    expect(screen.getByText("34% chance of leaving 6+ minutes late")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Late stop in a long journey")).toBeInTheDocument();
    expect(screen.getByText(/Model v1/)).toHaveTextContent("Model v1 · trained 4 Oct 2026 · data as of 15:30");
  });
  it("closes on Escape", async () => {
    const onClose = vi.fn();
    open({}, onClose);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
  it("explains a cancelled departure and a missing forecast", () => {
    open({ cancelled: true, prediction: null });
    expect(screen.getByText("This departure is cancelled.")).toBeInTheDocument();
  });
  it("keeps Tab inside the dialog", async () => {
    open();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Close details" })).toHaveFocus();
  });
});
```

Run `npm --prefix frontend test` → FAIL.

- [ ] **Step 3: Implement**

`RiskBadge.tsx`:

```tsx
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { Prediction } from "../api/types";
import { percent } from "../lib/format";

interface Variant {
  label: string;
  icon: LucideIcon;
  className: string;
}
const VARIANTS: Record<"low" | "medium" | "high" | "cancelled" | "none", Variant> = {
  low: { label: "Low", icon: CheckCircle2, className: "bg-risk-low-soft text-risk-low" },
  medium: { label: "Medium", icon: AlertTriangle, className: "bg-risk-medium-soft text-risk-medium" },
  high: { label: "High", icon: AlertOctagon, className: "bg-risk-high-soft text-risk-high" },
  cancelled: { label: "Cancelled", icon: XCircle, className: "bg-cancelled-soft text-cancelled" },
  none: { label: "No forecast", icon: HelpCircle, className: "bg-surface-2 text-muted" },
};

export function RiskBadge({
  prediction,
  cancelled = false,
}: {
  prediction: Prediction | null;
  cancelled?: boolean;
}) {
  const key = cancelled ? "cancelled" : (prediction?.risk_level ?? "none");
  const { label, icon: Icon, className } = VARIANTS[key];
  const shown = !cancelled && prediction ? prediction : null;
  const name = cancelled
    ? "Cancelled"
    : shown
      ? `Delay risk ${label.toLowerCase()}, ${Math.round(shown.p_late * 100)} percent`
      : "No forecast available";
  return (
    <span
      role="img"
      aria-label={name}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-medium whitespace-nowrap ${className}`}
    >
      <Icon aria-hidden="true" className="size-4" />
      {shown ? `${label} · ${percent(shown.p_late)}` : label}
    </span>
  );
}
```

`DepartureRow.tsx`:

```tsx
import type { Departure } from "../api/types";
import { formatTime } from "../lib/format";
import { RiskBadge } from "./RiskBadge";

// Live time is shown only for delayed trains (an on-time duplicate of the planned time is noise).
function delayClass(delay: number): string {
  return delay >= 6 ? "text-risk-high" : "text-risk-medium";
}

export function DepartureRow({
  departure,
  onOpen,
}: {
  departure: Departure;
  onOpen: (departure: Departure) => void;
}) {
  const { train, cancelled, live_departure: live } = departure;
  const delay = departure.live_delay_min ?? 0;
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(departure)}
        className={`grid w-full grid-cols-[auto_1fr_auto] items-start gap-3 rounded-lg border border-border bg-surface p-3 text-left shadow-card transition-colors duration-150 hover:bg-surface-2 ${
          cancelled ? "opacity-70" : ""
        }`}
      >
        <span className="font-mono tabular">
          <span className={`block text-xl font-medium ${cancelled ? "line-through" : ""}`}>
            {formatTime(departure.planned_departure)}
          </span>
          {!cancelled && live && delay > 0 ? (
            <span className={`block text-sm ${delayClass(delay)}`}>
              {formatTime(live)} +{delay}
            </span>
          ) : null}
        </span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-xs font-medium">
              {train.type} <span className="font-mono">{train.number ?? ""}</span>
            </span>
            {train.line ? <span className="text-muted">{train.line}</span> : null}
          </span>
          <span className="block truncate font-semibold">
            <span aria-hidden="true">→ </span>
            <span lang="de">{train.destination ?? "Destination unknown"}</span>
          </span>
          {departure.platform ? (
            <span className="block text-sm text-muted">Pl. {departure.platform}</span>
          ) : null}
        </span>
        <RiskBadge prediction={departure.prediction} cancelled={cancelled} />
      </button>
    </li>
  );
}
```

`Banner.tsx`:

```tsx
import { AlertOctagon, Clock, Info } from "lucide-react";
import type { ReactNode } from "react";

type Variant = "info" | "stale" | "error";
const STYLE: Record<Variant, string> = {
  info: "bg-primary-soft text-text",
  stale: "bg-accent-soft text-accent-ink",
  error: "bg-risk-high-soft text-text",
};
const ICON = { info: Info, stale: Clock, error: AlertOctagon } as const;

export function Banner({
  variant,
  children,
  action,
}: {
  variant: Variant;
  children: ReactNode;
  action?: ReactNode;
}) {
  const Icon = ICON[variant];
  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={`flex flex-wrap items-center gap-3 rounded-md px-4 py-3 text-sm ${STYLE[variant]}`}
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
      {action}
    </div>
  );
}
```

`ProbabilityBar.tsx`:

```tsx
import type { Prediction } from "../api/types";

const FILL = { low: "bg-risk-low", medium: "bg-risk-medium", high: "bg-risk-high" } as const;

export function ProbabilityBar({
  prediction,
  thresholds,
}: {
  prediction: Prediction;
  thresholds: { medium: number; high: number } | null;
}) {
  const pct = Math.round(prediction.p_late * 100);
  const ticks = thresholds ? [thresholds.medium, thresholds.high] : [];
  return (
    <div>
      <div
        role="meter"
        aria-label="Chance of leaving 6 or more minutes late"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct} percent`}
        className="relative h-3 rounded-full bg-surface-2"
      >
        <div className={`h-3 rounded-full ${FILL[prediction.risk_level]}`} style={{ width: `${pct}%` }} />
        {ticks.map((t) => (
          <div
            key={t}
            aria-hidden="true"
            className="absolute -top-1 h-5 w-0.5 bg-text"
            style={{ left: `${t * 100}%` }}
          />
        ))}
      </div>
      {ticks.length > 0 ? (
        <div aria-hidden="true" className="relative mt-1 h-4 text-xs text-muted">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2" style={{ left: `${t * 100}%` }}>
              {Math.round(t * 100)}%
            </span>
          ))}
        </div>
      ) : null}
      <p className="mt-3 text-base">{pct}% chance of leaving 6+ minutes late</p>
    </div>
  );
}
```

`DepartureDetail.tsx`:

```tsx
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import type { Departure } from "../api/types";
import { formatDate, formatTime } from "../lib/format";
import { ProbabilityBar } from "./ProbabilityBar";

interface Props {
  departure: Departure;
  thresholds: { medium: number; high: number } | null;
  modelVersion: string | null;
  trainedAt: string | null;
  dataAsOf: string;
  onClose: () => void;
}

const FOCUSABLE = 'button, [href], input, [tabindex]:not([tabindex="-1"])';

export function DepartureDetail({ departure, thresholds, modelVersion, trainedAt, dataAsOf, onClose }: Props) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
    return () => previous?.focus();
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !panel.current) return;
    const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const { train, prediction } = departure;
  const footer = [
    modelVersion ? `Model v${modelVersion}` : "No model",
    trainedAt ? `trained ${formatDate(trainedAt)}` : null,
    `data as of ${formatTime(dataAsOf)}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-overlay lg:items-stretch lg:justify-end" onClick={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        className="max-h-[85vh] w-full overflow-y-auto rounded-t-lg bg-surface p-4 shadow-card lg:h-full lg:max-h-none lg:w-[420px] lg:rounded-none lg:p-6"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id={titleId} className="text-xl font-semibold">
              <span className="font-mono tabular">{formatTime(departure.planned_departure)}</span>{" "}
              {train.type} {train.number ?? ""} <span aria-hidden="true">→</span>{" "}
              <span lang="de">{train.destination ?? "Destination unknown"}</span>
            </h2>
            <p className="text-sm text-muted">
              {departure.platform ? `Platform ${departure.platform}` : "Platform not known yet"}
              {train.line ? ` · Line ${train.line}` : ""}
            </p>
          </div>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-md hover:bg-surface-2"
          >
            <X aria-hidden="true" className="size-5" />
          </button>
        </div>

        <div className="mt-6 space-y-6">
          {departure.cancelled ? (
            <p>This departure is cancelled.</p>
          ) : prediction ? (
            <>
              <ProbabilityBar prediction={prediction} thresholds={thresholds} />
              <section aria-labelledby={`${titleId}-why`}>
                <h3 id={`${titleId}-why`} className="text-base font-semibold">
                  Why?
                </h3>
                <ul className="mt-2 space-y-2">
                  {prediction.top_factors.map((factor) => (
                    <li key={factor.feature} className="flex items-start gap-2">
                      {factor.direction === "up" ? (
                        <ArrowUp aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-risk-high" />
                      ) : (
                        <ArrowDown aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-risk-low" />
                      )}
                      <span>
                        {factor.text}
                        <span className="sr-only">
                          {factor.direction === "up" ? " (raises the risk)" : " (lowers the risk)"}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : (
            <p>No forecast for this departure right now.</p>
          )}
        </div>

        <p className="mt-8 text-xs font-medium text-muted">{footer}</p>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS**: `npm --prefix frontend test && npm --prefix frontend run lint && npm --prefix frontend run typecheck`

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(web): risk badge, departure row, banner, probability bar and detail sheet"
```

---

### Task 14: Board page — station search, hooks, all board states

**Files:**
- Create: `frontend/src/hooks/useStations.ts`, `useDepartures.ts`, `useModel.ts`, `frontend/src/lib/recent.ts`, `frontend/src/components/StationSearch.tsx`, `frontend/src/components/DepartureBoard.tsx`, `frontend/src/pages/BoardPage.tsx`
- Test: `frontend/src/components/StationSearch.test.tsx`, `frontend/src/components/DepartureBoard.test.tsx`, `frontend/src/lib/recent.test.ts`

**Interfaces:**
- Produces: `useStations(query)`, `useDepartures(eva, hours)` (refetch 60 s), `useModel()` (no retry on 503); `readRecent()`, `rememberStation(station)`, `MAX_RECENT = 5`; `<StationSearch query onQueryChange options onSelect>`; `<DepartureBoard data isPending error hours onRetry onShowMore onOpen now?>`; `BoardPage` replaces the Task 12 placeholder.

- [ ] **Step 1: Write failing tests**

`recent.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { MAX_RECENT, readRecent, rememberStation } from "./recent";

const st = (n: number) => ({ eva: `800000${n}`, name: `Station ${n}`, state: "BY" });

describe("recent stations", () => {
  it("keeps the newest five without duplicates", () => {
    for (let i = 0; i < 7; i++) rememberStation(st(i));
    rememberStation(st(4));
    const recent = readRecent();
    expect(recent).toHaveLength(MAX_RECENT);
    expect(recent[0]?.eva).toBe("8000004");
    expect(new Set(recent.map((s) => s.eva)).size).toBe(MAX_RECENT);
  });
  it("survives blocked or corrupt storage", () => {
    localStorage.setItem("puenktlich.recentStations", "{not json");
    expect(readRecent()).toEqual([]);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(rememberStation(st(1))).toHaveLength(1);
    spy.mockRestore();
  });
});
```

`StationSearch.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Station } from "../api/types";
import { StationSearch } from "./StationSearch";

const OPTIONS: Station[] = [
  { eva: "8010101", name: "Erfurt Hbf", state: "TH" },
  { eva: "8000098", name: "Essen Hbf", state: "NW" },
];

function Harness({ onSelect }: { onSelect: (s: Station) => void }) {
  const [query, setQuery] = useState("");
  return <StationSearch query={query} onQueryChange={setQuery} options={OPTIONS} onSelect={onSelect} />;
}

describe("StationSearch", () => {
  it("is a labelled combobox", () => {
    render(<Harness onSelect={vi.fn()} />);
    const box = screen.getByRole("combobox", { name: "Station" });
    expect(box).toHaveAttribute("placeholder", "Search a station, e.g. Erfurt Hbf");
    expect(box).toHaveAttribute("aria-expanded", "false");
  });
  it("supports arrow keys and Enter", async () => {
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const box = screen.getByRole("combobox");
    await userEvent.type(box, "e");
    expect(box).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("option", { name: "Essen Hbf" })).toHaveAttribute("aria-selected", "true");
    expect(box.getAttribute("aria-activedescendant")).toBe(
      screen.getByRole("option", { name: "Essen Hbf" }).id,
    );
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(OPTIONS[1]);
  });
  it("closes on Escape", async () => {
    render(<Harness onSelect={vi.fn()} />);
    await userEvent.type(screen.getByRole("combobox"), "e");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
  it("selects with a click", async () => {
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    await userEvent.type(screen.getByRole("combobox"), "er");
    await userEvent.click(screen.getByRole("option", { name: "Erfurt Hbf" }));
    expect(onSelect).toHaveBeenCalledWith(OPTIONS[0]);
  });
});
```

`DepartureBoard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DeparturesResponse } from "../api/types";
import { makeBoard, makeDeparture } from "../test/fixtures";
import { DepartureBoard } from "./DepartureBoard";

const NOW = new Date("2026-10-05T13:57:00Z"); // 27 min after data_as_of 15:30 CEST

function show(props: Partial<Parameters<typeof DepartureBoard>[0]> & { data?: DeparturesResponse }) {
  const handlers = { onRetry: vi.fn(), onShowMore: vi.fn(), onOpen: vi.fn() };
  render(
    <DepartureBoard data={undefined} isPending={false} error={null} hours={3} now={NOW} {...handlers} {...props} />,
  );
  return handlers;
}

describe("DepartureBoard states", () => {
  it("loading shows skeleton rows", () => {
    show({ isPending: true });
    expect(screen.getByRole("status", { name: "Loading departures" })).toHaveAttribute("aria-busy", "true");
  });
  it("rows", () => {
    show({ data: makeBoard() });
    expect(screen.getByRole("list", { name: "Departures from Erfurt Hbf" }).children).toHaveLength(2);
  });
  it("empty offers six hours", async () => {
    const { onShowMore } = show({ data: makeBoard({ departures: [] }) });
    expect(screen.getByText("No departures in the next 3 hours")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show 6 hours" }));
    expect(onShowMore).toHaveBeenCalled();
  });
  it("error without data offers a retry", async () => {
    const { onRetry } = show({ error: new Error("x") });
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load departures.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });
  it("error with data keeps the last good board", () => {
    show({ error: new Error("x"), data: makeBoard() });
    expect(screen.getByRole("alert")).toHaveTextContent("Showing the last data we have.");
    expect(screen.getAllByRole("img", { name: /Delay risk/ })).toHaveLength(2);
  });
  it("stale data says how old it is", () => {
    show({ data: makeBoard({ stale: true }) });
    expect(screen.getByText("Live data is 27 min old.")).toBeInTheDocument();
  });
  it("no model shows no-forecast badges and a banner", () => {
    const departures = [makeDeparture({ prediction: null })];
    show({ data: makeBoard({ model_version: null, departures }) });
    expect(screen.getByText("Forecasts are temporarily unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "No forecast available" })).toBeInTheDocument();
  });
  it("sample data is labelled", () => {
    show({ data: makeBoard({ data_source: "sample", replayed_from: "2026-08-24" }) });
    expect(screen.getByText(/Sample data: real departures from 24 Aug 2026/)).toBeInTheDocument();
  });
});
```

Run `npm --prefix frontend test` → FAIL.

- [ ] **Step 2: Implement**

`hooks/useStations.ts`:

```ts
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function useStations(query: string) {
  return useQuery({
    queryKey: ["stations", query.trim()],
    queryFn: ({ signal }) => api.stations(query.trim(), signal),
    staleTime: 60 * 60 * 1000,
    placeholderData: keepPreviousData,
  });
}
```

`hooks/useDepartures.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export const BOARD_REFRESH_MS = 60_000;

/** Last good data stays in `data` when a refetch fails (shown with an error banner). */
export function useDepartures(eva: string | undefined, hours: number) {
  return useQuery({
    queryKey: ["departures", eva, hours],
    queryFn: ({ signal }) => api.departures(eva ?? "", hours, signal),
    enabled: eva !== undefined,
    refetchInterval: BOARD_REFRESH_MS,
    retry: 1,
  });
}
```

`hooks/useModel.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";

export function useModel() {
  return useQuery({
    queryKey: ["model"],
    queryFn: ({ signal }) => api.model(signal),
    staleTime: 5 * 60 * 1000,
    // 503 = no champion: a normal state, not worth retrying.
    retry: (count, error) => !(error instanceof ApiError && error.status === 503) && count < 2,
  });
}
```

`lib/recent.ts`:

```ts
import type { Station } from "../api/types";

const KEY = "puenktlich.recentStations";
export const MAX_RECENT = 5;

function isStation(value: unknown): value is Station {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.eva === "string" && typeof record.name === "string";
}

export function readRecent(): Station[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isStation).slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

export function rememberStation(station: Station): Station[] {
  const next = [station, ...readRecent().filter((s) => s.eva !== station.eva)].slice(0, MAX_RECENT);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage blocked: recent stations last for this page view only.
  }
  return next;
}
```

`components/StationSearch.tsx`:

```tsx
import { Search } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";
import type { Station } from "../api/types";

interface Props {
  query: string;
  onQueryChange: (query: string) => void;
  options: Station[];
  onSelect: (station: Station) => void;
}

/** WAI-ARIA combobox with a listbox popup (↑ ↓ Enter Esc). */
export function StationSearch({ query, onQueryChange, options, onSelect }: Props) {
  const inputId = useId();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const expanded = open && query.trim() !== "" && options.length > 0;

  function choose(station: Station) {
    onSelect(station);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    const count = options.length;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setOpen(true);
        setActive((i) => (count === 0 ? -1 : (i + 1) % count));
        break;
      case "ArrowUp":
        event.preventDefault();
        setOpen(true);
        setActive((i) => (count === 0 ? -1 : i <= 0 ? count - 1 : i - 1));
        break;
      case "Enter": {
        const station = options[active] ?? (count === 1 ? options[0] : undefined);
        if (expanded && station) {
          event.preventDefault();
          choose(station);
        }
        break;
      }
      case "Escape":
        setOpen(false);
        setActive(-1);
        break;
    }
  }

  return (
    <div className="relative">
      <label htmlFor={inputId} className="sr-only">
        Station
      </label>
      <div className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-surface px-3 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary">
        <Search aria-hidden="true" className="size-5 text-muted" />
        <input
          id={inputId}
          role="combobox"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={expanded && active >= 0 ? `${listId}-${active}` : undefined}
          value={query}
          placeholder="Search a station, e.g. Erfurt Hbf"
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            onQueryChange(event.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          className="h-11 w-full bg-transparent text-base outline-none placeholder:text-muted"
        />
      </div>
      {expanded ? (
        <ul
          id={listId}
          role="listbox"
          aria-label="Stations"
          className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-md border border-border bg-surface py-1 shadow-card"
        >
          {options.map((station, index) => (
            <li
              key={station.eva}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(station)}
              className={`cursor-pointer px-3 py-2.5 ${index === active ? "bg-primary-soft" : "hover:bg-surface-2"}`}
            >
              <span lang="de">{station.name}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
```

`components/DepartureBoard.tsx`:

```tsx
import { TrainFront } from "lucide-react";
import type { Departure, DeparturesResponse } from "../api/types";
import { formatDate, minutesOld } from "../lib/format";
import { Banner } from "./Banner";
import { DepartureRow } from "./DepartureRow";

interface Props {
  data: DeparturesResponse | undefined;
  isPending: boolean;
  error: Error | null;
  hours: number;
  onRetry: () => void;
  onShowMore: () => void;
  onOpen: (departure: Departure) => void;
  now?: Date;
}

const BUTTON =
  "inline-flex min-h-11 items-center rounded-md px-4 text-sm font-medium transition-colors duration-150";

function Skeleton() {
  return (
    <div role="status" aria-label="Loading departures" aria-busy="true" className="space-y-2">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-[76px] animate-pulse rounded-lg bg-surface-2" />
      ))}
    </div>
  );
}

export function DepartureBoard({ data, isPending, error, hours, onRetry, onShowMore, onOpen, now = new Date() }: Props) {
  if (isPending && !data) return <Skeleton />;
  return (
    <div className="space-y-3">
      {error ? (
        <Banner
          variant="error"
          action={
            <button type="button" onClick={onRetry} className={`${BUTTON} bg-surface text-text border border-border`}>
              Try again
            </button>
          }
        >
          {data ? "Couldn't refresh the board. Showing the last data we have." : "Couldn't load departures."}
        </Banner>
      ) : null}
      {data ? (
        <>
          {data.stale ? <Banner variant="stale">Live data is {minutesOld(data.data_as_of, now)} min old.</Banner> : null}
          {data.data_source === "sample" && data.replayed_from ? (
            <Banner variant="info">
              Sample data: real departures from {formatDate(data.replayed_from)} replayed onto today.
            </Banner>
          ) : null}
          {data.model_version === null ? (
            <Banner variant="info">Forecasts are temporarily unavailable.</Banner>
          ) : null}
          {data.departures.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-surface p-8 text-center">
              <TrainFront aria-hidden="true" className="size-8 text-muted" />
              <p>No departures in the next {hours === 1 ? "hour" : `${hours} hours`}</p>
              {hours < 6 ? (
                <button type="button" onClick={onShowMore} className={`${BUTTON} bg-primary text-primary-fg hover:bg-primary-hover`}>
                  Show 6 hours
                </button>
              ) : null}
            </div>
          ) : (
            <ul aria-label={`Departures from ${data.station.name}`} className="space-y-2">
              {data.departures.map((departure) => (
                <DepartureRow key={departure.event_id} departure={departure} onOpen={onOpen} />
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  );
}
```

`pages/BoardPage.tsx`:

```tsx
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import type { Departure, Station } from "../api/types";
import { DepartureBoard } from "../components/DepartureBoard";
import { DepartureDetail } from "../components/DepartureDetail";
import { StationSearch } from "../components/StationSearch";
import { useDepartures } from "../hooks/useDepartures";
import { useModel } from "../hooks/useModel";
import { useStations } from "../hooks/useStations";
import { formatTime } from "../lib/format";
import { readRecent, rememberStation } from "../lib/recent";

const HOURS = [1, 3, 6] as const;

export function BoardPage() {
  const { eva } = useParams();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [hours, setHours] = useState<number>(3);
  const [recent, setRecent] = useState<Station[]>(() => readRecent());
  const [selected, setSelected] = useState<Departure | null>(null);
  const stations = useStations(query);
  const board = useDepartures(eva, hours);
  const model = useModel();

  function select(station: Station) {
    setRecent(rememberStation(station));
    setQuery("");
    setSelected(null);
    navigate(`/station/${station.eva}`);
  }

  const data = board.data;
  return (
    <div className="space-y-4">
      <StationSearch query={query} onQueryChange={setQuery} options={stations.data ?? []} onSelect={select} />
      {recent.length > 0 ? (
        <ul aria-label="Recent stations" className="flex flex-wrap gap-2">
          {recent.map((station) => (
            <li key={station.eva}>
              <button
                type="button"
                onClick={() => select(station)}
                className="min-h-11 rounded-sm bg-surface-2 px-3 text-sm hover:bg-primary-soft"
              >
                <span lang="de">{station.name}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {eva === undefined ? (
        <>
          <h1 className="text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10">Will my train leave on time?</h1>
          <p className="text-muted">Pick a station to see the next departures and their chance of leaving 6+ minutes late.</p>
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10">
                <span lang="de">{data?.station.name ?? "Departures"}</span>
              </h1>
              {data ? (
                <p className="flex items-center gap-2 text-sm text-muted">
                  <span aria-hidden="true" className={`size-2 rounded-full ${data.data_source === "live" ? "bg-accent" : "bg-muted"}`} />
                  {data.data_source === "live" ? "Live" : "Sample"} · Updated {formatTime(data.data_as_of)}
                </p>
              ) : null}
            </div>
            <div role="group" aria-label="Time window" className="flex gap-1 rounded-md bg-surface-2 p-1">
              {HOURS.map((h) => (
                <button
                  key={h}
                  type="button"
                  aria-pressed={hours === h}
                  onClick={() => setHours(h)}
                  className={`min-h-11 min-w-11 rounded-sm px-3 text-sm font-medium ${hours === h ? "bg-surface text-text shadow-card" : "text-muted hover:text-text"}`}
                >
                  {h} h
                </button>
              ))}
            </div>
          </div>
          <DepartureBoard
            data={data}
            isPending={board.isPending}
            error={board.error}
            hours={hours}
            onRetry={() => void board.refetch()}
            onShowMore={() => setHours(6)}
            onOpen={setSelected}
          />
        </>
      )}

      {selected && data ? (
        <DepartureDetail
          departure={selected}
          thresholds={model.data?.risk_thresholds ?? null}
          modelVersion={data.model_version}
          trainedAt={model.data?.trained_at ?? null}
          dataAsOf={data.data_as_of}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </div>
  );
}
```

- [ ] **Step 3: Run — expect PASS**: `make web-check` (lint + typecheck + tests).

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat(web): board page with station search, hours filter and all data states"
```

---

### Task 15: Health page

**Files:**
- Create: `frontend/src/components/MetricCard.tsx`, `frontend/src/pages/HealthPage.tsx`
- Test: `frontend/src/pages/HealthPage.test.tsx`

**Interfaces:**
- Consumes: `useModel()`, `ModelInfo`, `ApiError`.
- Produces: `<MetricCard label value caption?>`; `HealthPage` replaces the Task 12 placeholder.

- [ ] **Step 1: Write the failing test** `HealthPage.test.tsx` (mock `fetch`):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MODEL } from "../test/fixtures";
import { HealthPage } from "./HealthPage";

function renderWith(response: Response) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("HealthPage", () => {
  it("shows the champion's real test metrics", async () => {
    renderWith(new Response(JSON.stringify(MODEL), { status: 200 }));
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByText("0.141")).toBeInTheDocument();
    expect(screen.getByText("Baseline 0.152 · lower is better")).toBeInTheDocument();
    expect(screen.getByText("0.808")).toBeInTheDocument();
    expect(screen.getByText(/Daily monitoring starts in a later phase/)).toBeInTheDocument();
  });
  it("explains a missing model", async () => {
    const problem = { type: "/errors/model-unavailable", title: "Forecasts are temporarily unavailable", status: 503, detail: "", instance: "/api/v1/model", request_id: "r" };
    renderWith(new Response(JSON.stringify(problem), { status: 503 }));
    expect(await screen.findByText("No forecast model is live right now.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL**.

- [ ] **Step 3: Implement**

`MetricCard.tsx`:

```tsx
export function MetricCard({ label, value, caption }: { label: string; value: string; caption?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular">{value}</p>
      {caption ? <p className="mt-1 text-sm text-muted">{caption}</p> : null}
    </div>
  );
}
```

`HealthPage.tsx`:

```tsx
import { Activity } from "lucide-react";
import { ApiError } from "../api/client";
import type { ModelInfo } from "../api/types";
import { Banner } from "../components/Banner";
import { MetricCard } from "../components/MetricCard";
import { useModel } from "../hooks/useModel";
import { formatDate } from "../lib/format";

const num = (value: number | null, digits = 3) => (value === null ? "—" : value.toFixed(digits));

function ModelCards({ model }: { model: ModelInfo }) {
  const m = model.metrics;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <MetricCard label="Model version" value={`v${model.version}`} caption={`Trained ${formatDate(model.trained_at)}`} />
      <MetricCard label="Brier score (test)" value={num(m.test_brier)} caption={`Baseline ${num(m.baseline_brier)} · lower is better`} />
      <MetricCard label="ROC-AUC (test)" value={num(m.test_auc)} caption="Higher is better" />
      <MetricCard label="PR-AUC (test)" value={num(m.test_pr_auc)} caption="Higher is better" />
      <MetricCard label="Calibration error (ECE)" value={num(m.test_ece)} caption="Lower is better" />
      <MetricCard
        label="Training data"
        value={`${formatDate(model.train_window.start)} – ${formatDate(model.train_window.end)}`}
        caption={`Snapshot ${model.data_snapshot_id}`}
      />
    </div>
  );
}

export function HealthPage() {
  const model = useModel();
  const noModel = model.error instanceof ApiError && model.error.status === 503;
  return (
    <div className="space-y-6">
      <h1 className="text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10">Model health</h1>
      {model.isPending ? (
        <div role="status" aria-label="Loading model details" aria-busy="true" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg bg-surface-2" />
          ))}
        </div>
      ) : noModel ? (
        <Banner variant="info">No forecast model is live right now.</Banner>
      ) : model.error ? (
        <Banner
          variant="error"
          action={
            <button type="button" onClick={() => void model.refetch()} className="min-h-11 rounded-md border border-border bg-surface px-4 text-sm font-medium">
              Try again
            </button>
          }
        >
          Couldn't load model details.
        </Banner>
      ) : (
        <ModelCards model={model.data} />
      )}
      <section aria-labelledby="monitoring" className="space-y-3">
        <h2 id="monitoring" className="text-xl font-semibold">
          Daily monitoring
        </h2>
        <div className="flex items-start gap-3 rounded-lg border border-border bg-surface p-4">
          <Activity aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-muted" />
          <p className="text-muted">
            Daily monitoring starts in a later phase: yesterday's accuracy and data drift will appear
            here. Until then, the numbers above come from the model's test period.
          </p>
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS**: `make web-check`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(web): model health page with real champion metrics"
```

---

### Task 16: Exit check — run the stack, accessibility review, design critique

**Files:** fixes only, wherever the reviews point.

- [ ] **Step 1: Use the `run` skill** to start everything: Docker Desktop up → `make up` → `make seed` (record output) → `make app-up` → `curl -s localhost:8000/api/v1/health`, `curl -s localhost:5173/api/v1/health` (through the Vite proxy) and `curl -s localhost:5173/` (HTML). Keep the PC awake while the stack runs.
- [ ] **Step 2: Browser check** (owner or browser tool): open `http://localhost:5173`, search "erfurt", open the board, open a departure → probability bar + 3 reasons; switch 1/3/6 h; theme toggle; 360 px width (devtools) without horizontal scroll; keyboard only (Tab, arrows, Enter, Esc).
- [ ] **Step 3: No-champion check** without touching the real pointer: `docker compose --profile app run --rm -e MODELS_BUCKET=does-not-exist -p 127.0.0.1:8001:8000 api` → `curl -s localhost:8001/api/v1/stations/8010101/departures` shows `"model_version":null` and `"prediction":null`; `curl -s -i localhost:8001/api/v1/model` → `503` problem+json. (Never move or delete `models/_pointer.json` by hand — rules §1.)
- [ ] **Step 4: Use `design:accessibility-review`** on the running UI/components (WCAG 2.1 AA, design.md §12 checklist) and **`design:design-critique`** on the board and detail screens. Fix confirmed issues with tests where behaviour changes; rerun `make web-check`.
- [ ] **Step 5: Full checks**: `make check` (lint, mypy, unit tests + coverage ≥ 80 %), `make web-check`, `make test-integration`, `make test-dags` (Airflow image unchanged, but `tracking.py` changed), `uv run pre-commit run --all-files`.
- [ ] **Step 6: Commit fixes**: `git commit -m "fix(web): accessibility and design review fixes"` (only if there were fixes).

---

### Task 17: Docs, branch record, final review

**Files:**
- Modify: `docs/architecture.md` (§3.2 board line → points to §7; §7: live board contract table, additive `data_source`/`replayed_from`/`top_factors`, error types, request id, CORS, new settings; §13 settings rows `BOARD_KEY`, `CORS_ORIGINS`; §14 layout: `serving/{model_loader,board,scoring,explain,stations,seed}.py`, `training/report.py`, `services/api/{requirements*.txt}`), `docs/phases.md` (tick Phase 5 tasks), `README.md` (Phase 5 status + how to run: `make up`, `make seed`, `make app-up`, `make api-dev`, `make web-check`), `CLAUDE.md` (Status, Phase 5 results, env lessons, session log; MLflow telemetry decided), `CHANGELOG.md` (Phase 5 entry: Built · Key decisions · Tested (commands + real results) · Known gaps · Docs touched), `Phases/README.md`
- Create: `Phases/phase-5-serving-ui.md` (goal, what was built + why, how it works with a diagram, decisions, tests with real results, how to run, known gaps, what's next)

- [ ] **Step 1: Write the docs** above with **measured numbers only** (seed counts, test counts, coverage, image size, integration run time); `TBD` for anything not measured.
- [ ] **Step 2: Final review** — `superpowers:verification-before-completion`, then the `code-reviewer` subagent (`.claude/agents/code-reviewer.md`; if not registered, a `general-purpose` agent told to follow it) over `git diff main...HEAD`; handle findings with `superpowers:receiving-code-review`; fix confirmed Critical/Important, list Minor ones in CHANGELOG "Known gaps".
- [ ] **Step 3: Branch record** — merge message in `.git/MERGE_SUMMARY.txt` (same sections as the CHANGELOG entry). No Claude attribution anywhere.
- [ ] **Step 4: Commit and push the branch** (never `main`):

```bash
git add docs README.md CLAUDE.md CHANGELOG.md Phases
git commit -m "docs(phase-5): changelog, phase doc and architecture updates for serving and UI"
git push -u origin phase-5/serving-ui
```

- [ ] **Step 5:** `superpowers:finishing-a-development-branch` — present the options to the owner (never delete the branch).

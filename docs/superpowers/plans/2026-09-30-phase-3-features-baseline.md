# Phase 3 — Features & Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single feature builder, a reproducible time-split gold snapshot of the 9 silver months, and a late-rate baseline with recorded metrics (`make baseline`).

**Architecture:** `dbdelay.features` (calendar, spec, build) turns silver rows into model inputs using a spec fitted on train only. `dbdelay.training` downloads the window's silver days, filters with DuckDB, drops flagged data gaps, splits by UTC date, writes deterministic parquet to `gold/training_sets/<id>/`, then fits and evaluates the baseline and writes three JSON files next to the snapshot.

**Tech Stack:** Python 3.12, pandas 2.3 (<3), pyarrow, DuckDB 1.5.5, pydantic v2, `holidays` (new), `scikit-learn` (new), boto3/moto, MinIO, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-phase-3-features-baseline-design.md`

## Global Constraints

- Python 3.12; `pandas>=2.2,<3`; mypy `--strict` on `src/`; ruff rules `E,F,I,B,UP,S,SIM,RUF,PL`; tests run with `-W error`.
- New dependencies: **only** `holidays>=0.105` (main deps) and `scikit-learn>=1.9.1` (new optional extra `training` + dev group). Nothing else.
- Never read or print `.env`; settings only via `dbdelay.config.get_settings()`.
- No row values in log lines or exception messages (counts, column names, dates and ids only).
- NumPy 2.5 + pandas 2.3: use `datetime.timedelta`, never `pd.Timedelta(minutes=…)`; never `pd.concat` an empty frame.
- Outputs must be deterministic: same silver → same `snapshot_id`, same bytes, same `metrics.json`.
- Unit tests `chdir` into a temp dir (`tests/unit/conftest.py`), so repo files are referenced as `REPO = Path(__file__).resolve().parents[2]`.
- Commits: conventional messages, **no** `Co-Authored-By` / "Generated with" lines; run `uv run ruff format src tests` before every commit; never commit to `main`.
- Bash prefix for every command: `export PATH="$PATH:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe/bin"; unset VIRTUAL_ENV`.

## Review Focus

1. **Frames with a non-default or duplicate index** (filtered frames, API batches): features must line up row by row and never multiply rows — pinned in Task 4 (`test_duplicate_index_is_kept_row_for_row`).
2. **Naive (tz-less) `planned_departure_utc`**: must raise `DataValidationError`, never produce silently shifted hours — pinned in Task 3 (`test_naive_timestamps_are_rejected`).
3. **`end_date` on a 31st with a shorter start month**: the window start must clamp to the month end (e.g. 2026-05-31, 3 months → 2026-03-01) — pinned in Task 5 (`test_window_start_clamps_to_month_end`).
4. **A split left empty** (e.g. rows only on train days): clear `DataValidationError` naming the split, no empty parquet written — pinned in Task 6 (`test_empty_split_is_an_error`).
5. **Baseline probabilities of exactly 0 or 1** (a group that was always late / never late): log loss must stay finite and metrics must not be NaN — pinned in Task 8 (`test_extreme_probabilities_give_finite_log_loss`).

---

## File Structure

| File | Responsibility |
|---|---|
| `configs/training.yaml` | Window, split and threshold settings (spec §4). |
| `src/dbdelay/training/__init__.py` | Package marker. |
| `src/dbdelay/training/config.py` | `TrainingConfig` + `load_training_config`. |
| `src/dbdelay/features/__init__.py` | Package marker. |
| `src/dbdelay/features/calendar.py` | Berlin local-time parts; public holidays. |
| `src/dbdelay/features/spec.py` | Feature constants, `FeatureSpec`, `fit_spec`, input checks, category keys. |
| `src/dbdelay/features/build.py` | `build_features` (the only feature maker). |
| `src/dbdelay/training/split.py` | Windows, gap masks, split assignment, `build_snapshot`, `load_snapshot_split`. |
| `src/dbdelay/training/baseline.py` | `BaselineModel` (fit / predict / JSON). |
| `src/dbdelay/training/evaluate.py` | Metrics, calibration bins, slices, report models. |
| `src/dbdelay/training/run_baseline.py` | `run_baseline` + CLI `main`. |
| `tests/builders.py` | + `silver_day`, `quality_report`, `put_march_silver`, `MARCH_CONFIG`. |
| `tests/unit/conftest.py` | + `s3_store` moto fixture. |
| `tests/unit/test_training_config.py`, `test_calendar.py`, `test_feature_spec.py`, `test_build_features.py`, `test_split_windows.py`, `test_snapshot.py`, `test_baseline.py`, `test_evaluate.py`, `test_run_baseline.py` | Unit tests. |
| `tests/integration/test_snapshot_minio.py` | Snapshot twice + end-to-end on MinIO. |
| `notebooks/02_baseline.ipynb` | Metrics table + calibration plot (real run). |
| `Makefile`, `pyproject.toml`, `README.md`, `docs/*`, `CHANGELOG.md`, `Phases/*`, `CLAUDE.md` | Wiring and docs. |

---

### Task 1: Dependencies and training config

**Files:**
- Modify: `pyproject.toml` (via `uv add`), add mypy override for sklearn
- Create: `configs/training.yaml`, `src/dbdelay/training/__init__.py`, `src/dbdelay/training/config.py`
- Test: `tests/unit/test_training_config.py`

**Interfaces:**
- Consumes: `dbdelay.errors.ConfigError`
- Produces: `TrainingConfig` (fields `window_months: int`, `end_date: date | None`, `test_days: int`, `valid_days: int`, `exclude_data_gaps: bool`, `features.min_count: int`, `baseline.min_count: int`, `evaluation.ece_bins: int`, `evaluation.slice_min_rows: int`); `FeatureConfig`, `BaselineConfig`, `EvaluationConfig`; `load_training_config(path: Path) -> TrainingConfig`.

- [ ] **Step 1: Add the two approved dependencies**

```bash
uv add "holidays>=0.105"
uv add --optional training "scikit-learn>=1.9.1"
uv add --dev "scikit-learn>=1.9.1"
```
Then append to `pyproject.toml` after the pyarrow mypy override:
```toml
[[tool.mypy.overrides]]
# scikit-learn ships no type information (stubs would be a new dependency)
module = ["sklearn", "sklearn.*"]
ignore_missing_imports = true
```
Run: `uv run python -c "import holidays, sklearn; print('ok')"`
Expected: `ok`

- [ ] **Step 2: Write the failing tests** — `tests/unit/test_training_config.py`

```python
from datetime import date
from pathlib import Path

import pytest

from dbdelay.errors import ConfigError
from dbdelay.training.config import load_training_config

REPO = Path(__file__).resolve().parents[2]

VALID = """
window_months: 9
end_date: null
test_days: 14
valid_days: 14
exclude_data_gaps: true
features: {min_count: 200}
baseline: {min_count: 50}
evaluation: {ece_bins: 10, slice_min_rows: 500}
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "training.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_config_loads_with_spec_defaults() -> None:
    cfg = load_training_config(REPO / "configs" / "training.yaml")
    assert cfg.window_months == 9
    assert cfg.end_date is None
    assert (cfg.test_days, cfg.valid_days) == (14, 14)
    assert cfg.exclude_data_gaps is True
    assert cfg.features.min_count == 200
    assert cfg.baseline.min_count == 50
    assert (cfg.evaluation.ece_bins, cfg.evaluation.slice_min_rows) == (10, 500)


def test_end_date_is_parsed(tmp_path: Path) -> None:
    cfg = load_training_config(_write(tmp_path, VALID.replace("null", "2026-08-31")))
    assert cfg.end_date == date(2026, 8, 31)


@pytest.mark.parametrize(
    "broken",
    [
        VALID.replace("test_days: 14", "test_days: 0"),
        VALID.replace("ece_bins: 10", "ece_bins: 1"),
        VALID + "surprise: 1\n",
        VALID.replace("window_months: 9\n", ""),
        "window_months: [unclosed",
    ],
)
def test_invalid_config_raises_config_error(tmp_path: Path, broken: str) -> None:
    with pytest.raises(ConfigError):
        load_training_config(_write(tmp_path, broken))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_training_config(tmp_path / "nope.yaml")
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/unit/test_training_config.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.training'`

- [ ] **Step 4: Implement** — `configs/training.yaml`

```yaml
# Phase 3 training settings (docs/superpowers/specs/2026-09-30-phase-3-features-baseline-design.md §4).
window_months: 9          # architecture §5.3
end_date: null            # null = latest silver day present; recorded in snapshot.json
test_days: 14
valid_days: 14
exclude_data_gaps: true   # drop rows in flagged low-volume hours / station-days (owner decision)
features:
  min_count: 200          # category levels with fewer train rows → OTHER (initial default, not tuned)
baseline:
  min_count: 50           # a lookup group needs this many train rows, else back off (initial default)
evaluation:
  ece_bins: 10
  slice_min_rows: 500     # smaller slices report null metrics
```

`src/dbdelay/training/__init__.py`:
```python
"""Training: snapshot, baseline, evaluation (Phase 3); model training arrives in Phase 4."""
```

`src/dbdelay/training/config.py`:
```python
"""Training configuration (`configs/training.yaml`), validated with pydantic."""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError

from dbdelay.errors import ConfigError


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeatureConfig(_Section):
    min_count: PositiveInt


class BaselineConfig(_Section):
    min_count: PositiveInt


class EvaluationConfig(_Section):
    ece_bins: int = Field(ge=2)
    slice_min_rows: PositiveInt


class TrainingConfig(_Section):
    window_months: PositiveInt
    end_date: date | None = None
    test_days: PositiveInt
    valid_days: PositiveInt
    exclude_data_gaps: bool
    features: FeatureConfig
    baseline: BaselineConfig
    evaluation: EvaluationConfig


def load_training_config(path: Path) -> TrainingConfig:
    """Load and validate the training config.

    Raises:
        ConfigError: if the file is missing, not YAML, or fails validation.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read training config {path.name}") from exc
    try:
        return TrainingConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid training config {path.name}: {exc.error_count()} errors"
        ) from None
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_training_config.py -q -W error`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests
git add pyproject.toml uv.lock configs/training.yaml src/dbdelay/training tests/unit/test_training_config.py
git commit -m "feat(training): training config and holidays/scikit-learn dependencies"
```

---

### Task 2: Calendar features

**Files:**
- Create: `src/dbdelay/features/__init__.py`, `src/dbdelay/features/calendar.py`
- Test: `tests/unit/test_calendar.py`

**Interfaces:**
- Consumes: `holidays.country_holidays`
- Produces: `BERLIN = "Europe/Berlin"`; `local_parts(planned_utc: pd.Series) -> pd.DataFrame` with columns `hour_local:int8, minute_of_day:int16, weekday:int8, is_weekend:bool, month:int8` and the input index; `is_public_holiday(planned_utc: pd.Series, states: pd.Series) -> pd.Series[bool]` (input index).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_calendar.py`

```python
import pandas as pd

from dbdelay.features.calendar import is_public_holiday, local_parts


def _utc(*stamps: str) -> pd.Series:
    return pd.Series(pd.to_datetime(list(stamps), utc=True))


def test_local_parts_in_winter_and_summer() -> None:
    parts = local_parts(_utc("2026-03-10 07:15", "2026-07-04 10:00"))
    assert parts["hour_local"].tolist() == [8, 12]
    assert parts["minute_of_day"].tolist() == [8 * 60 + 15, 12 * 60]
    assert parts["weekday"].tolist() == [1, 5]
    assert parts["is_weekend"].tolist() == [False, True]
    assert parts["month"].tolist() == [3, 7]
    assert parts.dtypes.astype(str).to_dict() == {
        "hour_local": "int8",
        "minute_of_day": "int16",
        "weekday": "int8",
        "is_weekend": "bool",
        "month": "int8",
    }


def test_spring_forward_skips_the_two_oclock_hour() -> None:
    assert local_parts(_utc("2026-03-29 00:30", "2026-03-29 01:30"))["hour_local"].tolist() == [1, 3]


def test_autumn_back_repeats_the_two_oclock_hour() -> None:
    parts = local_parts(_utc("2025-10-26 00:30", "2025-10-26 01:30"))
    assert parts["hour_local"].tolist() == [2, 2]
    assert parts["minute_of_day"].tolist() == [150, 150]


def test_local_date_can_differ_from_utc_date() -> None:
    parts = local_parts(_utc("2026-02-28 23:30"))
    assert (parts["month"].iloc[0], parts["weekday"].iloc[0]) == (3, 6)


def test_state_holiday_only_counts_for_that_state() -> None:
    planned = _utc("2026-01-06 10:00", "2026-01-06 10:00", "2025-12-25 10:00", "2026-03-10 10:00")
    states = pd.Series(["BW", "NW", "NW", "BW"])
    assert is_public_holiday(planned, states).tolist() == [True, False, True, False]


def test_holiday_uses_the_berlin_local_date() -> None:
    assert is_public_holiday(_utc("2026-01-05 23:30"), pd.Series(["BW"])).tolist() == [True]


def test_unknown_or_missing_state_uses_national_holidays() -> None:
    planned = _utc("2025-12-25 10:00", "2026-01-06 10:00", "2026-01-06 10:00")
    states = pd.Series(["XX", None, "XX"])
    assert is_public_holiday(planned, states).tolist() == [True, False, False]


def test_empty_input_gives_empty_output() -> None:
    empty = pd.Series(pd.to_datetime([], utc=True))
    assert local_parts(empty).empty
    assert is_public_holiday(empty, pd.Series([], dtype=object)).empty
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_calendar.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.features'`

- [ ] **Step 3: Implement**

`src/dbdelay/features/__init__.py`:
```python
"""Feature building shared by training, the API and monitoring (architecture §4)."""
```

`src/dbdelay/features/calendar.py`:
```python
"""Berlin local-time parts and German public holidays for planned departures."""

from functools import lru_cache

import holidays
import pandas as pd

BERLIN = "Europe/Berlin"
_SATURDAY = 5
_NATIONAL = ""


def local_parts(planned_utc: pd.Series) -> pd.DataFrame:
    """Hour, minute of day, weekday (0 = Monday), weekend flag and month in Europe/Berlin."""
    local = planned_utc.dt.tz_convert(BERLIN)
    hour = local.dt.hour
    weekday = local.dt.weekday
    return pd.DataFrame(
        {
            "hour_local": hour.astype("int8"),
            "minute_of_day": (hour * 60 + local.dt.minute).astype("int16"),
            "weekday": weekday.astype("int8"),
            "is_weekend": (weekday >= _SATURDAY).astype("bool"),
            "month": local.dt.month.astype("int8"),
        },
        index=planned_utc.index,
    )


@lru_cache(maxsize=64)
def _holiday_days(state: str, years: tuple[int, ...]) -> pd.DatetimeIndex:
    subdivisions = holidays.country_holidays("DE").subdivisions
    subdiv = state if state in subdivisions else None
    calendar = holidays.country_holidays("DE", subdiv=subdiv, years=years)
    return pd.DatetimeIndex(sorted(calendar.keys()))


def is_public_holiday(planned_utc: pd.Series, states: pd.Series) -> "pd.Series[bool]":
    """True on a national holiday or a holiday of the row's state (Berlin local date).

    ``states`` holds German state codes (e.g. ``"BW"``); unknown or missing codes use the
    national calendar only.
    """
    local_day = planned_utc.dt.tz_convert(BERLIN).dt.tz_localize(None).dt.normalize()
    years = tuple(sorted({int(year) for year in local_day.dt.year.unique()}))
    keys = states.fillna(_NATIONAL).astype(str).to_numpy()
    result = pd.Series(False, index=planned_utc.index, dtype="bool")
    for state in sorted(set(keys.tolist())):
        mask = keys == state
        result[mask] = local_day[mask].isin(_holiday_days(state, years)).to_numpy()
    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_calendar.py -q -W error`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/features tests/unit/test_calendar.py
git commit -m "feat(features): Berlin local-time parts and state public holidays"
```

---

### Task 3: Feature spec

**Files:**
- Create: `src/dbdelay/features/spec.py`
- Test: `tests/unit/test_feature_spec.py`

**Interfaces:**
- Consumes: `dbdelay.data.stations.Station`, `dbdelay.errors.DataValidationError`
- Produces: constants `FEATURE_VERSION=1`, `OTHER="OTHER"`, `REQUIRED_COLUMNS`, `FORBIDDEN_COLUMNS`, `CATEGORICAL_FEATURES`, `FEATURE_COLUMNS`; `require_columns(df) -> None`; `category_keys(df) -> pd.DataFrame` (columns `eva, train_type, line_key, destination_key`, str values, input index); `FeatureSpec` (`min_count: int`, `levels: dict[str, tuple[str, ...]]`, `station_states: dict[str, str]`, `.to_json() -> str`, `.spec_hash -> str`, `FeatureSpec.from_json(text) -> FeatureSpec`); `fit_spec(train: pd.DataFrame, stations: Sequence[Station], min_count: int) -> FeatureSpec`.

Note: spec §6 lists `train_number` among the inputs, but no v1 feature uses it, so it is not required.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_feature_spec.py`

```python
import pandas as pd
import pytest

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError
from dbdelay.features.spec import (
    FEATURE_COLUMNS,
    FORBIDDEN_COLUMNS,
    OTHER,
    FeatureSpec,
    category_keys,
    fit_spec,
    require_columns,
)
from tests.builders import silver_frame

STATIONS = [
    Station(eva="8000105", name="Frankfurt (Main) Hbf", state="HE"),
    Station(eva="8000096", name="Stuttgart Hbf", state="BW"),
]


def _train() -> pd.DataFrame:
    frame = silver_frame(4)
    frame["train_type"] = ["RB", "RB", "RB", "ICE"]
    frame["line_number"] = ["58", "58", None, None]
    return frame


def test_constants_do_not_overlap() -> None:
    assert not set(FEATURE_COLUMNS) & set(FORBIDDEN_COLUMNS)


def test_category_keys_build_line_and_destination_keys() -> None:
    keys = category_keys(_train())
    assert keys["line_key"].tolist() == ["RB:58", "RB:58", "RB:none", "ICE:none"]
    assert keys["destination_key"].tolist() == ["Dieburg"] * 4


def test_rare_levels_become_other_and_other_is_last() -> None:
    spec = fit_spec(_train(), STATIONS, min_count=2)
    assert spec.levels["train_type"] == ("RB", OTHER)
    assert spec.levels["line_key"] == ("RB:58", OTHER)
    assert spec.levels["eva"] == ("8000105", OTHER)
    assert spec.station_states == {"8000105": "HE", "8000096": "BW"}


def test_json_round_trip_and_stable_hash() -> None:
    spec = fit_spec(_train(), STATIONS, min_count=1)
    again = FeatureSpec.from_json(spec.to_json())
    assert again == spec
    assert again.spec_hash == spec.spec_hash
    assert len(spec.spec_hash) == 64


def test_bad_spec_json_raises() -> None:
    with pytest.raises(DataValidationError):
        FeatureSpec.from_json('{"min_count": "many"}')


def test_missing_column_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="final_destination"):
        require_columns(_train().drop(columns=["final_destination"]))


def test_naive_timestamps_are_rejected() -> None:
    frame = _train()
    frame["planned_departure_utc"] = frame["planned_departure_utc"].dt.tz_localize(None)
    with pytest.raises(DataValidationError, match="timezone"):
        require_columns(frame)


def test_empty_train_cannot_be_fitted() -> None:
    with pytest.raises(DataValidationError):
        fit_spec(_train().iloc[0:0], STATIONS, min_count=1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_feature_spec.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.features.spec'`

- [ ] **Step 3: Implement** — `src/dbdelay/features/spec.py`

```python
"""Feature spec: frozen category levels and station states, fitted on the train split only."""

import hashlib
from collections.abc import Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError

FEATURE_VERSION = 1
OTHER = "OTHER"
_MISSING = "none"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_type",
    "line_number",
    "final_destination",
    "stop_index",
    "planned_departure_utc",
)
# Outcome and bookkeeping columns: build_features never reads them (ADR 0001 leakage policy).
FORBIDDEN_COLUMNS: tuple[str, ...] = (
    "changed_departure_utc",
    "delay_min",
    "is_cancelled",
    "is_late",
    "event_id",
    "ride_id",
    "ingested_at",
    "source",
)
CATEGORICAL_FEATURES: tuple[str, ...] = ("eva", "train_type", "line_key", "destination_key")
FEATURE_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_type",
    "line_key",
    "destination_key",
    "stop_index",
    "hour_local",
    "minute_of_day",
    "weekday",
    "is_weekend",
    "month",
    "is_public_holiday",
)


class FeatureSpec(BaseModel):
    """Everything `build_features` needs besides the rows (saved as `feature_spec.json`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = FEATURE_VERSION
    features: tuple[str, ...] = FEATURE_COLUMNS
    min_count: int
    levels: dict[str, tuple[str, ...]]
    station_states: dict[str, str]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, text: str | bytes) -> "FeatureSpec":
        """Parse a saved spec.

        Raises:
            DataValidationError: if the JSON does not match the spec model.
        """
        try:
            return cls.model_validate_json(text)
        except ValidationError as exc:
            raise DataValidationError(f"invalid feature spec: {exc.error_count()} errors") from None


def require_columns(df: pd.DataFrame) -> None:
    """Raise ``DataValidationError`` if an input column is missing or times are naive."""
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise DataValidationError(f"feature input is missing columns: {missing}")
    if not isinstance(df["planned_departure_utc"].dtype, pd.DatetimeTZDtype):
        raise DataValidationError("planned_departure_utc must be timezone-aware (UTC)")


def category_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Raw string keys of the categorical features (before rare/unseen → OTHER)."""
    train_type = df["train_type"].fillna(_MISSING).astype(str)
    line = df["line_number"].fillna(_MISSING).astype(str)
    return pd.DataFrame(
        {
            "eva": df["eva"].fillna(_MISSING).astype(str),
            "train_type": train_type,
            "line_key": train_type + ":" + line,
            "destination_key": df["final_destination"].fillna(_MISSING).astype(str),
        },
        index=df.index,
    )


def fit_spec(train: pd.DataFrame, stations: Sequence[Station], min_count: int) -> FeatureSpec:
    """Freeze category levels (≥ ``min_count`` train rows, sorted, ``OTHER`` last).

    Raises:
        DataValidationError: if ``train`` is empty or misses input columns.
    """
    require_columns(train)
    if train.empty:
        raise DataValidationError("cannot fit a feature spec on an empty train split")
    keys = category_keys(train)
    levels: dict[str, tuple[str, ...]] = {}
    for column in CATEGORICAL_FEATURES:
        counts = keys[column].value_counts()
        kept = sorted(str(level) for level, n in counts.items() if n >= min_count and level != OTHER)
        levels[column] = (*kept, OTHER)
    return FeatureSpec(
        min_count=min_count,
        levels=levels,
        station_states={station.eva: station.state for station in stations},
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_feature_spec.py -q -W error`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/features/spec.py tests/unit/test_feature_spec.py
git commit -m "feat(features): FeatureSpec fitted on train with rare levels mapped to OTHER"
```

---

### Task 4: `build_features` and the leakage guard

**Files:**
- Create: `src/dbdelay/features/build.py`
- Test: `tests/unit/test_build_features.py`

**Interfaces:**
- Consumes: `local_parts`, `is_public_holiday` (Task 2); `FeatureSpec`, `require_columns`, `category_keys`, `CATEGORICAL_FEATURES`, `FEATURE_COLUMNS`, `OTHER` (Task 3)
- Produces: `build_features(df: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame` — columns exactly `FEATURE_COLUMNS`, dtypes `category ×4, int16, int8, int16, int8, bool, int8, bool`, index of `df`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_build_features.py`

```python
import pandas as pd
import pytest

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS, FORBIDDEN_COLUMNS, OTHER, fit_spec
from tests.builders import silver_frame

STATIONS = [Station(eva="8000105", name="Frankfurt (Main) Hbf", state="BW")]


def _frame() -> pd.DataFrame:
    return silver_frame(3)


def test_columns_order_and_dtypes() -> None:
    frame = _frame()
    features = build_features(frame, fit_spec(frame, STATIONS, min_count=1))
    assert tuple(features.columns) == FEATURE_COLUMNS
    assert features.dtypes.astype(str).tolist() == [
        "category",
        "category",
        "category",
        "category",
        "int16",
        "int8",
        "int16",
        "int8",
        "bool",
        "int8",
        "bool",
    ]


def test_unseen_level_maps_to_other() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    frame.loc[0, "train_type"] = "ICE"
    features = build_features(frame, spec)
    assert features["train_type"].tolist() == [OTHER, "RB", "RB"]
    assert list(features["train_type"].cat.categories) == list(spec.levels["train_type"])


def test_holiday_uses_the_spec_station_state() -> None:
    frame = _frame()
    frame["planned_departure_utc"] = pd.Series(
        pd.to_datetime(["2026-01-06 10:00"] * 3, utc=True), dtype="datetime64[us, UTC]"
    )
    features = build_features(frame, fit_spec(frame, STATIONS, min_count=1))
    assert features["is_public_holiday"].tolist() == [True, True, True]


def test_deterministic() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    pd.testing.assert_frame_equal(build_features(frame, spec), build_features(frame, spec))


def test_duplicate_index_is_kept_row_for_row() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    frame.index = pd.Index([7, 7, 3])
    frame.loc[3, "stop_index"] = 9
    features = build_features(frame, spec)
    assert len(features) == 3
    assert features.index.tolist() == [7, 7, 3]
    assert features["stop_index"].tolist() == [1, 1, 9]


def test_missing_input_column_raises() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    with pytest.raises(DataValidationError):
        build_features(frame.drop(columns=["stop_index"]), spec)


def test_leakage_guard_output_has_no_forbidden_columns() -> None:
    frame = _frame()
    features = build_features(frame, fit_spec(frame, STATIONS, min_count=1))
    assert not set(features.columns) & set(FORBIDDEN_COLUMNS)


def test_leakage_guard_forbidden_columns_cannot_change_features() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    expected = build_features(frame, spec)

    scrambled = frame.copy()
    scrambled["changed_departure_utc"] = scrambled["planned_departure_utc"] + pd.to_timedelta(
        [90, 5, 0], unit="m"
    )
    scrambled["delay_min"] = pd.Series([90, 5, 0], dtype="Int16")
    scrambled["is_late"] = pd.Series([True, False, True], dtype="boolean")
    scrambled["is_cancelled"] = [True, True, False]
    scrambled["event_id"] = ["x", "y", "z"]
    scrambled["ride_id"] = ["r", "s", "t"]
    scrambled["ingested_at"] = scrambled["ingested_at"].iloc[::-1].to_numpy()
    scrambled["source"] = "live"
    pd.testing.assert_frame_equal(build_features(scrambled, spec), expected)
    pd.testing.assert_frame_equal(
        build_features(frame.drop(columns=list(FORBIDDEN_COLUMNS)), spec), expected
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_build_features.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.features.build'`

- [ ] **Step 3: Implement** — `src/dbdelay/features/build.py`

```python
"""`build_features`: the single place features are made (training, API, monitoring)."""

import pandas as pd

from dbdelay.features.calendar import is_public_holiday, local_parts
from dbdelay.features.spec import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    OTHER,
    FeatureSpec,
    category_keys,
    require_columns,
)


def build_features(df: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Model inputs for ``df`` in ``FEATURE_COLUMNS`` order, keeping ``df``'s index.

    Reads only ``REQUIRED_COLUMNS`` — never labels or outcome columns (leakage guard).
    Unseen category levels map to ``OTHER``. Values are assigned positionally, so
    duplicate or non-default indexes stay row-for-row.

    Raises:
        DataValidationError: if an input column is missing or times are not tz-aware.
    """
    require_columns(df)
    keys = category_keys(df)
    out = pd.DataFrame(index=df.index)
    for column in CATEGORICAL_FEATURES:
        levels = list(spec.levels[column])
        raw = keys[column].to_numpy()
        values = pd.Series(raw).where(pd.Series(raw).isin(levels), OTHER).to_numpy()
        out[column] = pd.Categorical(values, categories=levels)
    out["stop_index"] = df["stop_index"].to_numpy().astype("int16")
    planned = df["planned_departure_utc"]
    parts = local_parts(planned)
    for column in parts.columns:
        out[column] = parts[column].to_numpy()
    states = df["eva"].astype(str).map(spec.station_states)
    out["is_public_holiday"] = is_public_holiday(planned, states).to_numpy()
    return out[list(FEATURE_COLUMNS)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_build_features.py tests/unit/test_feature_spec.py tests/unit/test_calendar.py -q -W error`
Expected: PASS (24 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/features/build.py tests/unit/test_build_features.py
git commit -m "feat(features): build_features with leakage guard tests"
```

---

### Task 5: Windows, gap masks and split assignment

**Files:**
- Create: `src/dbdelay/training/split.py` (pure part)
- Test: `tests/unit/test_split_windows.py`
- Modify: `tests/builders.py` (add `quality_report`)

**Interfaces:**
- Consumes: `TrainingConfig` (Task 1); `QualityReport`, `StationQuality` from `dbdelay.data.quality`; `BERLIN` (Task 2)
- Produces: `Windows` (`start, valid_start, test_start, end: date`; `.split_ranges() -> dict[str, tuple[date, date]]`; `.days() -> list[date]`; `.months() -> list[str]`); `compute_windows(end: date, cfg: TrainingConfig) -> Windows`; `gap_masks(df, reports) -> tuple[pd.Series, pd.Series]` (bool, index of df); `assign_split(planned_utc: pd.Series, windows: Windows) -> pd.Series` (values `train|valid|test`); `SPLITS = ("train", "valid", "test")`.

- [ ] **Step 1: Add the report builder** — append to `tests/builders.py` (add imports at the top: `from collections.abc import Mapping, Sequence` and `from dbdelay.data.quality import QualityReport, StationQuality`)

```python
def quality_report(
    month: str,
    *,
    low_volume_hours: Sequence[str] = (),
    drop_days: Mapping[str, Sequence[str]] | None = None,
) -> QualityReport:
    """A minimal monthly quality report with the given gap flags."""
    stations = [
        StationQuality(eva=eva, rows=0, volume_drop_days=list(days))
        for eva, days in (drop_days or {}).items()
    ]
    return QualityReport(
        month=month,
        rows_read=0,
        rows_out=0,
        drops={},
        quarantined={},
        null_rates={},
        late_rate=None,
        cancelled_rate=None,
        stations=stations,
        low_volume_hours=list(low_volume_hours),
        edge_complete={"prev": True, "next": True},
        content_hash="0" * 64,
    )
```

- [ ] **Step 2: Write the failing tests** — `tests/unit/test_split_windows.py`

```python
from datetime import date

import pandas as pd
import pytest

from dbdelay.errors import DataValidationError
from dbdelay.training.config import (
    BaselineConfig,
    EvaluationConfig,
    FeatureConfig,
    TrainingConfig,
)
from dbdelay.training.split import assign_split, compute_windows, gap_masks
from tests.builders import quality_report


def _cfg(window_months: int = 9, test_days: int = 14, valid_days: int = 14) -> TrainingConfig:
    return TrainingConfig(
        window_months=window_months,
        test_days=test_days,
        valid_days=valid_days,
        exclude_data_gaps=True,
        features=FeatureConfig(min_count=1),
        baseline=BaselineConfig(min_count=1),
        evaluation=EvaluationConfig(ece_bins=10, slice_min_rows=1),
    )


def test_default_window_matches_the_spec() -> None:
    windows = compute_windows(date(2026, 8, 31), _cfg())
    assert windows.start == date(2025, 12, 1)
    assert windows.valid_start == date(2026, 8, 4)
    assert windows.test_start == date(2026, 8, 18)
    assert windows.split_ranges() == {
        "train": (date(2025, 12, 1), date(2026, 8, 3)),
        "valid": (date(2026, 8, 4), date(2026, 8, 17)),
        "test": (date(2026, 8, 18), date(2026, 8, 31)),
    }
    assert len(windows.days()) == 274
    assert windows.months() == [
        "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08",
    ]  # fmt: skip


def test_window_start_clamps_to_month_end() -> None:
    assert compute_windows(date(2026, 5, 31), _cfg(window_months=3)).start == date(2026, 3, 1)
    assert compute_windows(date(2026, 3, 31), _cfg(window_months=1)).start == date(2026, 3, 1)


def test_window_too_short_for_valid_and_test_raises() -> None:
    with pytest.raises(DataValidationError, match="too short"):
        compute_windows(date(2026, 3, 31), _cfg(window_months=1, test_days=20, valid_days=11))


def test_assign_split_boundaries_are_utc_dates() -> None:
    windows = compute_windows(date(2026, 8, 31), _cfg())
    planned = pd.Series(
        pd.to_datetime(
            ["2026-08-03 23:59", "2026-08-04 00:00", "2026-08-17 23:59", "2026-08-18 00:00"],
            utc=True,
        )
    )
    assert assign_split(planned, windows).tolist() == ["train", "valid", "valid", "test"]


def test_gap_masks_match_local_hours_and_station_days() -> None:
    frame = pd.DataFrame(
        {
            "eva": ["8000105", "8000105", "8000105", "8000096"],
            "planned_departure_utc": pd.to_datetime(
                ["2026-03-16 17:30", "2026-03-16 16:30", "2026-03-23 10:00", "2026-03-23 10:00"],
                utc=True,
            ),
        }
    )
    report = quality_report(
        "2026-03", low_volume_hours=["2026-03-16T18"], drop_days={"8000105": ["2026-03-23"]}
    )
    in_hour, in_day = gap_masks(frame, [report])
    assert in_hour.tolist() == [True, False, False, False]
    assert in_day.tolist() == [False, False, True, False]


def test_gap_masks_without_flags_are_all_false() -> None:
    frame = pd.DataFrame(
        {"eva": ["8000105"], "planned_departure_utc": pd.to_datetime(["2026-03-16 17:30"], utc=True)}
    )
    in_hour, in_day = gap_masks(frame, [quality_report("2026-03")])
    assert not in_hour.any()
    assert not in_day.any()
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/unit/test_split_windows.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.training.split'`

- [ ] **Step 4: Implement** — `src/dbdelay/training/split.py` (first part; Task 6 appends to this file)

```python
"""Time-based train/valid/test snapshot of labelled silver rows (architecture §3.4, §5.3)."""

from calendar import monthrange
from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from dbdelay.data.quality import QualityReport
from dbdelay.errors import DataValidationError
from dbdelay.features.calendar import BERLIN
from dbdelay.training.config import TrainingConfig

SPLITS: tuple[str, ...] = ("train", "valid", "test")


class Windows(BaseModel):
    """Inclusive UTC-date bounds of the snapshot window and its splits."""

    model_config = ConfigDict(frozen=True)

    start: date
    valid_start: date
    test_start: date
    end: date

    def split_ranges(self) -> dict[str, tuple[date, date]]:
        one = timedelta(days=1)
        return {
            "train": (self.start, self.valid_start - one),
            "valid": (self.valid_start, self.test_start - one),
            "test": (self.test_start, self.end),
        }

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def months(self) -> list[str]:
        return sorted({f"{day:%Y-%m}" for day in self.days()})


def _minus_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def compute_windows(end: date, cfg: TrainingConfig) -> Windows:
    """Window = the ``window_months`` ending on ``end``; test/valid are its last days.

    Raises:
        DataValidationError: if no train day would be left.
    """
    start = _minus_months(end, cfg.window_months) + timedelta(days=1)
    test_start = end - timedelta(days=cfg.test_days - 1)
    valid_start = test_start - timedelta(days=cfg.valid_days)
    if valid_start <= start:
        raise DataValidationError("window is too short for the valid and test splits")
    return Windows(start=start, valid_start=valid_start, test_start=test_start, end=end)


def gap_masks(
    df: pd.DataFrame, reports: Sequence[QualityReport]
) -> tuple["pd.Series[bool]", "pd.Series[bool]"]:
    """Rows in a flagged low-volume local hour, and rows on a station's flagged UTC day."""
    planned = df["planned_departure_utc"]
    gap_hours = pd.DatetimeIndex(
        [pd.Timestamp(f"{hour}:00").tz_localize(BERLIN) for r in reports for hour in r.low_volume_hours],
        tz=BERLIN,
    )
    # Berlin offsets are whole hours, so flooring in UTC equals flooring in local time.
    local_hour = planned.dt.floor("h").dt.tz_convert(BERLIN)
    in_hour = local_hour.isin(gap_hours).to_numpy()

    drop_days: dict[str, set[str]] = {}
    for report in reports:
        for station in report.stations:
            drop_days.setdefault(station.eva, set()).update(station.volume_drop_days)
    utc_day = planned.dt.floor("D")
    evas = df["eva"].astype(str).to_numpy()
    in_day = np.zeros(len(df), dtype=bool)
    for eva, days in sorted(drop_days.items()):
        flagged = pd.DatetimeIndex(sorted(days)).tz_localize("UTC")
        in_day |= (evas == eva) & utc_day.isin(flagged).to_numpy()
    return (
        pd.Series(in_hour, index=df.index, dtype="bool"),
        pd.Series(in_day, index=df.index, dtype="bool"),
    )


def assign_split(planned_utc: pd.Series, windows: Windows) -> pd.Series:
    """``train`` / ``valid`` / ``test`` by the UTC date of the planned departure."""
    day = planned_utc.dt.floor("D")
    valid_start = pd.Timestamp(windows.valid_start.isoformat(), tz="UTC")
    test_start = pd.Timestamp(windows.test_start.isoformat(), tz="UTC")
    labels = np.where(day < valid_start, "train", np.where(day < test_start, "valid", "test"))
    return pd.Series(labels, index=planned_utc.index)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_split_windows.py -q -W error`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/training/split.py tests/unit/test_split_windows.py tests/builders.py
git commit -m "feat(training): time windows, data-gap masks and split assignment"
```

---

### Task 6: `build_snapshot` to gold

**Files:**
- Modify: `src/dbdelay/training/split.py` (append), `tests/builders.py` (add `silver_day`, `put_march_silver`, `MARCH_CONFIG`), `tests/unit/conftest.py` (add `s3_store`)
- Test: `tests/unit/test_snapshot.py`

**Interfaces:**
- Consumes: `ObjectStore.download_file/upload_file/put_bytes/get_bytes/exists/iter_keys`; `silver_key`, `quality_key`, `to_parquet_bytes`, `SILVER_ARROW_SCHEMA`, `SILVER_COLUMNS`, `write_silver_month`; Task 5 functions
- Produces: `SplitInfo`, `SnapshotManifest` (fields `snapshot_id, content_hash, window_start, window_end, splits: dict[str, SplitInfo], excluded: dict[str, int], silver_days: int, quality_months: list[str], config: dict[str, Any]`); `snapshot_prefix(snapshot_id: str, root: str = "") -> str`; `latest_silver_day(store, root="") -> date`; `build_snapshot(store, cfg, workdir: Path, *, root: str = "") -> SnapshotManifest`; `load_snapshot_split(store, snapshot_id: str, split: str, workdir: Path, *, root: str = "") -> pd.DataFrame`. Test helpers `silver_day(...)`, `put_march_silver(store, root="", *, with_report=True)`, `MARCH_CONFIG`.

- [ ] **Step 1: Add test helpers**

Append to `tests/unit/conftest.py` (add imports `from moto import mock_aws` and `from dbdelay.storage import ObjectStore, make_s3_client`):
```python
TEST_BUCKET = "puenktlich-test"


@pytest.fixture
def s3_store() -> Iterator[ObjectStore]:
    """An empty moto-backed bucket."""
    with mock_aws():
        client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=TEST_BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, TEST_BUCKET)
```

Append to `tests/builders.py` (add imports `from datetime import date`, `from dbdelay.data.silver import quality_key, write_silver_month`, `from dbdelay.storage import ObjectStore`, `from dbdelay.training.config import BaselineConfig, EvaluationConfig, FeatureConfig, TrainingConfig`):
```python
def silver_day(  # noqa: PLR0913 - test builder with independent knobs
    day: str,
    n: int = 4,
    *,
    eva: str = "8000105",
    hour_utc: int = 7,
    late_every: int = 2,
    cancelled: int = 0,
) -> pd.DataFrame:
    """``n`` silver rows on UTC ``day`` from ``hour_utc``:00, one per minute.

    Every ``late_every``-th row is 10 min late; the last ``cancelled`` rows are cancelled.
    """
    frame = silver_frame(n, day)
    planned = [
        pd.Timestamp(f"{day} {hour_utc:02d}:00", tz="UTC") + timedelta(minutes=i) for i in range(n)
    ]
    rides = [f"{100 + i}-{day[2:4]}{day[5:7]}{day[8:10]}0600" for i in range(n)]
    late = [i % late_every == late_every - 1 for i in range(n)]
    is_cancelled = [i >= n - cancelled for i in range(n)]
    frame["eva"] = eva
    frame["ride_id"] = rides
    frame["event_id"] = [make_event_id(eva, r, p) for r, p in zip(rides, planned, strict=True)]
    frame["planned_departure_utc"] = pd.Series(planned, dtype="datetime64[us, UTC]")
    frame["changed_departure_utc"] = pd.Series(
        [
            None if c else p + timedelta(minutes=10 if lt else 0)
            for p, lt, c in zip(planned, late, is_cancelled, strict=True)
        ],
        dtype="datetime64[us, UTC]",
    )
    frame["delay_min"] = pd.Series(
        [None if c else (10 if lt else 0) for lt, c in zip(late, is_cancelled, strict=True)],
        dtype="Int16",
    )
    frame["is_cancelled"] = pd.Series(is_cancelled, dtype="bool")
    frame["is_late"] = pd.Series(
        [None if c else lt for lt, c in zip(late, is_cancelled, strict=True)], dtype="boolean"
    )
    return frame


MARCH_CONFIG = TrainingConfig(
    window_months=1,
    end_date=date(2026, 3, 31),
    test_days=7,
    valid_days=7,
    exclude_data_gaps=True,
    features=FeatureConfig(min_count=1),
    baseline=BaselineConfig(min_count=2),
    evaluation=EvaluationConfig(ece_bins=10, slice_min_rows=1),
)


def put_march_silver(store: ObjectStore, root: str = "", *, with_report: bool = True) -> None:
    """March 2026: 4 rows/day at 07:00 UTC, every 2nd late; 1 cancelled on 03-05;
    gap hour 2026-03-16T08 (local) and station gap day 8000105 / 2026-03-20.

    With ``MARCH_CONFIG``: train 03-01..17 → 63 rows (31 late), valid 03-18..24 → 24 (12),
    test 03-25..31 → 28 (14); excluded: 1 cancelled, 4 gap_hours, 4 gap_station_days.
    """
    days = []
    for d in range(1, 32):
        day = f"2026-03-{d:02d}"
        days.append(silver_day(day, cancelled=1 if day == "2026-03-05" else 0))
    frames = [f for f in days if not f.empty]
    silver = pd.concat(frames, ignore_index=True)
    write_silver_month(silver, store, "2026-03", root)
    if with_report:
        report = quality_report(
            "2026-03", low_volume_hours=["2026-03-16T08"], drop_days={"8000105": ["2026-03-20"]}
        )
        store.put_bytes(quality_key("2026-03", root), report.model_dump_json().encode())
```

- [ ] **Step 2: Write the failing tests** — `tests/unit/test_snapshot.py`

```python
from datetime import date
from pathlib import Path

import pytest

from dbdelay.data.silver import quality_key
from dbdelay.errors import DataValidationError
from dbdelay.storage import ObjectStore
from dbdelay.training.split import (
    build_snapshot,
    latest_silver_day,
    load_snapshot_split,
    snapshot_prefix,
)
from tests.builders import MARCH_CONFIG, put_march_silver, quality_report


def test_snapshot_rows_exclusions_and_files(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)

    assert manifest.snapshot_id == f"2026-03-31_{manifest.content_hash[:8]}"
    assert (manifest.window_start, manifest.window_end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert {k: v.rows for k, v in manifest.splits.items()} == {"train": 63, "valid": 24, "test": 28}
    assert manifest.splits["train"].late_rate == pytest.approx(31 / 63)
    assert manifest.splits["test"].late_rate == pytest.approx(0.5)
    assert manifest.excluded == {"cancelled": 1, "gap_hours": 4, "gap_station_days": 4}
    assert manifest.silver_days == 31
    assert manifest.quality_months == ["2026-03"]
    prefix = snapshot_prefix(manifest.snapshot_id)
    for name in ("train.parquet", "valid.parquet", "test.parquet", "snapshot.json"):
        assert s3_store.exists(prefix + name)

    test = load_snapshot_split(s3_store, manifest.snapshot_id, "test", tmp_path)
    assert len(test) == 28
    assert test["event_id"].is_monotonic_increasing
    assert test["is_late"].notna().all()


def test_snapshot_is_deterministic_and_idempotent(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    first = build_snapshot(s3_store, MARCH_CONFIG, tmp_path / "a")
    key = snapshot_prefix(first.snapshot_id) + "train.parquet"
    before = s3_store.get_bytes(key)
    second = build_snapshot(s3_store, MARCH_CONFIG, tmp_path / "b")
    assert second == first
    assert s3_store.get_bytes(key) == before


def test_end_date_defaults_to_latest_silver_day(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    assert latest_silver_day(s3_store) == date(2026, 3, 31)
    cfg = MARCH_CONFIG.model_copy(update={"end_date": None})
    assert build_snapshot(s3_store, cfg, tmp_path).window_end == date(2026, 3, 31)


def test_gap_exclusion_can_be_switched_off(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store, with_report=False)
    cfg = MARCH_CONFIG.model_copy(update={"exclude_data_gaps": False})
    manifest = build_snapshot(s3_store, cfg, tmp_path)
    assert manifest.excluded == {"cancelled": 1, "gap_hours": 0, "gap_station_days": 0}
    assert manifest.quality_months == []


def test_missing_quality_report_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store, with_report=False)
    with pytest.raises(DataValidationError, match="quality report"):
        build_snapshot(s3_store, MARCH_CONFIG, tmp_path)


def test_missing_silver_day_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    cfg = MARCH_CONFIG.model_copy(update={"end_date": date(2026, 4, 2)})
    with pytest.raises(DataValidationError, match="silver day"):
        build_snapshot(s3_store, cfg, tmp_path)


def test_empty_split_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    cfg = MARCH_CONFIG.model_copy(update={"test_days": 1, "valid_days": 1})
    # valid = 2026-03-30 only; flag that station-day so the valid split is empty.
    report = quality_report(
        "2026-03",
        low_volume_hours=["2026-03-16T08"],
        drop_days={"8000105": ["2026-03-20", "2026-03-30"]},
    )
    s3_store.put_bytes(quality_key("2026-03"), report.model_dump_json().encode())
    with pytest.raises(DataValidationError, match="valid"):
        build_snapshot(s3_store, cfg, tmp_path)


def test_no_silver_at_all_is_an_error(s3_store: ObjectStore, tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="no silver"):
        latest_silver_day(s3_store)


def test_manifest_records_split_config(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    manifest = build_snapshot(s3_store, MARCH_CONFIG, tmp_path)
    assert manifest.config == {
        "window_months": 1,
        "end_date": "2026-03-31",
        "test_days": 7,
        "valid_days": 7,
        "exclude_data_gaps": True,
    }
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/unit/test_snapshot.py -q -W error`
Expected: FAIL — `ImportError: cannot import name 'build_snapshot'`

- [ ] **Step 4: Implement** — append to `src/dbdelay/training/split.py` (merge the new imports into the import block at the top)

```python
import hashlib
from pathlib import Path
from typing import Any

import duckdb

from dbdelay.data.silver import (
    SILVER_ARROW_SCHEMA,
    SILVER_COLUMNS,
    quality_key,
    silver_key,
    to_parquet_bytes,
)
from dbdelay.errors import NotFoundError
from dbdelay.storage import ObjectStore

SILVER_PREFIX = "silver/departures/source=hf/"
SNAPSHOT_FILE = "snapshot.json"
_SPLIT_CONFIG_KEYS = ("window_months", "end_date", "test_days", "valid_days", "exclude_data_gaps")


class SplitInfo(BaseModel):
    start: date
    end: date
    rows: int
    late_rate: float


class SnapshotManifest(BaseModel):
    """`snapshot.json` — what the snapshot contains and how it was made."""

    snapshot_id: str
    content_hash: str
    window_start: date
    window_end: date
    splits: dict[str, SplitInfo]
    excluded: dict[str, int]
    silver_days: int
    quality_months: list[str]
    config: dict[str, Any]


def snapshot_prefix(snapshot_id: str, root: str = "") -> str:
    return f"{root}gold/training_sets/{snapshot_id}/"


def latest_silver_day(store: ObjectStore, root: str = "") -> date:
    """Newest UTC day with a silver partition.

    Raises:
        DataValidationError: if there is no silver data at all.
    """
    days = [
        date.fromisoformat(key.split("date=", 1)[1][:10])
        for key in store.iter_keys(root + SILVER_PREFIX)
        if "date=" in key
    ]
    if not days:
        raise DataValidationError("no silver days found")
    return max(days)


def _load_reports(store: ObjectStore, months: Sequence[str], root: str) -> list[QualityReport]:
    reports = []
    for month in months:
        try:
            raw = store.get_bytes(quality_key(month, root))
        except NotFoundError:
            raise DataValidationError(f"quality report for {month} is missing") from None
        reports.append(QualityReport.model_validate_json(raw))
    return reports


def _read_labelled_silver(
    store: ObjectStore, days: Sequence[date], workdir: Path, root: str
) -> tuple[pd.DataFrame, int]:
    """Labelled rows of ``days`` sorted by event_id, and the count of unlabelled (cancelled) rows."""
    files = []
    for day in days:
        path = workdir / f"silver-{day:%Y-%m-%d}.parquet"
        try:
            store.download_file(silver_key(day, root), path)
        except NotFoundError:
            raise DataValidationError(f"silver day {day} is missing") from None
        files.append(str(path))
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        params = {"files": files}
        counted = con.execute(
            "SELECT count(*) FROM read_parquet($files) WHERE is_late IS NULL", params
        ).fetchone()
        rows = con.execute(
            "SELECT * FROM read_parquet($files) WHERE is_late IS NOT NULL ORDER BY event_id",
            params,
        ).df()
    finally:
        con.close()
    return rows, int(counted[0]) if counted else 0


def build_snapshot(
    store: ObjectStore, cfg: TrainingConfig, workdir: Path, *, root: str = ""
) -> SnapshotManifest:
    """Build (or reuse) the gold snapshot for the configured window.

    Raises:
        DataValidationError: missing silver day or quality report, too-short window,
            or an empty split.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    end = cfg.end_date or latest_silver_day(store, root)
    windows = compute_windows(end, cfg)
    rows, cancelled = _read_labelled_silver(store, windows.days(), workdir, root)
    excluded = {"cancelled": cancelled, "gap_hours": 0, "gap_station_days": 0}
    quality_months: list[str] = []
    if cfg.exclude_data_gaps:
        quality_months = windows.months()
        in_hour, in_day = gap_masks(rows, _load_reports(store, quality_months, root))
        excluded["gap_hours"] = int(in_hour.sum())
        excluded["gap_station_days"] = int((in_day & ~in_hour).sum())
        rows = rows[~(in_hour | in_day)].reset_index(drop=True)
    split = assign_split(rows["planned_departure_utc"], windows).to_numpy()

    digest = hashlib.sha256()
    infos: dict[str, SplitInfo] = {}
    paths: dict[str, Path] = {}
    for name, (lo, hi) in windows.split_ranges().items():
        part = rows[split == name].reset_index(drop=True)[list(SILVER_COLUMNS)]
        if part.empty:
            raise DataValidationError(f"split {name} is empty")
        data = to_parquet_bytes(part, SILVER_ARROW_SCHEMA)
        digest.update(data)
        paths[name] = workdir / f"{name}.parquet"
        paths[name].write_bytes(data)
        infos[name] = SplitInfo(
            start=lo, end=hi, rows=len(part), late_rate=float(part["is_late"].mean())
        )
    content_hash = digest.hexdigest()
    manifest = SnapshotManifest(
        snapshot_id=f"{end:%Y-%m-%d}_{content_hash[:8]}",
        content_hash=content_hash,
        window_start=windows.start,
        window_end=windows.end,
        splits=infos,
        excluded=excluded,
        silver_days=len(windows.days()),
        quality_months=quality_months,
        config={
            key: value
            for key, value in cfg.model_dump(mode="json").items()
            if key in _SPLIT_CONFIG_KEYS
        }
        | {"end_date": end.isoformat()},
    )
    prefix = snapshot_prefix(manifest.snapshot_id, root)
    if store.exists(prefix + SNAPSHOT_FILE):
        return SnapshotManifest.model_validate_json(store.get_bytes(prefix + SNAPSHOT_FILE))
    for name, path in paths.items():
        store.upload_file(prefix + f"{name}.parquet", path)
    store.put_bytes(
        prefix + SNAPSHOT_FILE, manifest.model_dump_json(indent=2).encode(), "application/json"
    )
    return manifest


def load_snapshot_split(
    store: ObjectStore, snapshot_id: str, split: str, workdir: Path, *, root: str = ""
) -> pd.DataFrame:
    """Download one split of a snapshot and read it."""
    path = workdir / f"load-{split}.parquet"
    store.download_file(snapshot_prefix(snapshot_id, root) + f"{split}.parquet", path)
    return pd.read_parquet(path)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_snapshot.py tests/unit/test_split_windows.py -q -W error`
Expected: PASS (15 passed)

- [ ] **Step 6: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/training/split.py tests/unit/test_snapshot.py tests/unit/conftest.py tests/builders.py
git commit -m "feat(training): deterministic gold snapshot with data-gap exclusion"
```

---

### Task 7: Baseline model

**Files:**
- Create: `src/dbdelay/training/baseline.py`
- Test: `tests/unit/test_baseline.py`

**Interfaces:**
- Consumes: feature frames with columns `eva, train_type, hour_local, weekday` (from `build_features`)
- Produces: `BACKOFF_LEVELS`; `BaselineModel.fit(features: pd.DataFrame, y: NDArray[np.bool_], min_count: int) -> BaselineModel`; `.predict(features) -> NDArray[np.float64]`; `.to_json() -> str`; `BaselineModel.from_json(text: str | bytes) -> BaselineModel`; attributes `min_count`, `global_rate`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_baseline.py`

```python
import numpy as np
import pandas as pd
import pytest

from dbdelay.errors import DataValidationError
from dbdelay.training.baseline import BaselineModel

TRAIN = [
    # (eva, train_type, hour_local, weekday, late)
    ("A", "RB", 8, 1, True),
    ("A", "RB", 8, 1, True),
    ("A", "RB", 8, 2, False),
    ("A", "RB", 9, 3, False),
    ("B", "RB", 7, 0, False),
    ("B", "ICE", 7, 0, True),
    ("B", "ICE", 7, 0, True),
]


def _features(rows: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["eva", "train_type", "hour_local", "weekday"])


def _fit() -> BaselineModel:
    frame = _features([row[:4] for row in TRAIN])
    labels = np.array([row[4] for row in TRAIN])
    return BaselineModel.fit(frame, labels, min_count=2)


def test_each_backoff_level_is_used() -> None:
    queries = _features(
        [
            ("A", "RB", 8, 1),  # level 1: (A, RB, 8, 1) n=2 → 2/2
            ("A", "RB", 8, 5),  # level 2: (A, RB, 8) n=3 → 2/3
            ("A", "RB", 9, 3),  # level 3: (A, RB) n=4 → 2/4
            ("B", "RB", 7, 0),  # level 4: (RB) n=5 → 2/5
            ("C", "S", 7, 0),  # global: 4/7
        ]
    )
    assert _fit().predict(queries) == pytest.approx([1.0, 2 / 3, 0.5, 0.4, 4 / 7])


def test_json_round_trip_gives_identical_predictions() -> None:
    model = _fit()
    queries = _features([row[:4] for row in TRAIN] + [("C", "S", 7, 0)])
    again = BaselineModel.from_json(model.to_json())
    assert (again.min_count, again.global_rate) == (model.min_count, model.global_rate)
    np.testing.assert_array_equal(again.predict(queries), model.predict(queries))
    assert again.to_json() == model.to_json()


def test_fit_rejects_empty_or_mismatched_input() -> None:
    with pytest.raises(DataValidationError):
        BaselineModel.fit(_features([]), np.array([], dtype=bool), min_count=1)
    with pytest.raises(DataValidationError):
        BaselineModel.fit(_features([("A", "RB", 8, 1)]), np.array([True, False]), min_count=1)


def test_from_json_rejects_other_levels() -> None:
    with pytest.raises(DataValidationError):
        BaselineModel.from_json('{"min_count": 1, "global_rate": 0.5, "levels": []}')
    with pytest.raises(DataValidationError):
        BaselineModel.from_json("not json")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_baseline.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.training.baseline'`

- [ ] **Step 3: Implement** — `src/dbdelay/training/baseline.py`

```python
"""Late-rate lookup baseline with back-off to coarser groups (architecture §5.2)."""

import json
from collections.abc import Sequence

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from dbdelay.errors import DataValidationError

BACKOFF_LEVELS: tuple[tuple[str, ...], ...] = (
    ("eva", "train_type", "hour_local", "weekday"),
    ("eva", "train_type", "hour_local"),
    ("eva", "train_type"),
    ("train_type",),
)


def _group_keys(features: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    keys = features[columns[0]].astype(str)
    for column in columns[1:]:
        keys = keys + "|" + features[column].astype(str)
    return keys


class BaselineModel:
    """Predicts the train late rate of the most specific group with ≥ ``min_count`` rows."""

    def __init__(
        self,
        *,
        min_count: int,
        global_rate: float,
        groups: dict[tuple[str, ...], dict[str, tuple[int, int]]],
    ) -> None:
        self.min_count = min_count
        self.global_rate = global_rate
        self.groups = groups

    @classmethod
    def fit(
        cls, features: pd.DataFrame, y: NDArray[np.bool_], min_count: int
    ) -> "BaselineModel":
        """Count rows and late rows per group at every back-off level.

        Raises:
            DataValidationError: if there are no rows or ``y`` does not match the rows.
        """
        labels = np.asarray(y, dtype=bool)
        if len(features) == 0 or len(features) != len(labels):
            raise DataValidationError("baseline needs a non-empty train set with one label per row")
        groups: dict[tuple[str, ...], dict[str, tuple[int, int]]] = {}
        for level in BACKOFF_LEVELS:
            stats = (
                pd.DataFrame({"key": _group_keys(features, level).to_numpy(), "late": labels})
                .groupby("key")["late"]
                .agg(["size", "sum"])
            )
            kept = stats[stats["size"] >= min_count]
            groups[level] = {
                str(key): (int(n), int(late))
                for key, n, late in zip(kept.index, kept["size"], kept["sum"], strict=True)
            }
        return cls(min_count=min_count, global_rate=float(labels.mean()), groups=groups)

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        """Late probability per row (first level with a qualifying group, else global)."""
        result = np.full(len(features), np.nan)
        for level in BACKOFF_LEVELS:
            missing = np.isnan(result)
            if not missing.any():
                break
            rates = {key: late / n for key, (n, late) in self.groups[level].items()}
            mapped = _group_keys(features, level).map(rates).to_numpy(dtype=float, na_value=np.nan)
            result = np.where(missing, mapped, result)
        return np.where(np.isnan(result), self.global_rate, result)

    def to_json(self) -> str:
        return json.dumps(
            {
                "min_count": self.min_count,
                "global_rate": self.global_rate,
                "levels": [
                    {
                        "columns": list(level),
                        "groups": {k: list(v) for k, v in sorted(self.groups[level].items())},
                    }
                    for level in BACKOFF_LEVELS
                ],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> "BaselineModel":
        """Restore a saved baseline.

        Raises:
            DataValidationError: if the JSON is malformed or has other back-off levels.
        """
        try:
            raw = json.loads(text)
            levels = [tuple(level["columns"]) for level in raw["levels"]]
            if levels != list(BACKOFF_LEVELS):
                raise DataValidationError("baseline back-off levels do not match this code")
            groups = {
                tuple(level["columns"]): {k: (int(v[0]), int(v[1])) for k, v in level["groups"].items()}
                for level in raw["levels"]
            }
            return cls(
                min_count=int(raw["min_count"]),
                global_rate=float(raw["global_rate"]),
                groups=groups,
            )
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise DataValidationError("invalid baseline JSON") from exc
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_baseline.py -q -W error`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/training/baseline.py tests/unit/test_baseline.py
git commit -m "feat(training): late-rate baseline with back-off levels"
```

---

### Task 8: Evaluation metrics

**Files:**
- Create: `src/dbdelay/training/evaluate.py`
- Test: `tests/unit/test_evaluate.py`

**Interfaces:**
- Consumes: scikit-learn metrics; feature frames (`train_type`, `eva`, `hour_local`)
- Produces: `Metrics`, `CalibrationBin`, `SplitReport`, `EvaluationReport` (fields `snapshot_id: str, spec_hash: str, config: dict[str, Any], splits: dict[str, SplitReport]`); `compute_metrics(y_true, y_prob, *, ece_bins: int, min_rows: int) -> Metrics`; `calibration_bins(y_true, y_prob, bins) -> list[CalibrationBin]`; `expected_calibration_error(y_true, y_prob, bins) -> float`; `hour_band(hours: pd.Series) -> pd.Series`; `slice_frame(features: pd.DataFrame) -> pd.DataFrame`; `evaluate_split(y_true, y_prob, slices: pd.DataFrame, *, ece_bins: int, slice_min_rows: int) -> SplitReport`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_evaluate.py`

```python
import math

import numpy as np
import pandas as pd
import pytest

from dbdelay.training.evaluate import (
    calibration_bins,
    compute_metrics,
    evaluate_split,
    expected_calibration_error,
    hour_band,
    slice_frame,
)

Y = np.array([False, False, True, True])
P = np.array([0.1, 0.4, 0.35, 0.8])


def test_metrics_match_hand_computed_values() -> None:
    m = compute_metrics(Y, P, ece_bins=2, min_rows=1)
    assert m.n == 4
    assert m.base_rate == 0.5
    assert m.brier == pytest.approx(0.158125)
    assert m.roc_auc == pytest.approx(0.75)
    assert m.pr_auc == pytest.approx(0.8333333, rel=1e-6)
    assert m.log_loss == pytest.approx(0.4722880, rel=1e-6)
    assert m.ece == pytest.approx(0.0875)


def test_calibration_bins_and_ece() -> None:
    bins = calibration_bins(Y, P, 2)
    assert [(b.lower, b.upper, b.count) for b in bins] == [(0.0, 0.5, 3), (0.5, 1.0, 1)]
    assert bins[0].mean_pred == pytest.approx(0.85 / 3)
    assert bins[0].observed_rate == pytest.approx(1 / 3)
    assert expected_calibration_error(Y, P, 2) == pytest.approx(0.0875)
    assert calibration_bins(np.array([True]), np.array([1.0]), 10)[0].lower == pytest.approx(0.9)


def test_small_or_one_class_slices_report_null_metrics() -> None:
    small = compute_metrics(Y, P, ece_bins=10, min_rows=5)
    assert (small.n, small.base_rate, small.brier, small.roc_auc) == (4, 0.5, None, None)
    one_class = compute_metrics(Y[:2], P[:2], ece_bins=10, min_rows=1)
    assert one_class.roc_auc is None
    empty = compute_metrics(Y[:0], P[:0], ece_bins=10, min_rows=1)
    assert (empty.n, empty.base_rate) == (0, None)


def test_extreme_probabilities_give_finite_log_loss() -> None:
    m = compute_metrics(np.array([False, True]), np.array([0.0, 1.0]), ece_bins=10, min_rows=1)
    assert m.log_loss is not None
    assert math.isfinite(m.log_loss)
    assert m.brier == pytest.approx(0.0)


def test_hour_bands() -> None:
    hours = pd.Series([0, 5, 6, 9, 10, 15, 16, 19, 20, 23])
    assert hour_band(hours).tolist() == [
        "00-05", "00-05", "06-09", "06-09", "10-15",
        "10-15", "16-19", "16-19", "20-23", "20-23",
    ]  # fmt: skip


def test_evaluate_split_builds_overall_bins_and_slices() -> None:
    features = pd.DataFrame(
        {
            "train_type": pd.Categorical(["RB", "RB", "ICE", "ICE"]),
            "eva": pd.Categorical(["A", "A", "A", "B"]),
            "hour_local": [7, 7, 18, 18],
        }
    )
    report = evaluate_split(Y, P, slice_frame(features), ece_bins=2, slice_min_rows=2)
    assert report.overall.n == 4
    assert [b.count for b in report.calibration] == [3, 1]
    assert set(report.slices) == {"train_type", "eva", "hour_band"}
    assert list(report.slices["train_type"]) == ["ICE", "RB"]
    assert report.slices["eva"]["B"].n == 1
    assert report.slices["eva"]["B"].brier is None
    assert report.slices["hour_band"]["06-09"].roc_auc is None  # one class only
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_evaluate.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.training.evaluate'`

- [ ] **Step 3: Implement** — `src/dbdelay/training/evaluate.py`

```python
"""Probability metrics for the late label: overall, calibration bins and slices."""

from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

EPS = 1e-6
HOUR_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 5, "00-05"),
    (6, 9, "06-09"),
    (10, 15, "10-15"),
    (16, 19, "16-19"),
    (20, 23, "20-23"),
)
SLICE_COLUMNS: tuple[str, ...] = ("train_type", "eva", "hour_band")


class Metrics(BaseModel):
    n: int
    base_rate: float | None
    brier: float | None = None
    roc_auc: float | None = None
    pr_auc: float | None = None
    log_loss: float | None = None
    ece: float | None = None


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    mean_pred: float
    observed_rate: float
    count: int


class SplitReport(BaseModel):
    overall: Metrics
    calibration: list[CalibrationBin]
    slices: dict[str, dict[str, Metrics]]


class EvaluationReport(BaseModel):
    """`metrics.json`."""

    snapshot_id: str
    spec_hash: str
    config: dict[str, Any]
    splits: dict[str, SplitReport]


def calibration_bins(y_true: ArrayLike, y_prob: ArrayLike, bins: int) -> list[CalibrationBin]:
    """Equal-width probability bins (non-empty only): mean prediction vs observed late rate."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    index = np.minimum((p * bins).astype(np.int64), bins - 1)
    result = []
    for b in range(bins):
        mask = index == b
        if mask.any():
            result.append(
                CalibrationBin(
                    lower=b / bins,
                    upper=(b + 1) / bins,
                    mean_pred=float(p[mask].mean()),
                    observed_rate=float(y[mask].mean()),
                    count=int(mask.sum()),
                )
            )
    return result


def expected_calibration_error(y_true: ArrayLike, y_prob: ArrayLike, bins: int) -> float:
    """Bin-size-weighted mean |mean prediction − observed rate|."""
    n = len(np.asarray(y_true))
    return float(
        sum(b.count / n * abs(b.mean_pred - b.observed_rate) for b in calibration_bins(y_true, y_prob, bins))
    )


def compute_metrics(y_true: ArrayLike, y_prob: ArrayLike, *, ece_bins: int, min_rows: int) -> Metrics:
    """All metrics, or only ``n``/``base_rate`` when too few rows or a single class."""
    y = np.asarray(y_true, dtype=bool)
    p = np.asarray(y_prob, dtype=float)
    n = len(y)
    if n == 0:
        return Metrics(n=0, base_rate=None)
    base_rate = float(y.mean())
    if n < min_rows or y.all() or not y.any():
        return Metrics(n=n, base_rate=base_rate)
    y_int = y.astype(int)
    return Metrics(
        n=n,
        base_rate=base_rate,
        brier=float(brier_score_loss(y_int, p)),
        roc_auc=float(roc_auc_score(y_int, p)),
        pr_auc=float(average_precision_score(y_int, p)),
        log_loss=float(log_loss(y_int, np.clip(p, EPS, 1 - EPS), labels=[0, 1])),
        ece=expected_calibration_error(y, p, ece_bins),
    )


def hour_band(hours: pd.Series) -> pd.Series:
    """Map local hours to the five slice bands."""
    values = hours.to_numpy()
    labels = np.full(len(values), "", dtype=object)
    for low, high, name in HOUR_BANDS:
        labels[(values >= low) & (values <= high)] = name
    return pd.Series(labels, index=hours.index)


def slice_frame(features: pd.DataFrame) -> pd.DataFrame:
    """Slice keys for ``evaluate_split`` from a feature frame."""
    return pd.DataFrame(
        {
            "train_type": features["train_type"].astype(str).to_numpy(),
            "eva": features["eva"].astype(str).to_numpy(),
            "hour_band": hour_band(features["hour_local"]).to_numpy(),
        }
    )


def evaluate_split(
    y_true: ArrayLike,
    y_prob: ArrayLike,
    slices: pd.DataFrame,
    *,
    ece_bins: int,
    slice_min_rows: int,
) -> SplitReport:
    """Overall metrics, calibration bins and per-slice metrics for one split."""
    y: NDArray[np.bool_] = np.asarray(y_true, dtype=bool)
    p: NDArray[np.float64] = np.asarray(y_prob, dtype=float)
    per_slice: dict[str, dict[str, Metrics]] = {}
    for column in SLICE_COLUMNS:
        keys = slices[column].astype(str).to_numpy()
        per_slice[column] = {
            value: compute_metrics(
                y[keys == value], p[keys == value], ece_bins=ece_bins, min_rows=slice_min_rows
            )
            for value in sorted(set(keys.tolist()))
        }
    return SplitReport(
        overall=compute_metrics(y, p, ece_bins=ece_bins, min_rows=1),
        calibration=calibration_bins(y, p, ece_bins),
        slices=per_slice,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_evaluate.py -q -W error`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format src tests
git add src/dbdelay/training/evaluate.py tests/unit/test_evaluate.py
git commit -m "feat(training): Brier, AUC, PR-AUC, log loss, ECE and slice metrics"
```

---

### Task 9: `run_baseline` CLI and `make baseline`

**Files:**
- Create: `src/dbdelay/training/run_baseline.py`
- Modify: `Makefile` (new target), `README.md` (one command line)
- Test: `tests/unit/test_run_baseline.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8; `load_stations`, `get_settings`, `make_s3_client`, `get_logger`
- Produces: `run_baseline(store, cfg, stations, workdir: Path, *, root: str = "") -> EvaluationReport` writing `gold/training_sets/<id>/baseline/{feature_spec.json, baseline.json, metrics.json}`; `main(argv: Sequence[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_run_baseline.py`

```python
import json
from pathlib import Path

import pytest

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.features.spec import FeatureSpec
from dbdelay.storage import ObjectStore
from dbdelay.training.baseline import BaselineModel
from dbdelay.training.run_baseline import main, run_baseline
from dbdelay.training.split import snapshot_prefix
from tests.builders import MARCH_CONFIG, put_march_silver

REPO = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO / "configs" / "stations.yaml")


def test_run_baseline_writes_spec_model_and_metrics(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    report = run_baseline(s3_store, MARCH_CONFIG, STATIONS, tmp_path)

    assert set(report.splits) == {"valid", "test"}
    assert report.splits["valid"].overall.n == 24
    assert report.splits["test"].overall.n == 28
    prefix = snapshot_prefix(report.snapshot_id) + "baseline/"
    spec = FeatureSpec.from_json(s3_store.get_bytes(prefix + "feature_spec.json"))
    assert spec.spec_hash == report.spec_hash
    BaselineModel.from_json(s3_store.get_bytes(prefix + "baseline.json"))
    stored = json.loads(s3_store.get_bytes(prefix + "metrics.json"))
    assert stored["snapshot_id"] == report.snapshot_id


def test_run_baseline_is_deterministic(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    first = run_baseline(s3_store, MARCH_CONFIG, STATIONS, tmp_path / "a")
    second = run_baseline(s3_store, MARCH_CONFIG, STATIONS, tmp_path / "b")
    assert first == second


def test_main_runs_end_to_end(
    s3_store: ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_BUCKET", s3_store.bucket)
    monkeypatch.setenv("STATIONS_FILE", str(REPO / "configs" / "stations.yaml"))
    get_settings.cache_clear()
    put_march_silver(s3_store)
    config = tmp_path / "training.yaml"
    config.write_text(
        "window_months: 1\nend_date: 2026-03-31\ntest_days: 7\nvalid_days: 7\n"
        "exclude_data_gaps: true\nfeatures: {min_count: 1}\nbaseline: {min_count: 2}\n"
        "evaluation: {ece_bins: 10, slice_min_rows: 1}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(config)]) == 0
    keys = list(s3_store.iter_keys("gold/training_sets/"))
    assert sum(key.endswith("baseline/metrics.json") for key in keys) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_run_baseline.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbdelay.training.run_baseline'`

- [ ] **Step 3: Implement** — `src/dbdelay/training/run_baseline.py`

```python
"""`make baseline`: snapshot → feature spec → late-rate baseline → metrics (Phase 3)."""

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path

from dbdelay.config import get_settings
from dbdelay.data.stations import Station, load_stations
from dbdelay.features.build import build_features
from dbdelay.features.spec import fit_spec
from dbdelay.logging import get_logger
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.baseline import BaselineModel
from dbdelay.training.config import TrainingConfig, load_training_config
from dbdelay.training.evaluate import EvaluationReport, evaluate_split, slice_frame
from dbdelay.training.split import SPLITS, build_snapshot, load_snapshot_split, snapshot_prefix

DEFAULT_CONFIG = Path("configs/training.yaml")
EVAL_SPLITS: tuple[str, ...] = ("valid", "test")
BASELINE_DIR = "baseline/"


def run_baseline(
    store: ObjectStore,
    cfg: TrainingConfig,
    stations: Sequence[Station],
    workdir: Path,
    *,
    root: str = "",
) -> EvaluationReport:
    """Build or reuse the snapshot, fit spec + baseline on train, evaluate valid and test."""
    manifest = build_snapshot(store, cfg, workdir, root=root)
    frames = {
        name: load_snapshot_split(store, manifest.snapshot_id, name, workdir, root=root)
        for name in SPLITS
    }
    spec = fit_spec(frames["train"], stations, cfg.features.min_count)
    features = {name: build_features(frame, spec) for name, frame in frames.items()}
    labels = {name: frame["is_late"].to_numpy(dtype=bool) for name, frame in frames.items()}
    model = BaselineModel.fit(features["train"], labels["train"], cfg.baseline.min_count)
    report = EvaluationReport(
        snapshot_id=manifest.snapshot_id,
        spec_hash=spec.spec_hash,
        config=cfg.model_dump(mode="json"),
        splits={
            name: evaluate_split(
                labels[name],
                model.predict(features[name]),
                slice_frame(features[name]),
                ece_bins=cfg.evaluation.ece_bins,
                slice_min_rows=cfg.evaluation.slice_min_rows,
            )
            for name in EVAL_SPLITS
        },
    )
    prefix = snapshot_prefix(manifest.snapshot_id, root) + BASELINE_DIR
    store.put_bytes(prefix + "feature_spec.json", spec.to_json().encode(), "application/json")
    store.put_bytes(prefix + "baseline.json", model.to_json().encode(), "application/json")
    store.put_bytes(
        prefix + "metrics.json", report.model_dump_json(indent=2).encode(), "application/json"
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the training snapshot and score the baseline.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    settings = get_settings()
    cfg = load_training_config(args.config)
    stations = load_stations(settings.stations_file)
    store = ObjectStore(make_s3_client(settings), settings.data_bucket)
    log = get_logger("training")
    with tempfile.TemporaryDirectory() as tmp:
        report = run_baseline(store, cfg, stations, Path(tmp))
    for name, split in report.splits.items():
        log.info(
            "baseline metrics",
            extra={"snapshot_id": report.snapshot_id, "split": name, **split.overall.model_dump()},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Makefile — add after `backfill` (keep the `## ` help comment style):
```make
baseline: ## Build the gold training snapshot and evaluate the late-rate baseline (Phase 3)
	uv run python -m dbdelay.training.run_baseline
```

README.md — in the commands block next to the backfill line add:
```text
make baseline        # gold training snapshot + late-rate baseline metrics (needs the backfilled silver)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_run_baseline.py -q -W error`
Expected: PASS (3 passed)

- [ ] **Step 5: Full gate and commit**

Run: `make check`
Expected: ruff + mypy clean, all unit tests pass, coverage report printed.

```bash
uv run ruff format src tests
git add src/dbdelay/training/run_baseline.py tests/unit/test_run_baseline.py Makefile README.md
git commit -m "feat(training): make baseline CLI writes spec, baseline and metrics"
```

---

### Task 10: Integration test on MinIO

**Files:**
- Create: `tests/integration/test_snapshot_minio.py`

**Interfaces:**
- Consumes: `put_march_silver`, `MARCH_CONFIG` (Task 6), `build_snapshot`, `snapshot_prefix` (Task 6), `run_baseline` (Task 9), stack started with `make up`.

- [ ] **Step 1: Write the test** — `tests/integration/test_snapshot_minio.py`

```python
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.run_baseline import run_baseline
from dbdelay.training.split import build_snapshot, snapshot_prefix
from tests.builders import MARCH_CONFIG, put_march_silver

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def scratch() -> Iterator[tuple[ObjectStore, str]]:
    settings = get_settings()
    client = make_s3_client(settings)
    root = f"_integration/{uuid.uuid4()}/"
    yield ObjectStore(client, settings.data_bucket), root
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.data_bucket, Prefix=root):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])


def test_snapshot_twice_gives_same_id_and_bytes(
    scratch: tuple[ObjectStore, str], tmp_path: Path
) -> None:
    store, root = scratch
    put_march_silver(store, root)
    first = build_snapshot(store, MARCH_CONFIG, tmp_path / "a", root=root)
    key = snapshot_prefix(first.snapshot_id, root) + "train.parquet"
    before = store.get_bytes(key)
    second = build_snapshot(store, MARCH_CONFIG, tmp_path / "b", root=root)
    assert second == first
    assert store.get_bytes(key) == before


def test_run_baseline_end_to_end(scratch: tuple[ObjectStore, str], tmp_path: Path) -> None:
    store, root = scratch
    put_march_silver(store, root)
    stations = load_stations(REPO / "configs" / "stations.yaml")
    report = run_baseline(store, MARCH_CONFIG, stations, tmp_path, root=root)
    prefix = snapshot_prefix(report.snapshot_id, root) + "baseline/"
    for name in ("feature_spec.json", "baseline.json", "metrics.json"):
        assert store.exists(prefix + name)
```

- [ ] **Step 2: Run it against the local stack**

Run: `make up` (if not running), then `make test-integration`
Expected: PASS (5 passed — 3 Phase 2 + 2 new)

- [ ] **Step 3: Commit**

```bash
uv run ruff format src tests
git add tests/integration/test_snapshot_minio.py
git commit -m "test(integration): gold snapshot twice and baseline end to end on MinIO"
```

---

### Task 11: Real run on the 9 months and the baseline notebook

**Files:**
- Create: `notebooks/02_baseline.ipynb` (generated by a script), `scripts/make_baseline_notebook.py`

**Interfaces:**
- Consumes: `make baseline` on the real silver (2025-12 … 2026-08 in MinIO), `EvaluationReport` JSON.

- [ ] **Step 1: Real run**

Run: `make up` then `time make baseline 2>&1 | tail -5`
Expected: exit 0; two log lines `baseline metrics` (valid, test) with the snapshot id. Record in the ledger: snapshot id, rows per split, `excluded`, test/valid Brier, ROC-AUC, PR-AUC, log loss, ECE, base rate, wall time.

Read the manifest (no row values):
```bash
uv run python - <<'EOF'
import json
from dbdelay.config import get_settings
from dbdelay.storage import ObjectStore, make_s3_client
s = get_settings(); st = ObjectStore(make_s3_client(s), s.data_bucket)
for key in st.iter_keys("gold/training_sets/"):
    if key.endswith("snapshot.json"):
        m = json.loads(st.get_bytes(key)); print(key); print({k: m[k] for k in ("snapshot_id", "window_start", "window_end", "excluded", "silver_days")})
        print({k: (v["rows"], round(v["late_rate"], 4)) for k, v in m["splits"].items()})
EOF
```

- [ ] **Step 2: Determinism on real data**

Run `make baseline` a second time; compare `metrics.json` bytes of both runs:
```bash
uv run python - <<'EOF'
import hashlib
from dbdelay.config import get_settings
from dbdelay.storage import ObjectStore, make_s3_client
s = get_settings(); st = ObjectStore(make_s3_client(s), s.data_bucket)
for key in sorted(st.iter_keys("gold/training_sets/")):
    if key.endswith(".json"):
        print(key, hashlib.sha256(st.get_bytes(key)).hexdigest()[:16])
EOF
```
Expected: exactly one snapshot id; hashes printed before and after the second run are identical (run the script after each run and compare).

- [ ] **Step 3: Generate the notebook** — `scripts/make_baseline_notebook.py`

```python
"""Write notebooks/02_baseline.ipynb (metrics table + calibration plot of the latest snapshot)."""

from pathlib import Path

import nbformat

CELLS = [
    nbformat.v4.new_markdown_cell(
        "# 02 — Baseline\n\nLate-rate lookup baseline on the Phase 3 gold snapshot "
        "(`make baseline`). Reads `metrics.json` from MinIO; no row data is shown."
    ),
    nbformat.v4.new_code_cell(
        "import json\n\nimport matplotlib.pyplot as plt\nimport pandas as pd\n\n"
        "from dbdelay.config import get_settings\n"
        "from dbdelay.storage import ObjectStore, make_s3_client\n\n"
        "settings = get_settings()\nstore = ObjectStore(make_s3_client(settings), settings.data_bucket)\n"
        "keys = sorted(k for k in store.iter_keys('gold/training_sets/') if k.endswith('baseline/metrics.json'))\n"
        "report = json.loads(store.get_bytes(keys[-1]))\nreport['snapshot_id']"
    ),
    nbformat.v4.new_code_cell(
        "pd.DataFrame({name: split['overall'] for name, split in report['splits'].items()}).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "rows = [{'train_type': k, **v} for k, v in report['splits']['test']['slices']['train_type'].items()]\n"
        "pd.DataFrame(rows).sort_values('n', ascending=False).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "fig, ax = plt.subplots(figsize=(5, 5))\n"
        "for name, split in report['splits'].items():\n"
        "    bins = pd.DataFrame(split['calibration'])\n"
        "    ax.plot(bins['mean_pred'], bins['observed_rate'], marker='o', label=name)\n"
        "ax.plot([0, 1], [0, 1], linestyle='--', color='grey', label='perfect')\n"
        "ax.set(xlabel='predicted late probability', ylabel='observed late rate', "
        "title='Baseline calibration')\nax.legend()\nplt.show()"
    ),
]

notebook = nbformat.v4.new_notebook(cells=CELLS)
Path("notebooks/02_baseline.ipynb").write_text(nbformat.writes(notebook), encoding="utf-8")
```

Run:
```bash
uv run python scripts/make_baseline_notebook.py
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/02_baseline.ipynb
```
Expected: executes without error; the metrics table matches the numbers recorded in Step 1.

- [ ] **Step 4: Commit**

```bash
uv run ruff format src tests scripts
git add scripts/make_baseline_notebook.py notebooks/02_baseline.ipynb
git commit -m "docs(notebook): baseline metrics and calibration plot on the real snapshot"
```

---

### Task 12: Docs, final review, branch record

**Files:**
- Modify: `docs/phases.md` (tick Phase 3 tasks and exit criteria), `docs/architecture.md` (§3.4 add `baseline/` files; §4 `line_key` null rule `"<train_type>:none"`; §14 layout already lists `features/` and `training/`), `CLAUDE.md` (status, keys rotated 2026-09-29, session log), `Phases/README.md`
- Create: `Phases/phase-3-features-baseline.md`, `CHANGELOG.md` Phase 3 entry (top of file, above Phase 2)

- [ ] **Step 1: Airflow image still builds** (main deps changed: `holidays`)

Run: `docker compose --profile airflow build` then `make test-dags`
Expected: build exit 0; `DAG check passed`.

- [ ] **Step 2: Final gates**

Run: `make check`, `make test-integration`, `uv run pre-commit run --all-files`
Expected: all pass; record unit test count and coverage.

- [ ] **Step 3: Final whole-branch review** — per superpowers:executing-plans "Final Review" (review package, reviewer on the most capable model, Review Focus section verbatim, ledger `Ruling:` lines). Fix Critical/Important with a failing test first; ledger Minors.

- [ ] **Step 4: Write docs with measured numbers only** — CHANGELOG entry (*Built · Key decisions · Tested · Known gaps · Docs touched*), `Phases/phase-3-features-baseline.md` (goal, what was built, how it works, decisions, tests with real results, how to run, gaps, next), `Phases/README.md` row, `docs/phases.md` ticks, architecture edits, CLAUDE.md status + log.

- [ ] **Step 5: Commit, merge message, push**

```bash
git add -A docs Phases CHANGELOG.md CLAUDE.md
git commit -m "docs(phase-3): changelog, phase doc, real baseline results and status"
git fetch origin && git merge-tree --write-tree origin/main HEAD
git push -u origin phase-3/features-baseline
```
Write `.git/MERGE_SUMMARY.txt` in the CHANGELOG format; hand the owner:
`git merge --no-ff phase-3/features-baseline -F .git/MERGE_SUMMARY.txt`. Never delete branches.

# Phase 4 — Training, tracking, registry, gate: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A calibrated LightGBM model is trained on the gold snapshot, tracked in MLflow, registered, gated against
baseline and champion, and released to MinIO `models/<v>/` — via `make train` and the `training_pipeline` DAG.

**Architecture:** Step functions in `dbdelay.training.pipeline` share state through one MLflow run (bundle files
logged under `bundle/`); XCom carries only ids. `dbdelay.registry` owns the artifact contract (manifest + SHA-256,
fail-closed loader), the champion pointer (MinIO object) and release/rollback. The DAG and the CLI are thin.

**Tech Stack:** Python 3.12, LightGBM (native Booster), scikit-learn (isotonic fit only), mlflow-skinny 3.16 client,
pandas/pyarrow, boto3 (MinIO), Airflow 3.3.2 TaskFlow, pytest + moto.

**Spec:** `docs/superpowers/specs/2026-10-04-phase-4-training-design.md`

## Global Constraints

- New deps only: `lightgbm` and `mlflow-skinny>=3.16.1,<3.17` (extra `training` + dev group). Nothing else.
- No pickle anywhere; model = `booster.model_to_string()` → `model.txt`; no MLflow pyfunc/flavor.
- Label, silver contract and Phase 3 feature logic unchanged. Features only via `build_features(df, spec)`.
- Gate values = architecture §5.4; challenger Brier must be **strictly** lower than champion's (tie → reject).
- Risk thresholds 0.20 / 0.45 in `configs/training.yaml`, exported in `feature_spec.json`.
- Pointer = `models/_pointer.json` in the models bucket (`Settings.models_bucket`); no deletes in storage code.
- Every random process takes `cfg.seed` (default 42). UTC everywhere.
- `mypy --strict` on `src/dbdelay/`; ruff `E,F,I,B,UP,S,SIM,RUF,PL`, line length 100; Google docstrings.
- Thin DAG: heavy imports inside tasks; logic in `src/dbdelay/`.
- No Claude co-author trailer in commits. Never delete branches. Measured numbers only (else `TBD`).
- Before every commit: `uv run ruff check --fix . && uv run ruff format .` (pre-commit aborts silently otherwise).
- Bash prefix for tools: `export PATH="$PATH:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe/bin"`

## Review Focus

1. Pointer names a champion whose `models/<v>/` is missing or tampered → evaluation fails closed with
   `ArtifactIntegrityError`, never silently skips the champion (test in Task 9).
2. Release re-run after a crash between pointer write and alias → pointer unchanged (previous not overwritten
   with the same version), alias set (test in Task 7).
3. A test-split `train_type` the reference model never saw / slice with too few rows → slice check skips it and
   says so, never crashes or fails the gate on `None` (test in Task 4).
4. Config typos (`learning_rate: 0`, empty grid, `seed` inside `params`, `medium >= high`) → `ConfigError`
   before any work (test in Task 1).
5. A step fails after the MLflow run was created → run marked FAILED, error re-raised (test in Task 9).

---

### Task 1: Dependencies, config sections, risk thresholds

**Files:**
- Modify: `pyproject.toml` (deps via `uv add`, mypy overrides)
- Modify: `src/dbdelay/features/spec.py` (add `RiskThresholds`, `FeatureSpec.risk_thresholds`, hash excludes None)
- Modify: `src/dbdelay/training/config.py`
- Modify: `src/dbdelay/config.py` (add `training_config_file`)
- Modify: `configs/training.yaml`
- Modify: `tests/builders.py` (MARCH_CONFIG gets new sections)
- Modify: `tests/unit/test_run_baseline.py` (YAML from MARCH_CONFIG)
- Test: `tests/unit/test_training_config.py`, `tests/unit/test_feature_spec.py`

**Interfaces:**
- Produces: `RiskThresholds(medium: float, high: float)` in `dbdelay.features.spec`;
  `FeatureSpec.risk_thresholds: RiskThresholds | None = None`;
  `LightGBMGrid`, `LightGBMConfig`, `GateConfig`, `RegistryConfig`, `ReleaseConfig` in `dbdelay.training.config`;
  `TrainingConfig.seed/lightgbm/risk_thresholds/gate/registry/release`; `Settings.training_config_file: Path`;
  `tests.builders.PHASE4_SECTIONS: dict[str, Any]`.

- [ ] **Step 1: Add dependencies**

```bash
uv add --optional training lightgbm "mlflow-skinny>=3.16.1,<3.17"
uv add --dev lightgbm "mlflow-skinny>=3.16.1,<3.17"
uv run python -c "import lightgbm, mlflow; print(lightgbm.__version__, mlflow.__version__)"
```
Expected: versions print (lightgbm 4.x, mlflow 3.16.x).

Add to `pyproject.toml` mypy overrides (only if `uv run mypy` reports missing stubs for them):
```toml
[[tool.mypy.overrides]]
# lightgbm ships no complete type information
module = ["lightgbm", "lightgbm.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Write failing config tests** (append to `tests/unit/test_training_config.py`; replace `VALID` with
the full text below and keep the existing tests passing with it)

```python
VALID = """
window_months: 9
end_date: null
test_days: 14
valid_days: 14
exclude_data_gaps: true
features: {min_count: 200}
baseline: {min_count: 50}
evaluation: {ece_bins: 10, slice_min_rows: 500}
seed: 42
lightgbm:
  num_threads: 8
  num_boost_round: 2000
  early_stopping_rounds: 50
  params: {objective: binary, feature_fraction: 0.9}
  grid: {num_leaves: [31, 127], learning_rate: [0.05, 0.1], min_data_in_leaf: [100, 500]}
risk_thresholds: {medium: 0.20, high: 0.45}
gate:
  min_brier_improvement_vs_baseline: 0.05
  max_brier_regression_vs_champion: 0.0
  max_auc_drop_vs_champion: 0.005
  max_slice_auc_drop: 0.02
  min_test_rows: 20000
registry: {model_name: puenktlich-delay, experiment: puenktlich-delay}
release: {reference_sample_rows: 50000}
"""


def test_repo_config_has_phase4_sections() -> None:
    cfg = load_training_config(REPO / "configs" / "training.yaml")
    assert cfg.seed == 42
    assert cfg.lightgbm.grid.num_leaves == (31, 127)
    assert cfg.lightgbm.params["objective"] == "binary"
    assert (cfg.risk_thresholds.medium, cfg.risk_thresholds.high) == (0.20, 0.45)
    assert cfg.gate.min_brier_improvement_vs_baseline == 0.05
    assert cfg.gate.min_test_rows == 20000
    assert cfg.registry.model_name == "puenktlich-delay"
    assert cfg.release.reference_sample_rows == 50000


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("learning_rate: [0.05, 0.1]", "learning_rate: [0, 0.1]"),
        ("num_leaves: [31, 127]", "num_leaves: []"),
        ("{objective: binary, feature_fraction: 0.9}", "{objective: binary, seed: 1}"),
        ("{objective: binary, feature_fraction: 0.9}", "{objective: regression}"),
        ("{medium: 0.20, high: 0.45}", "{medium: 0.5, high: 0.45}"),
        ("min_test_rows: 20000", "min_test_rows: 0"),
        ("model_name: puenktlich-delay", "model_name: Bad Name"),
        ("seed: 42", "seed: -1"),
    ],
)
def test_bad_phase4_values_raise_config_error(tmp_path: Path, old: str, new: str) -> None:
    assert old in VALID
    with pytest.raises(ConfigError):
        load_training_config(_write(tmp_path, VALID.replace(old, new)))
```

Append to `tests/unit/test_feature_spec.py`:
```python
def test_risk_thresholds_round_trip_and_hash_stable_when_absent() -> None:
    from dbdelay.features.spec import RiskThresholds

    spec = FeatureSpec(min_count=1, levels={c: ("OTHER",) for c in CATEGORICAL_FEATURES},
                       station_states={})
    with_risk = spec.model_copy(update={"risk_thresholds": RiskThresholds(medium=0.2, high=0.45)})
    assert FeatureSpec.from_json(with_risk.to_json()) == with_risk
    assert with_risk.spec_hash != spec.spec_hash
    # Phase 3 specs (no thresholds) keep their hash: None is not part of the hashed JSON.
    assert '"risk_thresholds"' not in spec.model_dump_json(exclude_none=True)


def test_risk_thresholds_must_be_ordered() -> None:
    from pydantic import ValidationError

    from dbdelay.features.spec import RiskThresholds

    with pytest.raises(ValidationError):
        RiskThresholds(medium=0.5, high=0.4)
```
(Import `CATEGORICAL_FEATURES` and `FeatureSpec` at the top of the test file if not already imported.)

- [ ] **Step 3: Run tests — expect FAIL**

Run: `uv run pytest tests/unit/test_training_config.py tests/unit/test_feature_spec.py -q`
Expected: FAIL (`extra fields not permitted` / ImportError `RiskThresholds`).

- [ ] **Step 4: Implement**

`src/dbdelay/features/spec.py` — add imports `Field, model_validator` and:
```python
class RiskThresholds(BaseModel):
    """Risk-level cut-offs for ``p_late``: Low < medium ≤ Medium < high ≤ High (prd §5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    medium: float = Field(gt=0, lt=1)
    high: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def _ordered(self) -> "RiskThresholds":
        if not self.medium < self.high:
            raise ValueError("risk_thresholds.medium must be below risk_thresholds.high")
        return self
```
In `FeatureSpec` add the field `risk_thresholds: RiskThresholds | None = None` (after `station_states`) and change
`spec_hash` to:
```python
    @property
    def spec_hash(self) -> str:
        # exclude_none keeps the hash of Phase 3 specs (no risk thresholds) unchanged.
        return hashlib.sha256(self.model_dump_json(exclude_none=True).encode()).hexdigest()
```

`src/dbdelay/training/config.py` — new imports and sections:
```python
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, field_validator

from dbdelay.features.spec import RiskThresholds

# Set by train.py itself (determinism, grid) — not allowed in `lightgbm.params`.
MANAGED_PARAMS = frozenset(
    {"seed", "deterministic", "force_row_wise", "num_threads", "verbose", "metric",
     "num_leaves", "learning_rate", "min_data_in_leaf", "num_iterations", "num_boost_round"}
)
Rate = Annotated[float, Field(gt=0, le=1)]


class LightGBMGrid(_Section):
    num_leaves: tuple[Annotated[int, Field(ge=2)], ...] = Field(min_length=1)
    learning_rate: tuple[Rate, ...] = Field(min_length=1)
    min_data_in_leaf: tuple[PositiveInt, ...] = Field(min_length=1)


class LightGBMConfig(_Section):
    num_threads: PositiveInt
    num_boost_round: PositiveInt
    early_stopping_rounds: PositiveInt
    params: dict[str, str | int | float | bool]
    grid: LightGBMGrid

    @field_validator("params")
    @classmethod
    def _check_params(
        cls, params: dict[str, str | int | float | bool]
    ) -> dict[str, str | int | float | bool]:
        managed = sorted(MANAGED_PARAMS & set(params))
        if managed:
            raise ValueError(f"lightgbm.params must not set {managed}")
        if params.get("objective") != "binary":
            raise ValueError("lightgbm.params.objective must be 'binary'")
        return params


class GateConfig(_Section):
    min_brier_improvement_vs_baseline: float = Field(ge=0, lt=1)
    max_brier_regression_vs_champion: float = Field(ge=0)
    max_auc_drop_vs_champion: float = Field(ge=0)
    max_slice_auc_drop: float = Field(ge=0)
    min_test_rows: PositiveInt


class RegistryConfig(_Section):
    model_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*\Z")
    experiment: str = Field(min_length=1)


class ReleaseConfig(_Section):
    reference_sample_rows: PositiveInt
```
and in `TrainingConfig` add after `evaluation`:
```python
    seed: int = Field(ge=0)
    lightgbm: LightGBMConfig
    risk_thresholds: RiskThresholds
    gate: GateConfig
    registry: RegistryConfig
    release: ReleaseConfig
```

`src/dbdelay/config.py` — in `Settings` after `stations_file`:
```python
    # Training config (relative to the working directory; containers set an absolute path).
    training_config_file: Path = Path("configs/training.yaml")
```

`configs/training.yaml` — append (and change the header comment to "Phase 3–4 training settings (… specs
2026-09-30 phase-3 and 2026-10-04 phase-4)"):
```yaml
seed: 42
lightgbm:                     # Phase 4 (spec 2026-10-04 §5)
  num_threads: 8              # fixed: deterministic=true needs a fixed thread count
  num_boost_round: 2000
  early_stopping_rounds: 50
  params:                     # fixed for every grid point
    objective: binary
    feature_fraction: 0.9
    bagging_fraction: 0.8
    bagging_freq: 1
  grid:                       # 2 × 2 × 2 = 8 combos, selected by valid binary_logloss
    num_leaves: [31, 127]
    learning_rate: [0.05, 0.1]
    min_data_in_leaf: [100, 500]
risk_thresholds:              # prd §5: Low < 0.20 ≤ Medium < 0.45 ≤ High
  medium: 0.20
  high: 0.45
gate:                         # architecture §5.4; challenger Brier must be strictly below champion's
  min_brier_improvement_vs_baseline: 0.05   # relative
  max_brier_regression_vs_champion: 0.00
  max_auc_drop_vs_champion: 0.005
  max_slice_auc_drop: 0.02                  # per train_type
  min_test_rows: 20000
registry:
  model_name: puenktlich-delay
  experiment: puenktlich-delay
release:
  reference_sample_rows: 50000              # rows in reference_sample.parquet (drift reference)
```

`tests/builders.py` — import the new config classes and `RiskThresholds`; add before `MARCH_CONFIG`:
```python
PHASE4_SECTIONS: dict[str, Any] = {
    "seed": 42,
    "lightgbm": LightGBMConfig(
        num_threads=2,
        num_boost_round=50,
        early_stopping_rounds=10,
        params={"objective": "binary"},
        grid=LightGBMGrid(num_leaves=(7,), learning_rate=(0.1,), min_data_in_leaf=(20,)),
    ),
    "risk_thresholds": RiskThresholds(medium=0.2, high=0.45),
    "gate": GateConfig(
        min_brier_improvement_vs_baseline=0.05,
        max_brier_regression_vs_champion=0.0,
        max_auc_drop_vs_champion=0.005,
        max_slice_auc_drop=0.02,
        min_test_rows=100,
    ),
    "registry": RegistryConfig(model_name="puenktlich-delay-test", experiment="puenktlich-delay-test"),
    "release": ReleaseConfig(reference_sample_rows=500),
}
```
and pass `**PHASE4_SECTIONS` into `MARCH_CONFIG = TrainingConfig(...)`.

`tests/unit/test_run_baseline.py::test_main_runs_end_to_end` — replace the inline YAML with:
```python
    config.write_text(yaml.safe_dump(MARCH_CONFIG.model_dump(mode="json")), encoding="utf-8")
```
(add `import yaml`).

- [ ] **Step 5: Run tests — expect PASS**

Run: `uv run pytest tests/unit -q && uv run mypy`
Expected: all pass, mypy clean.

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add pyproject.toml uv.lock src/dbdelay/features/spec.py src/dbdelay/training/config.py src/dbdelay/config.py configs/training.yaml tests/
git commit -m "feat(training): phase 4 config sections, risk thresholds, lightgbm + mlflow-skinny deps"
```

---

### Task 2: Signal test data + LightGBM training (`train.py`)

**Files:**
- Modify: `tests/builders.py` (signal silver + `SIGNAL_CONFIG`, `WEAK_LIGHTGBM`)
- Create: `src/dbdelay/training/train.py`
- Test: `tests/unit/test_train.py`

**Interfaces:**
- Consumes: `LightGBMConfig`, `build_features`, `fit_spec`, `CATEGORICAL_FEATURES`.
- Produces:
  - `GridScore(params: dict[str, float | int], best_iteration: int, valid_logloss: float)`
  - `TrainResult(booster, params: dict[str, Any], best_iteration: int, grid: list[GridScore])` with
    `.model_text() -> str` and `.valid_logloss -> float`
  - `train_lightgbm(train_x, train_y, valid_x, valid_y, cfg: LightGBMConfig, seed: int) -> TrainResult`
  - `booster_from_text(text: str) -> lgb.Booster`, `predict_raw(booster, features) -> NDArray[np.float64]`
  - `feature_importance(booster) -> dict[str, dict[str, float]]` (`{"gain": {...}, "split": {...}}`)
  - builders: `signal_day(day, n=120, *, seed=0)`, `put_signal_silver(store, root="", n=120)`,
    `SIGNAL_CONFIG: TrainingConfig`, `WEAK_LIGHTGBM: LightGBMConfig`, `signal_frames() -> dict[str, DataFrame]`

- [ ] **Step 1: Add builders** (`tests/builders.py`; add `import numpy as np`)

```python
SIGNAL_STATIONS = {"8000105": "Frankfurt (Main) Hbf", "8000261": "München Hbf"}


def signal_day(day: str, n: int = 120, *, seed: int = 0) -> pd.DataFrame:
    """``n`` labelled silver rows on UTC ``day`` with a learnable late signal.

    P(late) = 0.1, +0.6 if ``stop_index`` ≥ 6, +0.25 if the UTC hour is ≥ 12. The baseline's
    groups (station/type/hour/weekday) cannot see ``stop_index``; a 1-split stump sees only it.
    """
    rng = np.random.default_rng([seed, int(day.replace("-", ""))])
    frame = silver_frame(n, day)
    minutes = np.sort(rng.integers(0, 24 * 60, n))
    planned = [pd.Timestamp(f"{day} 00:00", tz="UTC") + timedelta(minutes=int(m)) for m in minutes]
    stop = rng.integers(1, 11, n)
    hours = np.array([p.hour for p in planned])
    late = rng.random(n) < 0.1 + np.where(stop >= 6, 0.6, 0.0) + np.where(hours >= 12, 0.25, 0.0)
    evas = np.array(list(SIGNAL_STATIONS))[rng.integers(0, 2, n)]
    rides = [f"{1000 + i}-{day[2:4]}{day[5:7]}{day[8:10]}0000" for i in range(n)]
    frame["eva"] = evas
    frame["station_name"] = [SIGNAL_STATIONS[e] for e in evas]
    frame["train_type"] = np.where(rng.random(n) < 0.5, "RE", "ICE")
    frame["ride_id"] = rides
    frame["stop_index"] = pd.Series(stop, dtype="int16")
    frame["event_id"] = [
        make_event_id(e, r, p) for e, r, p in zip(evas, rides, planned, strict=True)
    ]
    frame["planned_departure_utc"] = pd.Series(planned, dtype="datetime64[us, UTC]")
    frame["changed_departure_utc"] = pd.Series(
        [p + timedelta(minutes=10 if lt else 0) for p, lt in zip(planned, late, strict=True)],
        dtype="datetime64[us, UTC]",
    )
    frame["delay_min"] = pd.Series(np.where(late, 10, 0), dtype="Int16")
    frame["is_late"] = pd.Series(late, dtype="boolean")
    return frame


def put_signal_silver(store: ObjectStore, root: str = "", n: int = 120) -> None:
    """March 2026 of ``signal_day`` rows plus a gap-free quality report."""
    days = [signal_day(f"2026-03-{d:02d}", n) for d in range(1, 32)]
    write_silver_month(pd.concat(days, ignore_index=True), store, "2026-03", root)
    store.put_bytes(quality_key("2026-03", root), quality_report("2026-03").model_dump_json().encode())
```
After `MARCH_CONFIG`:
```python
SIGNAL_CONFIG = MARCH_CONFIG.model_copy(
    update={
        "baseline": BaselineConfig(min_count=5),
        "evaluation": EvaluationConfig(ece_bins=10, slice_min_rows=50),
    }
)
# One stump: learns stop_index only → beats the baseline, loses to SIGNAL_CONFIG's model.
WEAK_LIGHTGBM = SIGNAL_CONFIG.lightgbm.model_copy(
    update={
        "num_boost_round": 1,
        "grid": LightGBMGrid(num_leaves=(2,), learning_rate=(0.1,), min_data_in_leaf=(20,)),
    }
)


def signal_frames(n: int = 120) -> dict[str, pd.DataFrame]:
    """Signal silver rows of March 2026 split like ``SIGNAL_CONFIG`` (no storage)."""
    rows = pd.concat([signal_day(f"2026-03-{d:02d}", n) for d in range(1, 32)], ignore_index=True)
    day = rows["planned_departure_utc"].dt.day
    return {
        "train": rows[day <= 17].reset_index(drop=True),
        "valid": rows[(day >= 18) & (day <= 24)].reset_index(drop=True),
        "test": rows[day >= 25].reset_index(drop=True),
    }
```

- [ ] **Step 2: Write failing tests** — `tests/unit/test_train.py`

```python
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dbdelay.data.stations import load_stations
from dbdelay.errors import DataValidationError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS, fit_spec
from dbdelay.training.config import LightGBMGrid
from dbdelay.training.train import (
    booster_from_text,
    feature_importance,
    predict_raw,
    train_lightgbm,
)
from tests.builders import SIGNAL_CONFIG, WEAK_LIGHTGBM, signal_frames

REPO = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO / "configs" / "stations.yaml")


@pytest.fixture(scope="module")
def data() -> dict[str, tuple[pd.DataFrame, np.ndarray]]:
    frames = signal_frames()
    spec = fit_spec(frames["train"], STATIONS, 1)
    return {
        name: (build_features(frame, spec), frame["is_late"].to_numpy(dtype=bool))
        for name, frame in frames.items()
    }


def _train(data, cfg=SIGNAL_CONFIG.lightgbm):  # type: ignore[no-untyped-def]
    (tx, ty), (vx, vy) = data["train"], data["valid"]
    return train_lightgbm(tx, ty, vx, vy, cfg, seed=42)


def test_training_is_deterministic(data) -> None:  # type: ignore[no-untyped-def]
    assert _train(data).model_text() == _train(data).model_text()


def test_model_text_round_trips_predictions(data) -> None:  # type: ignore[no-untyped-def]
    result = _train(data)
    test_x = data["test"][0]
    restored = booster_from_text(result.model_text())
    np.testing.assert_array_equal(
        predict_raw(restored, test_x), predict_raw(booster_from_text(result.model_text()), test_x)
    )
    assert restored.num_trees() == result.best_iteration


def test_grid_picks_lowest_valid_logloss(data) -> None:  # type: ignore[no-untyped-def]
    grid = LightGBMGrid(num_leaves=(2, 7), learning_rate=(0.1,), min_data_in_leaf=(20,))
    result = _train(data, SIGNAL_CONFIG.lightgbm.model_copy(update={"grid": grid}))
    assert len(result.grid) == 2
    best = min(result.grid, key=lambda s: s.valid_logloss)
    assert result.params["num_leaves"] == best.params["num_leaves"]
    assert result.valid_logloss == best.valid_logloss


def test_model_learns_the_signal(data) -> None:  # type: ignore[no-untyped-def]
    booster = booster_from_text(_train(data).model_text())
    test_x, test_y = data["test"]
    p = predict_raw(booster, test_x)
    assert p[test_y].mean() > p[~test_y].mean() + 0.2
    gain = feature_importance(booster)["gain"]
    assert set(gain) == set(FEATURE_COLUMNS)
    assert max(gain, key=gain.__getitem__) == "stop_index"


def test_weak_config_trains_one_stump(data) -> None:  # type: ignore[no-untyped-def]
    result = _train(data, WEAK_LIGHTGBM)
    assert booster_from_text(result.model_text()).num_trees() == 1


def test_one_class_train_labels_raise(data) -> None:  # type: ignore[no-untyped-def]
    (tx, ty), (vx, vy) = data["train"], data["valid"]
    with pytest.raises(DataValidationError):
        train_lightgbm(tx, np.zeros_like(ty), vx, vy, SIGNAL_CONFIG.lightgbm, seed=42)
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError: dbdelay.training.train`)

Run: `uv run pytest tests/unit/test_train.py -q`

- [ ] **Step 4: Implement `src/dbdelay/training/train.py`**

```python
"""LightGBM training: small grid, early stopping on valid, native categoricals (architecture §5.2)."""

import itertools
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import BaseModel

from dbdelay.errors import DataValidationError
from dbdelay.features.spec import CATEGORICAL_FEATURES
from dbdelay.training.config import LightGBMConfig

GRID_KEYS: tuple[str, ...] = ("num_leaves", "learning_rate", "min_data_in_leaf")
METRIC = "binary_logloss"


class GridScore(BaseModel):
    params: dict[str, float | int]
    best_iteration: int
    valid_logloss: float


@dataclass(frozen=True)
class TrainResult:
    booster: lgb.Booster
    params: dict[str, Any]
    best_iteration: int
    grid: list[GridScore]

    @property
    def valid_logloss(self) -> float:
        return min(score.valid_logloss for score in self.grid)

    def model_text(self) -> str:
        """`model.txt`: the booster cut at the best iteration (LightGBM text format, no pickle)."""
        return str(self.booster.model_to_string(num_iteration=self.best_iteration))


def base_params(cfg: LightGBMConfig, seed: int) -> dict[str, Any]:
    """Params shared by every grid point; fixed threads + deterministic for reproducibility."""
    return {
        **cfg.params,
        "metric": METRIC,
        "seed": seed,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": cfg.num_threads,
        "verbose": -1,
    }


def grid_points(cfg: LightGBMConfig) -> list[dict[str, float | int]]:
    grid = cfg.grid
    return [
        dict(zip(GRID_KEYS, values, strict=True))
        for values in itertools.product(grid.num_leaves, grid.learning_rate, grid.min_data_in_leaf)
    ]


def _dataset(
    features: pd.DataFrame, labels: NDArray[np.bool_], reference: lgb.Dataset | None = None
) -> lgb.Dataset:
    return lgb.Dataset(
        features,
        label=labels.astype(np.int8),
        categorical_feature=list(CATEGORICAL_FEATURES),
        reference=reference,
        free_raw_data=False,
    )


def _check_labels(name: str, features: pd.DataFrame, labels: NDArray[np.bool_]) -> None:
    if features.empty or len(features) != len(labels):
        raise DataValidationError(f"{name} needs rows and one label per row")
    if labels.all() or not labels.any():
        raise DataValidationError(f"{name} labels need both classes")


def train_lightgbm(  # noqa: PLR0913 - two splits × (features, labels) + config + seed
    train_x: pd.DataFrame,
    train_y: NDArray[np.bool_],
    valid_x: pd.DataFrame,
    valid_y: NDArray[np.bool_],
    cfg: LightGBMConfig,
    seed: int,
) -> TrainResult:
    """Train one booster per grid point; keep the one with the lowest valid log loss.

    A fresh Dataset per grid point: `min_data_in_leaf` affects Dataset construction.

    Raises:
        DataValidationError: empty split, label/row mismatch, or a single label class.
    """
    _check_labels("train", train_x, np.asarray(train_y, dtype=bool))
    _check_labels("valid", valid_x, np.asarray(valid_y, dtype=bool))
    scores: list[GridScore] = []
    best: TrainResult | None = None
    for point in grid_points(cfg):
        params = {**base_params(cfg, seed), **point}
        train_set = _dataset(train_x, np.asarray(train_y, dtype=bool))
        valid_set = _dataset(valid_x, np.asarray(valid_y, dtype=bool), reference=train_set)
        booster = lgb.train(
            params,
            train_set,
            num_boost_round=cfg.num_boost_round,
            valid_sets=[valid_set],
            valid_names=["valid"],
            callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)],
        )
        iteration = int(booster.best_iteration) or int(booster.current_iteration())
        score = GridScore(
            params=point,
            best_iteration=iteration,
            valid_logloss=float(booster.best_score["valid"][METRIC]),
        )
        scores.append(score)
        if best is None or score.valid_logloss < best.valid_logloss:
            best = TrainResult(booster=booster, params=params, best_iteration=iteration, grid=[score])
    assert best is not None  # grid has ≥ 1 point (config validation)  # noqa: S101
    return TrainResult(
        booster=best.booster, params=best.params, best_iteration=best.best_iteration, grid=scores
    )


def booster_from_text(text: str) -> lgb.Booster:
    return lgb.Booster(model_str=text)


def predict_raw(booster: lgb.Booster, features: pd.DataFrame) -> NDArray[np.float64]:
    """Uncalibrated probabilities from a booster loaded with `booster_from_text`."""
    return np.asarray(booster.predict(features), dtype=np.float64)


def feature_importance(booster: lgb.Booster) -> dict[str, dict[str, float]]:
    names = [str(name) for name in booster.feature_name()]
    return {
        kind: dict(
            zip(names, (float(v) for v in booster.feature_importance(kind)), strict=True)
        )
        for kind in ("gain", "split")
    }
```
Note: inside `train_lightgbm` the `best` temporary holds a one-element `grid`; the returned result carries all
scores — `valid_logloss` of the returned object is therefore the minimum over the grid (= the chosen point).

- [ ] **Step 5: Run — expect PASS**

Run: `uv run pytest tests/unit/test_train.py -q && uv run mypy`
If `test_model_learns_the_signal` picks another top feature, inspect gains before changing the builder — the
signal must stay `stop_index`-dominated (the integration scenario in Task 10 relies on it).

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training/train.py tests/builders.py tests/unit/test_train.py
git commit -m "feat(training): lightgbm grid training with early stopping and native categoricals"
```

---

### Task 3: Isotonic calibration (`calibrate.py`)

**Files:**
- Create: `src/dbdelay/training/calibrate.py`
- Test: `tests/unit/test_calibrate.py`

**Interfaces:**
- Produces: `IsotonicCalibrator` (pydantic, frozen): fields `schema_version: Literal[1]`,
  `method: Literal["isotonic"]`, `x: tuple[float, ...]`, `y: tuple[float, ...]`;
  `IsotonicCalibrator.fit(raw: ArrayLike, labels: ArrayLike) -> IsotonicCalibrator`;
  `.apply(raw: ArrayLike) -> NDArray[np.float64]`; `.to_json() -> str`; `IsotonicCalibrator.from_json(text)`.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_calibrate.py`

```python
import numpy as np
import pytest
from sklearn.isotonic import IsotonicRegression

from dbdelay.errors import DataValidationError
from dbdelay.training.calibrate import IsotonicCalibrator


def _data(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    raw = rng.random(2000)
    labels = rng.random(2000) < raw**2
    return raw, labels


def test_apply_matches_sklearn_including_out_of_range() -> None:
    raw, labels = _data()
    calibrator = IsotonicCalibrator.fit(raw, labels)
    reference = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, labels)
    probe = np.concatenate([np.random.default_rng(1).random(500), [-1.0, 0.0, 1.0, 2.0]])
    np.testing.assert_allclose(calibrator.apply(probe), reference.predict(probe), atol=1e-12)


def test_output_is_monotone_and_in_unit_interval() -> None:
    calibrator = IsotonicCalibrator.fit(*_data())
    out = calibrator.apply(np.linspace(-0.5, 1.5, 1001))
    assert np.all(np.diff(out) >= 0)
    assert out.min() >= 0.0
    assert out.max() <= 1.0


def test_json_round_trip() -> None:
    calibrator = IsotonicCalibrator.fit(*_data())
    restored = IsotonicCalibrator.from_json(calibrator.to_json())
    assert restored == calibrator
    assert '"method": "isotonic"' in calibrator.to_json()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"schema_version": 1, "method": "isotonic", "x": [0.2, 0.1], "y": [0.1, 0.2]}',
        '{"schema_version": 1, "method": "isotonic", "x": [0.1, 0.2], "y": [0.3, 0.2]}',
        '{"schema_version": 1, "method": "isotonic", "x": [0.1], "y": [0.1, 0.2]}',
        '{"schema_version": 1, "method": "isotonic", "x": [0.1], "y": [1.5]}',
    ],
)
def test_bad_json_raises(text: str) -> None:
    with pytest.raises(DataValidationError):
        IsotonicCalibrator.from_json(text)


def test_fit_needs_matching_non_empty_inputs() -> None:
    with pytest.raises(DataValidationError):
        IsotonicCalibrator.fit(np.array([]), np.array([]))
    with pytest.raises(DataValidationError):
        IsotonicCalibrator.fit(np.array([0.1, 0.2]), np.array([True]))
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`)

Run: `uv run pytest tests/unit/test_calibrate.py -q`

- [ ] **Step 3: Implement `src/dbdelay/training/calibrate.py`**

```python
"""Isotonic calibration fitted on valid; applied with numpy.interp (serving needs no sklearn)."""

from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sklearn.isotonic import IsotonicRegression

from dbdelay.errors import DataValidationError


class IsotonicCalibrator(BaseModel):
    """`calibrator.json`: piecewise-linear map from raw score to calibrated ``p_late``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    method: Literal["isotonic"] = "isotonic"
    x: tuple[float, ...] = Field(min_length=1)
    y: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _monotone(self) -> "IsotonicCalibrator":
        x, y = np.asarray(self.x), np.asarray(self.y)
        if len(x) != len(y):
            raise ValueError("x and y must have the same length")
        if np.any(np.diff(x) <= 0):
            raise ValueError("x must be strictly increasing")
        if np.any(np.diff(y) < 0) or y.min() < 0 or y.max() > 1:
            raise ValueError("y must be non-decreasing within [0, 1]")
        return self

    @classmethod
    def fit(cls, raw: ArrayLike, labels: ArrayLike) -> "IsotonicCalibrator":
        """Fit on raw scores vs boolean labels.

        Raises:
            DataValidationError: if inputs are empty or of different length.
        """
        scores = np.asarray(raw, dtype=float)
        targets = np.asarray(labels, dtype=float)
        if scores.size == 0 or scores.shape != targets.shape:
            raise DataValidationError("calibration needs one label per raw score")
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(scores, targets)
        return cls(
            x=tuple(float(v) for v in iso.X_thresholds_),
            y=tuple(float(v) for v in iso.y_thresholds_),
        )

    def apply(self, raw: ArrayLike) -> NDArray[np.float64]:
        """Calibrated probabilities; inputs outside the fitted range take the end values."""
        out = np.interp(np.asarray(raw, dtype=float), self.x, self.y)
        return np.clip(out, 0.0, 1.0).astype(np.float64)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str | bytes) -> "IsotonicCalibrator":
        """Parse `calibrator.json`.

        Raises:
            DataValidationError: if the JSON does not match the calibrator model.
        """
        try:
            return cls.model_validate_json(text)
        except ValidationError as exc:
            raise DataValidationError(f"invalid calibrator: {exc.error_count()} errors") from None
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_calibrate.py -q && uv run mypy`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training/calibrate.py tests/unit/test_calibrate.py
git commit -m "feat(training): isotonic calibrator exported as json and applied with numpy.interp"
```

---

### Task 4: Training report + promotion gate (`gate.py`)

**Files:**
- Modify: `src/dbdelay/training/evaluate.py` (add `TrainingReport`)
- Create: `src/dbdelay/training/gate.py`
- Test: `tests/unit/test_gate.py`

**Interfaces:**
- Consumes: `SplitReport`, `Metrics` (evaluate.py), `GateConfig`.
- Produces:
  - `TrainingReport(snapshot_id, spec_hash, git_sha, trained_at: datetime, train_start: date, train_end: date,
    test_rows: int, challenger_valid: SplitReport, challenger_test: SplitReport, baseline_test: SplitReport,
    champion_version: str | None = None, champion_test: SplitReport | None = None)` — `metrics.json` of a bundle
  - `Check(name: str, passed: bool, value: float | None, limit: float | None, detail: str = "")`
  - `GateDecision(passed: bool, champion_version: str | None, checks: list[Check])` with
    `.failed -> list[str]` (names of failed checks)
  - `evaluate_gate(report: TrainingReport, cfg: GateConfig) -> GateDecision`
  - `SLICE_COLUMN = "train_type"`

- [ ] **Step 1: Add `TrainingReport` to `src/dbdelay/training/evaluate.py`**

```python
from datetime import date, datetime  # add to imports; also `model_validator` from pydantic


class TrainingReport(BaseModel):
    """`metrics.json` in a model bundle: challenger, baseline and champion on the same test rows."""

    snapshot_id: str
    spec_hash: str
    git_sha: str
    trained_at: datetime
    train_start: date
    train_end: date
    test_rows: int
    challenger_valid: SplitReport
    challenger_test: SplitReport
    baseline_test: SplitReport
    champion_version: str | None = None
    champion_test: SplitReport | None = None

    @model_validator(mode="after")
    def _champion_pair(self) -> "TrainingReport":
        if (self.champion_version is None) != (self.champion_test is None):
            raise ValueError("champion_version and champion_test come together")
        return self
```

- [ ] **Step 2: Write failing tests** — `tests/unit/test_gate.py`

```python
from datetime import UTC, date, datetime

import pytest

from dbdelay.training.config import GateConfig
from dbdelay.training.evaluate import Metrics, SplitReport, TrainingReport
from dbdelay.training.gate import evaluate_gate

GATE = GateConfig(
    min_brier_improvement_vs_baseline=0.05,
    max_brier_regression_vs_champion=0.0,
    max_auc_drop_vs_champion=0.005,
    max_slice_auc_drop=0.02,
    min_test_rows=1000,
)


def split(brier: float | None, auc: float | None, slices: dict[str, float | None]) -> SplitReport:
    return SplitReport(
        overall=Metrics(n=2000, base_rate=0.3, brier=brier, roc_auc=auc),
        calibration=[],
        slices={
            "train_type": {
                k: Metrics(n=500, base_rate=0.3, roc_auc=v) for k, v in slices.items()
            }
        },
    )


def report(
    challenger: SplitReport,
    baseline: SplitReport,
    champion: SplitReport | None = None,
    test_rows: int = 2000,
) -> TrainingReport:
    return TrainingReport(
        snapshot_id="2026-08-31_abcdef12",
        spec_hash="0" * 64,
        git_sha="abc1234",
        trained_at=datetime(2026, 10, 4, tzinfo=UTC),
        train_start=date(2025, 12, 1),
        train_end=date(2026, 8, 3),
        test_rows=test_rows,
        challenger_valid=challenger,
        challenger_test=challenger,
        baseline_test=baseline,
        champion_version="3" if champion else None,
        champion_test=champion,
    )


BASE = split(0.1520, 0.77, {"ICE": 0.70, "RE": 0.75})
GOOD = split(0.1400, 0.80, {"ICE": 0.72, "RE": 0.78})


def names(decision) -> dict[str, bool]:  # type: ignore[no-untyped-def]
    return {c.name: c.passed for c in decision.checks}


def test_first_run_passes_with_baseline_and_slice_checks_only() -> None:
    decision = evaluate_gate(report(GOOD, BASE), GATE)
    assert decision.passed
    assert names(decision) == {"min_test_rows": True, "beats_baseline": True, "slice_auc": True}


def test_baseline_margin_is_relative() -> None:
    limit = 0.1520 * 0.95
    just_over = split(limit + 1e-6, 0.80, {"ICE": 0.72})
    exactly = split(limit, 0.80, {"ICE": 0.72})
    assert not evaluate_gate(report(just_over, BASE), GATE).passed
    assert evaluate_gate(report(exactly, BASE), GATE).passed


def test_tie_with_champion_is_rejected() -> None:
    decision = evaluate_gate(report(GOOD, BASE, champion=GOOD), GATE)
    assert not decision.passed
    assert decision.failed == ["brier_vs_champion"]
    assert decision.champion_version == "3"


def test_better_than_champion_passes() -> None:
    champion = split(0.1410, 0.80, {"ICE": 0.72, "RE": 0.78})
    assert evaluate_gate(report(GOOD, BASE, champion=champion), GATE).passed


def test_auc_drop_vs_champion_fails() -> None:
    champion = split(0.1450, 0.81, {"ICE": 0.72, "RE": 0.78})
    decision = evaluate_gate(report(GOOD, BASE, champion=champion), GATE)
    assert decision.failed == ["auc_vs_champion"]


def test_slice_regression_fails_and_names_slice() -> None:
    worse_ice = split(0.1400, 0.80, {"ICE": 0.67, "RE": 0.78})
    decision = evaluate_gate(report(worse_ice, BASE), GATE)
    assert decision.failed == ["slice_auc"]
    assert "ICE" in decision.checks[-1].detail


def test_null_or_unknown_slices_are_skipped_not_failed() -> None:
    challenger = split(0.1400, 0.80, {"ICE": None, "RE": 0.78, "S": 0.6})
    decision = evaluate_gate(report(challenger, BASE), GATE)
    assert decision.passed
    assert "skipped" in decision.checks[-1].detail
    assert "ICE" in decision.checks[-1].detail
    assert "S" in decision.checks[-1].detail


def test_too_few_test_rows_fail() -> None:
    assert evaluate_gate(report(GOOD, BASE, test_rows=999), GATE).failed == ["min_test_rows"]


def test_missing_overall_metric_fails_closed() -> None:
    decision = evaluate_gate(report(split(None, None, {}), BASE), GATE)
    assert "beats_baseline" in decision.failed


def test_champion_pair_must_be_complete() -> None:
    with pytest.raises(ValueError, match="come together"):
        TrainingReport.model_validate(
            report(GOOD, BASE).model_dump() | {"champion_version": "1"}
        )
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError: dbdelay.training.gate`)

Run: `uv run pytest tests/unit/test_gate.py -q`

- [ ] **Step 4: Implement `src/dbdelay/training/gate.py`**

```python
"""Promotion gate: challenger vs baseline and champion on the same test rows (architecture §5.4)."""

from pydantic import BaseModel

from dbdelay.training.config import GateConfig
from dbdelay.training.evaluate import SplitReport, TrainingReport

SLICE_COLUMN = "train_type"


class Check(BaseModel):
    name: str
    passed: bool
    value: float | None
    limit: float | None
    detail: str = ""


class GateDecision(BaseModel):
    """`gate.json`."""

    passed: bool
    champion_version: str | None
    checks: list[Check]

    @property
    def failed(self) -> list[str]:
        return [check.name for check in self.checks if not check.passed]


def _missing(name: str) -> Check:
    return Check(name=name, passed=False, value=None, limit=None, detail="metric missing")


def _beats_baseline(challenger: SplitReport, baseline: SplitReport, cfg: GateConfig) -> Check:
    mine, theirs = challenger.overall.brier, baseline.overall.brier
    if mine is None or theirs is None:
        return _missing("beats_baseline")
    limit = theirs * (1 - cfg.min_brier_improvement_vs_baseline)
    return Check(name="beats_baseline", passed=mine <= limit, value=mine, limit=limit)


def _brier_vs_champion(challenger: SplitReport, champion: SplitReport, cfg: GateConfig) -> Check:
    mine, theirs = challenger.overall.brier, champion.overall.brier
    if mine is None or theirs is None:
        return _missing("brier_vs_champion")
    limit = theirs + cfg.max_brier_regression_vs_champion
    # Strict: an equal Brier is "no improvement" → rejected (owner decision 2026-10-04).
    return Check(name="brier_vs_champion", passed=mine < limit, value=mine, limit=limit)


def _auc_vs_champion(challenger: SplitReport, champion: SplitReport, cfg: GateConfig) -> Check:
    mine, theirs = challenger.overall.roc_auc, champion.overall.roc_auc
    if mine is None or theirs is None:
        return _missing("auc_vs_champion")
    limit = theirs - cfg.max_auc_drop_vs_champion
    return Check(name="auc_vs_champion", passed=mine >= limit, value=mine, limit=limit)


def _slice_auc(challenger: SplitReport, reference: SplitReport, cfg: GateConfig) -> Check:
    ours = challenger.slices.get(SLICE_COLUMN, {})
    theirs = reference.slices.get(SLICE_COLUMN, {})
    failed: list[str] = []
    skipped: list[str] = []
    worst: float | None = None
    for key in sorted(ours):
        mine = ours[key].roc_auc
        other = theirs[key].roc_auc if key in theirs else None
        if mine is None or other is None:
            skipped.append(key)
            continue
        drop = other - mine
        worst = drop if worst is None else max(worst, drop)
        if drop > cfg.max_slice_auc_drop:
            failed.append(f"{key} ({drop:.4f})")
    detail = "; ".join(
        part
        for part in (
            f"failed: {', '.join(failed)}" if failed else "",
            f"skipped: {', '.join(skipped)}" if skipped else "",
        )
        if part
    )
    return Check(
        name="slice_auc",
        passed=not failed,
        value=worst,
        limit=cfg.max_slice_auc_drop,
        detail=detail,
    )


def evaluate_gate(report: TrainingReport, cfg: GateConfig) -> GateDecision:
    """All gate checks; passes only if every check passes.

    Without a champion only the row-count, baseline and slice (vs baseline) checks apply.
    """
    challenger = report.challenger_test
    checks = [
        Check(
            name="min_test_rows",
            passed=report.test_rows >= cfg.min_test_rows,
            value=float(report.test_rows),
            limit=float(cfg.min_test_rows),
        ),
        _beats_baseline(challenger, report.baseline_test, cfg),
    ]
    reference = report.baseline_test
    if report.champion_test is not None:
        checks.append(_brier_vs_champion(challenger, report.champion_test, cfg))
        checks.append(_auc_vs_champion(challenger, report.champion_test, cfg))
        reference = report.champion_test
    checks.append(_slice_auc(challenger, reference, cfg))
    return GateDecision(
        passed=all(check.passed for check in checks),
        champion_version=report.champion_version,
        checks=checks,
    )
```

- [ ] **Step 5: Run — expect PASS**: `uv run pytest tests/unit/test_gate.py tests/unit/test_evaluate.py -q && uv run mypy`

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training/evaluate.py src/dbdelay/training/gate.py tests/unit/test_gate.py
git commit -m "feat(training): training report and promotion gate (tie with champion rejects)"
```

---

### Task 5: Tracking protocol, MLflow tracker, fake tracker

**Files:**
- Create: `src/dbdelay/training/tracking.py`
- Create: `tests/fakes.py`
- Test: `tests/unit/test_tracking.py` (fake contract + MLflow wrapper error mapping)

**Interfaces:**
- Produces `Tracker` protocol (all methods below), `MlflowTracker(tracking_uri: str)`, `FakeTracker` (tests):
  ```python
  start_run(experiment: str, tags: Mapping[str, str]) -> str
  log_params(run_id: str, params: Mapping[str, object]) -> None
  log_metrics(run_id: str, metrics: Mapping[str, float]) -> None
  set_tags(run_id: str, tags: Mapping[str, str]) -> None
  log_bytes(run_id: str, path: str, data: bytes) -> None        # path like "bundle/model.txt"
  load_bytes(run_id: str, path: str) -> bytes
  log_snapshot(run_id: str, snapshot_id: str, digest: str, source: str) -> None
  finish_run(run_id: str, *, failed: bool = False) -> None
  register_version(model_name: str, run_id: str, artifact_path: str) -> str
  set_alias(model_name: str, alias: str, version: str) -> None
  get_alias(model_name: str, alias: str) -> str | None
  set_version_tags(model_name: str, version: str, tags: Mapping[str, str]) -> None
  ```
  `FakeTracker` exposes dicts `runs`, `files`, `aliases`, `version_tags`, `status` for assertions.

- [ ] **Step 1: Write `tests/fakes.py`**

```python
"""In-memory stand-ins for external services (unit tests only)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from dbdelay.errors import NotFoundError


@dataclass
class FakeTracker:
    """Implements `dbdelay.training.tracking.Tracker` in memory."""

    runs: dict[str, dict[str, object]] = field(default_factory=dict)
    files: dict[tuple[str, str], bytes] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    versions: dict[str, list[str]] = field(default_factory=dict)  # model → run ids by version
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    version_tags: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def start_run(self, experiment: str, tags: Mapping[str, str]) -> str:
        run_id = f"run{len(self.runs) + 1}"
        self.runs[run_id] = {
            "experiment": experiment, "tags": dict(tags), "params": {}, "metrics": {}, "inputs": []
        }
        self.status[run_id] = "RUNNING"
        return run_id

    def _run(self, run_id: str) -> dict[str, object]:
        if run_id not in self.runs:
            raise NotFoundError(f"run {run_id} not found")
        return self.runs[run_id]

    def log_params(self, run_id: str, params: Mapping[str, object]) -> None:
        self._run(run_id)["params"].update({k: str(v) for k, v in params.items()})  # type: ignore[attr-defined]

    def log_metrics(self, run_id: str, metrics: Mapping[str, float]) -> None:
        self._run(run_id)["metrics"].update(dict(metrics))  # type: ignore[attr-defined]

    def set_tags(self, run_id: str, tags: Mapping[str, str]) -> None:
        self._run(run_id)["tags"].update(dict(tags))  # type: ignore[attr-defined]

    def log_bytes(self, run_id: str, path: str, data: bytes) -> None:
        self._run(run_id)
        self.files[(run_id, path)] = data

    def load_bytes(self, run_id: str, path: str) -> bytes:
        try:
            return self.files[(run_id, path)]
        except KeyError:
            raise NotFoundError(f"{run_id}/{path} not found") from None

    def log_snapshot(self, run_id: str, snapshot_id: str, digest: str, source: str) -> None:
        self._run(run_id)["inputs"].append((snapshot_id, digest, source))  # type: ignore[attr-defined]

    def finish_run(self, run_id: str, *, failed: bool = False) -> None:
        self._run(run_id)
        self.status[run_id] = "FAILED" if failed else "FINISHED"

    def register_version(self, model_name: str, run_id: str, artifact_path: str) -> str:
        self._run(run_id)
        self.versions.setdefault(model_name, []).append(run_id)
        return str(len(self.versions[model_name]))

    def set_alias(self, model_name: str, alias: str, version: str) -> None:
        self.aliases[(model_name, alias)] = version

    def get_alias(self, model_name: str, alias: str) -> str | None:
        return self.aliases.get((model_name, alias))

    def set_version_tags(self, model_name: str, version: str, tags: Mapping[str, str]) -> None:
        self.version_tags.setdefault((model_name, version), {}).update(dict(tags))
```

- [ ] **Step 2: Write failing tests** — `tests/unit/test_tracking.py`

```python
from typing import Any

import pytest
from mlflow.exceptions import MlflowException

from dbdelay.errors import ExternalServiceError
from dbdelay.training import tracking
from dbdelay.training.tracking import MlflowTracker, Tracker
from tests.fakes import FakeTracker


def test_fake_tracker_satisfies_protocol() -> None:
    tracker: Tracker = FakeTracker()
    run_id = tracker.start_run("exp", {"a": "b"})
    tracker.log_bytes(run_id, "bundle/model.txt", b"x")
    assert tracker.load_bytes(run_id, "bundle/model.txt") == b"x"
    assert tracker.register_version("m", run_id, "bundle") == "1"


class _BrokenClient:
    def __getattr__(self, name: str) -> Any:
        def fail(*args: object, **kwargs: object) -> None:
            raise MlflowException("server down")

        return fail


def test_mlflow_errors_become_external_service_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = MlflowTracker("http://127.0.0.1:9")
    monkeypatch.setattr(tracker, "_client", _BrokenClient())
    with pytest.raises(ExternalServiceError, match="set_tags"):
        tracker.set_tags("run", {"a": "b"})


def test_missing_alias_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoAlias:
        def get_model_version_by_alias(self, name: str, alias: str) -> None:
            raise MlflowException("nope", error_code="RESOURCE_DOES_NOT_EXIST")

    tracker = MlflowTracker("http://127.0.0.1:9")
    monkeypatch.setattr(tracker, "_client", _NoAlias())
    assert tracker.get_alias("m", "champion") is None


def test_digest_is_cut_to_mlflow_limit() -> None:
    assert len(tracking.dataset_digest("a" * 64)) == tracking.MAX_DIGEST
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError: dbdelay.training.tracking`)

Run: `uv run pytest tests/unit/test_tracking.py -q`

- [ ] **Step 4: Implement `src/dbdelay/training/tracking.py`**

```python
"""Experiment tracking and model registry behind a small protocol.

`MlflowTracker` talks to the MLflow server (artifacts go through its proxy, so clients need no
storage credentials); unit tests use an in-memory fake with the same methods.
"""

import json
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, TypeVar

import mlflow.artifacts
from mlflow import MlflowClient
from mlflow.entities import Dataset, DatasetInput, InputTag, Metric, Param, RunTag
from mlflow.exceptions import MlflowException

from dbdelay.errors import ExternalServiceError

T = TypeVar("T")
MAX_DIGEST = 36  # MLflow's limit for dataset digests
_MISSING_CODES = frozenset({"RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"})


class Tracker(Protocol):
    def start_run(self, experiment: str, tags: Mapping[str, str]) -> str: ...
    def log_params(self, run_id: str, params: Mapping[str, object]) -> None: ...
    def log_metrics(self, run_id: str, metrics: Mapping[str, float]) -> None: ...
    def set_tags(self, run_id: str, tags: Mapping[str, str]) -> None: ...
    def log_bytes(self, run_id: str, path: str, data: bytes) -> None: ...
    def load_bytes(self, run_id: str, path: str) -> bytes: ...
    def log_snapshot(self, run_id: str, snapshot_id: str, digest: str, source: str) -> None: ...
    def finish_run(self, run_id: str, *, failed: bool = False) -> None: ...
    def register_version(self, model_name: str, run_id: str, artifact_path: str) -> str: ...
    def set_alias(self, model_name: str, alias: str, version: str) -> None: ...
    def get_alias(self, model_name: str, alias: str) -> str | None: ...
    def set_version_tags(self, model_name: str, version: str, tags: Mapping[str, str]) -> None: ...


def dataset_digest(content_hash: str) -> str:
    return content_hash[:MAX_DIGEST]


class MlflowTracker:
    """`Tracker` backed by an MLflow tracking + registry server."""

    def __init__(self, tracking_uri: str) -> None:
        self._uri = tracking_uri
        self._client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)

    def _call(self, what: str, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except MlflowException as exc:
            raise ExternalServiceError(f"mlflow {what} failed") from exc

    def start_run(self, experiment: str, tags: Mapping[str, str]) -> str:
        def run() -> str:
            found = self._client.get_experiment_by_name(experiment)
            experiment_id = (
                found.experiment_id if found else self._client.create_experiment(experiment)
            )
            return str(self._client.create_run(experiment_id, tags=dict(tags)).info.run_id)

        return self._call("start_run", run)

    def log_params(self, run_id: str, params: Mapping[str, object]) -> None:
        batch = [Param(key, str(value)) for key, value in sorted(params.items())]
        self._call("log_params", lambda: self._client.log_batch(run_id, params=batch))

    def log_metrics(self, run_id: str, metrics: Mapping[str, float]) -> None:
        now = int(time.time() * 1000)
        batch = [Metric(key, float(value), now, 0) for key, value in sorted(metrics.items())]
        self._call("log_metrics", lambda: self._client.log_batch(run_id, metrics=batch))

    def set_tags(self, run_id: str, tags: Mapping[str, str]) -> None:
        batch = [RunTag(key, value) for key, value in sorted(tags.items())]
        self._call("set_tags", lambda: self._client.log_batch(run_id, tags=batch))

    def log_bytes(self, run_id: str, path: str, data: bytes) -> None:
        directory, _, name = path.rpartition("/")
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / name
            local.write_bytes(data)
            self._call(
                "log_artifact",
                lambda: self._client.log_artifact(run_id, str(local), directory or None),
            )

    def load_bytes(self, run_id: str, path: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            local = self._call(
                "download_artifacts",
                lambda: mlflow.artifacts.download_artifacts(
                    run_id=run_id, artifact_path=path, dst_path=tmp, tracking_uri=self._uri
                ),
            )
            return Path(local).read_bytes()

    def log_snapshot(self, run_id: str, snapshot_id: str, digest: str, source: str) -> None:
        dataset = Dataset(
            name=snapshot_id,
            digest=dataset_digest(digest),
            source_type="s3",
            source=json.dumps({"uri": source}),
        )
        tag = InputTag("mlflow.data.context", "training")
        self._call(
            "log_inputs",
            lambda: self._client.log_inputs(run_id, [DatasetInput(dataset, [tag])]),
        )

    def finish_run(self, run_id: str, *, failed: bool = False) -> None:
        status = "FAILED" if failed else "FINISHED"
        self._call("set_terminated", lambda: self._client.set_terminated(run_id, status=status))

    def register_version(self, model_name: str, run_id: str, artifact_path: str) -> str:
        def register() -> str:
            try:
                self._client.create_registered_model(model_name)
            except MlflowException as exc:
                if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                    raise
            source = f"{self._client.get_run(run_id).info.artifact_uri}/{artifact_path}"
            return str(self._client.create_model_version(model_name, source, run_id=run_id).version)

        return self._call("register_version", register)

    def set_alias(self, model_name: str, alias: str, version: str) -> None:
        self._call(
            "set_alias",
            lambda: self._client.set_registered_model_alias(model_name, alias, version),
        )

    def get_alias(self, model_name: str, alias: str) -> str | None:
        try:
            return str(self._client.get_model_version_by_alias(model_name, alias).version)
        except MlflowException as exc:
            if exc.error_code in _MISSING_CODES:
                return None
            raise ExternalServiceError("mlflow get_alias failed") from exc

    def set_version_tags(self, model_name: str, version: str, tags: Mapping[str, str]) -> None:
        def tag() -> None:
            for key, value in sorted(tags.items()):
                self._client.set_model_version_tag(model_name, version, key, value)

        self._call("set_version_tags", tag)
```
If `uv run mypy` reports untyped MLflow calls (`no-untyped-call`), add `# type: ignore[no-untyped-call]` on
those lines only (not a module-wide ignore). `MlflowClient(...)` constructs without network access.

- [ ] **Step 5: Run — expect PASS**: `uv run pytest tests/unit/test_tracking.py -q && uv run mypy`

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training/tracking.py tests/fakes.py tests/unit/test_tracking.py
git commit -m "feat(training): tracker protocol with mlflow client wrapper and in-memory fake"
```

---

### Task 6: Model bundle, manifest, fail-closed loader (`registry/artifacts.py`)

**Files:**
- Create: `src/dbdelay/registry/__init__.py` (docstring only), `src/dbdelay/registry/artifacts.py`
- Test: `tests/unit/test_artifacts.py`

**Interfaces:**
- Consumes: `TrainingReport`, `IsotonicCalibrator`, `FeatureSpec`, `booster_from_text`, `predict_raw`,
  `build_features`, `ObjectStore`.
- Produces:
  - `BUNDLE_FILES: tuple[str, ...] = ("model.txt", "calibrator.json", "feature_spec.json", "metrics.json",
    "model_card.md", "reference_sample.parquet")`, `MANIFEST_FILE = "manifest.json"`
  - `models_prefix(version: str, root: str = "") -> str` → `f"{root}models/{version}/"`
  - `sha256_ref(data: bytes) -> str` → `"sha256:<hex>"`
  - `Manifest` (pydantic, §6 contract)
  - `build_manifest(files: Mapping[str, bytes], *, model_name: str, version: str, run_id: str,
    report: TrainingReport) -> Manifest`
  - `write_bundle(store, manifest: Manifest, files: Mapping[str, bytes], root: str = "") -> None`
  - `read_bundle_files(store, version: str, root: str = "") -> tuple[Manifest, dict[str, bytes]]`
  - `ModelBundle(manifest, booster, calibrator, spec)` with `.predict(df: pd.DataFrame) -> NDArray[np.float64]`
  - `parse_bundle(manifest: Manifest, files: Mapping[str, bytes]) -> ModelBundle`
  - `load_bundle(store, version: str, root: str = "") -> ModelBundle`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_artifacts.py`

```python
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from dbdelay.data.stations import load_stations
from dbdelay.errors import ArtifactIntegrityError
from dbdelay.features.build import build_features
from dbdelay.features.spec import RiskThresholds, fit_spec
from dbdelay.registry.artifacts import (
    BUNDLE_FILES,
    MANIFEST_FILE,
    build_manifest,
    load_bundle,
    models_prefix,
    sha256_ref,
    write_bundle,
)
from dbdelay.storage import ObjectStore
from dbdelay.training.calibrate import IsotonicCalibrator
from dbdelay.training.evaluate import Metrics, SplitReport, TrainingReport
from dbdelay.training.train import booster_from_text, predict_raw, train_lightgbm
from tests.builders import SIGNAL_CONFIG, signal_frames

REPO = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO / "configs" / "stations.yaml")
SPLIT = SplitReport(
    overall=Metrics(n=10, base_rate=0.3, brier=0.14, roc_auc=0.8), calibration=[], slices={}
)


@pytest.fixture(scope="module")
def bundle_files() -> dict[str, bytes]:
    frames = signal_frames()
    spec = fit_spec(frames["train"], STATIONS, 1).model_copy(
        update={"risk_thresholds": RiskThresholds(medium=0.2, high=0.45)}
    )
    feats = {k: build_features(v, spec) for k, v in frames.items()}
    labels = {k: v["is_late"].to_numpy(dtype=bool) for k, v in frames.items()}
    result = train_lightgbm(
        feats["train"], labels["train"], feats["valid"], labels["valid"],
        SIGNAL_CONFIG.lightgbm, seed=42,
    )
    raw_valid = predict_raw(booster_from_text(result.model_text()), feats["valid"])
    report = TrainingReport(
        snapshot_id="2026-03-31_abcdef12", spec_hash=spec.spec_hash, git_sha="abc1234",
        trained_at=datetime(2026, 10, 4, tzinfo=UTC), train_start=date(2026, 3, 1),
        train_end=date(2026, 3, 17), test_rows=10, challenger_valid=SPLIT,
        challenger_test=SPLIT, baseline_test=SPLIT,
    )
    return {
        "model.txt": result.model_text().encode(),
        "calibrator.json": IsotonicCalibrator.fit(raw_valid, labels["valid"]).to_json().encode(),
        "feature_spec.json": spec.to_json().encode(),
        "metrics.json": report.model_dump_json().encode(),
        "model_card.md": b"# card\n",
        "reference_sample.parquet": b"PAR1",
    }


def _manifest(files: dict[str, bytes], version: str = "1"):  # type: ignore[no-untyped-def]
    report = TrainingReport.model_validate_json(files["metrics.json"])
    return build_manifest(files, model_name="m", version=version, run_id="r1", report=report)


def test_manifest_hashes_every_file(bundle_files: dict[str, bytes]) -> None:
    manifest = _manifest(bundle_files)
    assert set(manifest.files) == set(BUNDLE_FILES)
    assert manifest.files["model.txt"] == sha256_ref(bundle_files["model.txt"])
    assert manifest.metrics == {"test_brier": 0.14, "test_auc": 0.8, "baseline_brier": 0.14}
    assert manifest.train_window == {"start": date(2026, 3, 1), "end": date(2026, 3, 17)}
    assert manifest.data_snapshot_id == "2026-03-31_abcdef12"


def test_manifest_rejects_missing_or_extra_files(bundle_files: dict[str, bytes]) -> None:
    with pytest.raises(ArtifactIntegrityError):
        _manifest({k: v for k, v in bundle_files.items() if k != "model.txt"})
    with pytest.raises(ArtifactIntegrityError):
        _manifest(bundle_files | {"extra.bin": b""})


def test_write_then_load_predicts(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    manifest = _manifest(bundle_files)
    write_bundle(s3_store, manifest, bundle_files)
    bundle = load_bundle(s3_store, "1")
    test = signal_frames()["test"]
    p = bundle.predict(test)
    assert p.shape == (len(test),)
    assert np.all((p >= 0) & (p <= 1))
    assert bundle.spec.risk_thresholds == RiskThresholds(medium=0.2, high=0.45)


def test_tampered_file_fails_closed(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    write_bundle(s3_store, _manifest(bundle_files), bundle_files)
    s3_store.put_bytes(models_prefix("1") + "model.txt", b"tree\n")
    with pytest.raises(ArtifactIntegrityError, match=r"model\.txt"):
        load_bundle(s3_store, "1")


def test_unlisted_file_fails_closed(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    write_bundle(s3_store, _manifest(bundle_files), bundle_files)
    s3_store.put_bytes(models_prefix("1") + "surprise.txt", b"x")
    with pytest.raises(ArtifactIntegrityError, match="unlisted"):
        load_bundle(s3_store, "1")


def test_missing_version_fails_closed(s3_store: ObjectStore) -> None:
    with pytest.raises(ArtifactIntegrityError, match="manifest"):
        load_bundle(s3_store, "7")


def test_version_mismatch_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    manifest = _manifest(bundle_files, version="2")
    write_bundle(s3_store, manifest, bundle_files)
    s3_store.put_bytes(models_prefix("3") + MANIFEST_FILE, manifest.model_dump_json().encode())
    with pytest.raises(ArtifactIntegrityError, match="version"):
        load_bundle(s3_store, "3")


def test_unparseable_model_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    broken = bundle_files | {"model.txt": b"not a model"}
    write_bundle(s3_store, _manifest(broken), broken)
    with pytest.raises(ArtifactIntegrityError, match="parse"):
        load_bundle(s3_store, "1")
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: dbdelay.registry`)

Run: `uv run pytest tests/unit/test_artifacts.py -q`

- [ ] **Step 3: Implement**

`src/dbdelay/registry/__init__.py`:
```python
"""Model artifacts, champion pointer, release and rollback (architecture §6)."""
```

`src/dbdelay/registry/artifacts.py`:
```python
"""Model bundle contract: `models/<version>/` + `manifest.json` with SHA-256 (architecture §6).

Loaders verify every checksum and fail closed.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd
from lightgbm.basic import LightGBMError
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.errors import ArtifactIntegrityError, DataValidationError, NotFoundError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FeatureSpec
from dbdelay.storage import ObjectStore
from dbdelay.training.calibrate import IsotonicCalibrator
from dbdelay.training.evaluate import TrainingReport
from dbdelay.training.train import booster_from_text, predict_raw

MANIFEST_FILE = "manifest.json"
BUNDLE_FILES: tuple[str, ...] = (
    "model.txt",
    "calibrator.json",
    "feature_spec.json",
    "metrics.json",
    "model_card.md",
    "reference_sample.parquet",
)
CONTENT_TYPES = {
    "model.txt": "text/plain",
    "calibrator.json": "application/json",
    "feature_spec.json": "application/json",
    "metrics.json": "application/json",
    "model_card.md": "text/markdown",
    "reference_sample.parquet": "application/octet-stream",
}


def models_prefix(version: str, root: str = "") -> str:
    return f"{root}models/{version}/"


def sha256_ref(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Manifest(BaseModel):
    """`manifest.json` (schema version 1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    model_name: str
    version: str
    mlflow_run_id: str
    git_sha: str
    data_snapshot_id: str
    trained_at: datetime
    train_window: dict[str, date]
    files: dict[str, str]
    metrics: dict[str, float | None]


def _check_names(names: set[str]) -> None:
    missing = sorted(set(BUNDLE_FILES) - names)
    extra = sorted(names - set(BUNDLE_FILES))
    if missing or extra:
        raise ArtifactIntegrityError(f"bundle files: missing {missing}, unexpected {extra}")


def build_manifest(
    files: Mapping[str, bytes], *, model_name: str, version: str, run_id: str, report: TrainingReport
) -> Manifest:
    """Manifest for exactly ``BUNDLE_FILES``.

    Raises:
        ArtifactIntegrityError: if a bundle file is missing or an unexpected one is given.
    """
    _check_names(set(files))
    return Manifest(
        model_name=model_name,
        version=version,
        mlflow_run_id=run_id,
        git_sha=report.git_sha,
        data_snapshot_id=report.snapshot_id,
        trained_at=report.trained_at,
        train_window={"start": report.train_start, "end": report.train_end},
        files={name: sha256_ref(files[name]) for name in BUNDLE_FILES},
        metrics={
            "test_brier": report.challenger_test.overall.brier,
            "test_auc": report.challenger_test.overall.roc_auc,
            "baseline_brier": report.baseline_test.overall.brier,
        },
    )


def write_bundle(
    store: ObjectStore, manifest: Manifest, files: Mapping[str, bytes], root: str = ""
) -> None:
    """Upload the bundle files, then the manifest last (a manifest means a complete bundle)."""
    prefix = models_prefix(manifest.version, root)
    for name in BUNDLE_FILES:
        store.put_bytes(prefix + name, files[name], CONTENT_TYPES[name])
    store.put_bytes(prefix + MANIFEST_FILE, manifest.model_dump_json(indent=2).encode(), "application/json")


def read_bundle_files(
    store: ObjectStore, version: str, root: str = ""
) -> tuple[Manifest, dict[str, bytes]]:
    """Download and verify a bundle.

    Raises:
        ArtifactIntegrityError: missing/invalid manifest, version mismatch, missing or
            unlisted file, or checksum mismatch.
    """
    prefix = models_prefix(version, root)
    try:
        manifest = Manifest.model_validate_json(store.get_bytes(prefix + MANIFEST_FILE))
    except NotFoundError:
        raise ArtifactIntegrityError(f"models/{version}: manifest missing") from None
    except ValidationError:
        raise ArtifactIntegrityError(f"models/{version}: manifest invalid") from None
    if manifest.version != version:
        raise ArtifactIntegrityError(f"models/{version}: manifest is for version {manifest.version}")
    _check_names(set(manifest.files))
    present = {key[len(prefix) :] for key in store.iter_keys(prefix)}
    unlisted = sorted(present - set(manifest.files) - {MANIFEST_FILE})
    if unlisted:
        raise ArtifactIntegrityError(f"models/{version}: unlisted files {unlisted}")
    files: dict[str, bytes] = {}
    for name, expected in manifest.files.items():
        try:
            data = store.get_bytes(prefix + name)
        except NotFoundError:
            raise ArtifactIntegrityError(f"models/{version}: {name} missing") from None
        if sha256_ref(data) != expected:
            raise ArtifactIntegrityError(f"models/{version}: checksum mismatch for {name}")
        files[name] = data
    return manifest, files


@dataclass(frozen=True)
class ModelBundle:
    manifest: Manifest
    booster: lgb.Booster
    calibrator: IsotonicCalibrator
    spec: FeatureSpec

    def predict(self, df: pd.DataFrame) -> NDArray[np.float64]:
        """Calibrated ``p_late`` for silver-shaped rows (features built with this bundle's spec)."""
        return self.calibrator.apply(predict_raw(self.booster, build_features(df, self.spec)))


def parse_bundle(manifest: Manifest, files: Mapping[str, bytes]) -> ModelBundle:
    """Parse verified bundle files.

    Raises:
        ArtifactIntegrityError: if a file cannot be parsed.
    """
    try:
        return ModelBundle(
            manifest=manifest,
            booster=booster_from_text(files["model.txt"].decode("utf-8")),
            calibrator=IsotonicCalibrator.from_json(files["calibrator.json"]),
            spec=FeatureSpec.from_json(files["feature_spec.json"]),
        )
    except (LightGBMError, DataValidationError, UnicodeDecodeError) as exc:
        raise ArtifactIntegrityError(
            f"models/{manifest.version}: cannot parse bundle files"
        ) from exc


def load_bundle(store: ObjectStore, version: str, root: str = "") -> ModelBundle:
    """Verified, parsed bundle of one model version (fails closed)."""
    manifest, files = read_bundle_files(store, version, root)
    return parse_bundle(manifest, files)
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_artifacts.py -q && uv run mypy`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/registry tests/unit/test_artifacts.py
git commit -m "feat(registry): model bundle manifest with sha-256 and fail-closed loader"
```

---

### Task 7: Champion pointer, release, rollback (`pointer.py`, `release.py`, `make rollback`)

**Files:**
- Create: `src/dbdelay/registry/pointer.py`, `src/dbdelay/registry/release.py`, `scripts/rollback.py`
- Modify: `Makefile` (`rollback` target + `.PHONY`)
- Test: `tests/unit/test_pointer_release.py`

**Interfaces:**
- Consumes: `Manifest`, `write_bundle`, `read_bundle_files`, `load_bundle`, `Tracker`.
- Produces:
  - `POINTER_KEY = "models/_pointer.json"`; `PointerState(champion_version: str, previous_version: str | None,
    updated_at: datetime)`; `ModelPointer` protocol (`get() -> PointerState | None`,
    `set(champion: str, previous: str | None) -> PointerState`); `ObjectStorePointer(store, root="")`
  - `CHAMPION = "champion"`, `CHALLENGER = "challenger"`
  - `release_version(store, pointer, tracker, *, manifest: Manifest, files: Mapping[str, bytes], root="")
    -> PointerState`
  - `rollback(store, pointer, tracker, *, model_name: str, root="") -> PointerState`
  - `rollback_main(argv: Sequence[str] | None = None, *, tracker: Tracker | None = None) -> int`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_pointer_release.py`

```python
import pytest

from dbdelay.errors import ArtifactIntegrityError, ModelNotAvailableError
from dbdelay.registry.artifacts import models_prefix
from dbdelay.registry.pointer import POINTER_KEY, ObjectStorePointer
from dbdelay.registry.release import CHAMPION, release_version, rollback, rollback_main
from dbdelay.storage import ObjectStore
from tests.fakes import FakeTracker
from tests.unit.test_artifacts import _manifest, bundle_files  # noqa: F401 - fixture reuse


def _release(store, files, tracker, version):  # type: ignore[no-untyped-def]
    return release_version(
        store, ObjectStorePointer(store), tracker, manifest=_manifest(files, version), files=files
    )


def test_pointer_absent_then_set(s3_store: ObjectStore) -> None:
    pointer = ObjectStorePointer(s3_store)
    assert pointer.get() is None
    state = pointer.set("2", "1")
    assert pointer.get() == state
    assert (state.champion_version, state.previous_version) == ("2", "1")


def test_corrupt_pointer_raises(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(POINTER_KEY, b"{}")
    with pytest.raises(ArtifactIntegrityError):
        ObjectStorePointer(s3_store).get()


def test_release_sets_pointer_and_alias(s3_store, bundle_files) -> None:  # type: ignore[no-untyped-def]
    tracker = FakeTracker()
    first = _release(s3_store, bundle_files, tracker, "1")
    assert (first.champion_version, first.previous_version) == ("1", None)
    second = _release(s3_store, bundle_files, tracker, "2")
    assert (second.champion_version, second.previous_version) == ("2", "1")
    assert tracker.get_alias("m", CHAMPION) == "2"
    assert s3_store.exists(models_prefix("2") + "manifest.json")


def test_release_rerun_is_idempotent(s3_store, bundle_files) -> None:  # type: ignore[no-untyped-def]
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    tracker.aliases.clear()  # crash after the pointer write, before the alias
    again = _release(s3_store, bundle_files, tracker, "2")
    assert (again.champion_version, again.previous_version) == ("2", "1")
    assert tracker.get_alias("m", CHAMPION) == "2"


def test_release_refuses_different_existing_bundle(s3_store, bundle_files) -> None:  # type: ignore[no-untyped-def]
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    changed = bundle_files | {"model_card.md": b"# other\n"}
    with pytest.raises(ArtifactIntegrityError, match="different"):
        _release(s3_store, changed, tracker, "1")


def test_rollback_swaps_back_and_moves_alias(s3_store, bundle_files) -> None:  # type: ignore[no-untyped-def]
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    state = rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")
    assert (state.champion_version, state.previous_version) == ("1", "2")
    assert tracker.get_alias("m", CHAMPION) == "1"


def test_rollback_without_previous_raises(s3_store, bundle_files) -> None:  # type: ignore[no-untyped-def]
    tracker = FakeTracker()
    with pytest.raises(ModelNotAvailableError):
        rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")
    _release(s3_store, bundle_files, tracker, "1")
    with pytest.raises(ModelNotAvailableError):
        rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")


def test_rollback_refuses_broken_previous(s3_store, bundle_files) -> None:  # type: ignore[no-untyped-def]
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    s3_store.put_bytes(models_prefix("1") + "model.txt", b"x")
    with pytest.raises(ArtifactIntegrityError):
        rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")
    assert ObjectStorePointer(s3_store).get().champion_version == "2"  # type: ignore[union-attr]


def test_rollback_main_prints_versions(  # type: ignore[no-untyped-def]
    s3_store, bundle_files, monkeypatch, capsys
) -> None:
    from pathlib import Path

    import yaml

    from tests.builders import MARCH_CONFIG

    monkeypatch.setenv("MODELS_BUCKET", s3_store.bucket)
    config = Path("training.yaml")
    config.write_text(yaml.safe_dump(MARCH_CONFIG.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("TRAINING_CONFIG_FILE", str(config))
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    assert rollback_main([], tracker=tracker) == 0
    assert "champion 1 (was 2)" in capsys.readouterr().out
```
Note: `rollback_main` uses `get_settings()` — the autouse `isolated_env` fixture clears the cache per test; set env
before calling. The models bucket env var is `MODELS_BUCKET`.

- [ ] **Step 2: Run — expect FAIL**: `uv run pytest tests/unit/test_pointer_release.py -q`

- [ ] **Step 3: Implement**

`src/dbdelay/registry/pointer.py`:
```python
"""Champion pointer: which model version serving uses (MinIO object now, SSM in Phase 6)."""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.errors import ArtifactIntegrityError, NotFoundError
from dbdelay.storage import ObjectStore

POINTER_KEY = "models/_pointer.json"


class PointerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    champion_version: str
    previous_version: str | None
    updated_at: datetime


class ModelPointer(Protocol):
    def get(self) -> PointerState | None: ...
    def set(self, champion: str, previous: str | None) -> PointerState: ...


class ObjectStorePointer:
    """`ModelPointer` stored as one JSON object in the models bucket."""

    def __init__(self, store: ObjectStore, root: str = "") -> None:
        self._store = store
        self._key = root + POINTER_KEY

    def get(self) -> PointerState | None:
        """Current pointer, or ``None`` before the first release.

        Raises:
            ArtifactIntegrityError: if the pointer object is not valid JSON for ``PointerState``.
        """
        try:
            raw = self._store.get_bytes(self._key)
        except NotFoundError:
            return None
        try:
            return PointerState.model_validate_json(raw)
        except ValidationError:
            raise ArtifactIntegrityError("model pointer is corrupt") from None

    def set(self, champion: str, previous: str | None) -> PointerState:
        state = PointerState(
            champion_version=champion, previous_version=previous, updated_at=datetime.now(UTC)
        )
        self._store.put_bytes(self._key, state.model_dump_json(indent=2).encode(), "application/json")
        return state
```

`src/dbdelay/registry/release.py`:
```python
"""Release a model version (upload → verify → pointer → @champion) and roll back (architecture §6)."""

import argparse
import sys
from collections.abc import Mapping, Sequence

from dbdelay.errors import ArtifactIntegrityError, ModelNotAvailableError
from dbdelay.registry.artifacts import (
    MANIFEST_FILE,
    Manifest,
    load_bundle,
    models_prefix,
    read_bundle_files,
    write_bundle,
)
from dbdelay.registry.pointer import ModelPointer, ObjectStorePointer, PointerState
from dbdelay.storage import ObjectStore
from dbdelay.training.tracking import Tracker

CHAMPION = "champion"
CHALLENGER = "challenger"


def release_version(  # noqa: PLR0913 - store, pointer, tracker + bundle + root
    store: ObjectStore,
    pointer: ModelPointer,
    tracker: Tracker,
    *,
    manifest: Manifest,
    files: Mapping[str, bytes],
    root: str = "",
) -> PointerState:
    """Make ``manifest.version`` the champion. Safe to re-run after a crash.

    The pointer is the source of truth for serving; the MLflow alias follows it.

    Raises:
        ArtifactIntegrityError: if ``models/<version>/`` exists with other files, or the
            uploaded bundle does not verify.
    """
    version = manifest.version
    if store.exists(models_prefix(version, root) + MANIFEST_FILE):
        existing, _ = read_bundle_files(store, version, root)
        if existing.files != manifest.files:
            raise ArtifactIntegrityError(f"models/{version} exists with different files")
    else:
        write_bundle(store, manifest, files, root)
    load_bundle(store, version, root)  # verify exactly what serving will load
    current = pointer.get()
    if current is not None and current.champion_version == version:
        state = current
    else:
        state = pointer.set(version, current.champion_version if current else None)
    tracker.set_alias(manifest.model_name, CHAMPION, version)
    return state


def rollback(
    store: ObjectStore, pointer: ModelPointer, tracker: Tracker, *, model_name: str, root: str = ""
) -> PointerState:
    """Swap champion and previous (a second rollback rolls forward again).

    Raises:
        ModelNotAvailableError: if there is no previous version.
        ArtifactIntegrityError: if the previous bundle does not verify (pointer unchanged).
    """
    current = pointer.get()
    if current is None or current.previous_version is None:
        raise ModelNotAvailableError("no previous model version to roll back to")
    load_bundle(store, current.previous_version, root)
    state = pointer.set(current.previous_version, current.champion_version)
    tracker.set_alias(model_name, CHAMPION, state.champion_version)
    return state


def rollback_main(argv: Sequence[str] | None = None, *, tracker: Tracker | None = None) -> int:
    """CLI for `make rollback`."""
    from dbdelay.config import get_settings
    from dbdelay.storage import make_s3_client
    from dbdelay.training.config import load_training_config
    from dbdelay.training.tracking import MlflowTracker

    argparse.ArgumentParser(description="Point the champion back at the previous version.").parse_args(
        argv
    )
    settings = get_settings()
    cfg = load_training_config(settings.training_config_file)
    store = ObjectStore(make_s3_client(settings), settings.models_bucket)
    state = rollback(
        store,
        ObjectStorePointer(store),
        tracker or MlflowTracker(settings.mlflow_tracking_uri),
        model_name=cfg.registry.model_name,
    )
    sys.stdout.write(f"champion {state.champion_version} (was {state.previous_version})\n")
    return 0
```

`scripts/rollback.py`:
```python
"""`make rollback`: swap the champion pointer back to the previous model version."""

from dbdelay.registry.release import rollback_main

if __name__ == "__main__":
    raise SystemExit(rollback_main())
```

`Makefile` — add `rollback` to `.PHONY` and:
```make
rollback: ## Point the champion back at the previous model version (pointer + MLflow alias)
	uv run python scripts/rollback.py
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_pointer_release.py -q && uv run mypy`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/registry scripts/rollback.py Makefile tests/unit/test_pointer_release.py
git commit -m "feat(registry): champion pointer in minio, idempotent release and make rollback"
```

---

### Task 8: Model card draft (`model_card.py`)

**Files:**
- Create: `src/dbdelay/training/model_card.py`
- Test: `tests/unit/test_model_card.py`

**Interfaces:**
- Consumes: `TrainingReport`, `RiskThresholds`.
- Produces: `render_model_card(report: TrainingReport, *, model_name: str, params: Mapping[str, object],
  risk: RiskThresholds) -> str`

- [ ] **Step 1: Write failing test** — `tests/unit/test_model_card.py`

```python
from dbdelay.features.spec import RiskThresholds
from dbdelay.training.model_card import render_model_card
from tests.unit.test_gate import BASE, GOOD, report


def test_card_has_required_sections_and_numbers() -> None:
    card = render_model_card(
        report(GOOD, BASE, champion=BASE),
        model_name="puenktlich-delay",
        params={"num_leaves": 31},
        risk=RiskThresholds(medium=0.2, high=0.45),
    )
    for heading in (
        "# Model card — puenktlich-delay",
        "## Intended use",
        "## Data",
        "## Metrics (test split)",
        "## Slices (ROC-AUC by train type)",
        "## Risk levels",
        "## Limitations",
    ):
        assert heading in card
    assert "0.1400" in card  # challenger Brier
    assert "0.1520" in card  # baseline Brier
    assert "champion v3" in card
    assert "2026-08-31_abcdef12" in card
    assert "num_leaves" in card


def test_card_without_champion_says_first_model() -> None:
    card = render_model_card(
        report(GOOD, BASE), model_name="m", params={}, risk=RiskThresholds(medium=0.2, high=0.45)
    )
    assert "no champion yet" in card
```

- [ ] **Step 2: Run — expect FAIL**: `uv run pytest tests/unit/test_model_card.py -q`

- [ ] **Step 3: Implement `src/dbdelay/training/model_card.py`**

```python
"""Model card draft (markdown) written with every trained model (prd §8)."""

from collections.abc import Mapping

from dbdelay.features.spec import RiskThresholds
from dbdelay.training.evaluate import Metrics, SplitReport, TrainingReport
from dbdelay.training.gate import SLICE_COLUMN

_METRICS = (("Brier", "brier"), ("ROC-AUC", "roc_auc"), ("PR-AUC", "pr_auc"), ("ECE", "ece"))
LIMITATIONS = (
    "Timetable/calendar features only (v1): no live context, weather or incidents.",
    "Trained on December–August history; September–November months were never seen "
    "(`month` is extrapolated).",
    "The valid split drives early stopping, grid selection and calibration; only the test "
    "split is untouched.",
    "Label: departure ≥ 6 min late; cancelled departures are excluded, not predicted.",
)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _metric(split: SplitReport | None, name: str) -> float | None:
    if split is None:
        return None
    value: float | None = getattr(split.overall, name)
    return value


def _slice_auc(split: SplitReport | None, key: str) -> float | None:
    if split is None:
        return None
    metrics: Metrics | None = split.slices.get(SLICE_COLUMN, {}).get(key)
    return metrics.roc_auc if metrics else None


def render_model_card(
    report: TrainingReport,
    *,
    model_name: str,
    params: Mapping[str, object],
    risk: RiskThresholds,
) -> str:
    """Markdown card: intended use, data, metrics vs baseline/champion, slices, risk, limitations."""
    champion = (
        f"champion v{report.champion_version}" if report.champion_version else "no champion yet"
    )
    columns = [
        ("challenger", report.challenger_test),
        ("baseline", report.baseline_test),
        (champion, report.champion_test),
    ]
    lines = [
        f"# Model card — {model_name}",
        "",
        "Draft written at training time; the version is assigned at registration and the gate "
        "result is recorded in MLflow (`gate.json`).",
        "",
        "## Intended use",
        "Probability that a Deutsche Bahn departure at a supported station leaves ≥ 6 minutes "
        "late, shown as a risk badge to riders. Not for operational or safety decisions.",
        "",
        "## Data",
        f"- Snapshot: `{report.snapshot_id}`",
        f"- Train window: {report.train_start} → {report.train_end}",
        f"- Test rows: {report.test_rows:,}",
        f"- Trained at: {report.trained_at:%Y-%m-%d %H:%M} UTC · git `{report.git_sha}`",
        "- Parameters: " + ", ".join(f"`{k}={v}`" for k, v in sorted(params.items())),
        "",
        "## Metrics (test split)",
        "| Metric | " + " | ".join(name for name, _ in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for label, field in _METRICS:
        lines.append(
            f"| {label} | " + " | ".join(_fmt(_metric(s, field)) for _, s in columns) + " |"
        )
    keys = sorted(report.challenger_test.slices.get(SLICE_COLUMN, {}))
    lines += [
        "",
        "## Slices (ROC-AUC by train type)",
        "| Train type | " + " | ".join(name for name, _ in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for key in keys:
        lines.append(
            f"| {key} | " + " | ".join(_fmt(_slice_auc(s, key)) for _, s in columns) + " |"
        )
    lines += [
        "",
        "## Risk levels",
        f"Low < {risk.medium:.2f} ≤ Medium < {risk.high:.2f} ≤ High (calibrated `p_late`).",
        "",
        "## Limitations",
        *(f"- {item}" for item in LIMITATIONS),
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/unit/test_model_card.py -q && uv run mypy`

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training/model_card.py tests/unit/test_model_card.py
git commit -m "feat(training): model card draft with metrics, slices and limitations"
```

---

### Task 9: Pipeline steps, `make train` CLI

**Files:**
- Modify: `src/dbdelay/training/run_baseline.py` (extract `fit_baseline(…, snapshot_id, …)`)
- Create: `src/dbdelay/training/pipeline.py`, `src/dbdelay/training/run_train.py`
- Modify: `Makefile` (`train` target + `.PHONY`)
- Test: `tests/unit/test_pipeline.py`

**Interfaces:**
- Consumes: everything above; `build_snapshot`, `load_snapshot_split`, `snapshot_prefix`, `SNAPSHOT_FILE`,
  `SnapshotManifest`, `validate_silver`, `fit_spec`, `build_features`, `BaselineModel`, `evaluate_split`,
  `slice_frame`.
- Produces:
  - `run_baseline.fit_baseline(store, cfg, stations, snapshot_id, workdir, *, root="") -> EvaluationReport`
    (`run_baseline` becomes `build_snapshot` + `fit_baseline`, behaviour unchanged)
  - `PipelineContext(data_store, model_store, tracker, cfg, stations, workdir, root="", git_sha="unknown")`
  - `make_context(cfg, workdir, *, settings=None, tracker=None) -> PipelineContext`
  - `current_git_sha() -> str`
  - Steps: `build_training_set(ctx) -> SnapshotManifest`, `validate_snapshot(ctx, snapshot_id) -> None`,
    `train_baseline(ctx, snapshot_id) -> EvaluationReport`, `train_model(ctx, snapshot_id) -> str` (run id),
    `calibrate_model(ctx, snapshot_id, run_id) -> None`,
    `evaluate_models(ctx, snapshot_id, run_id) -> TrainingReport`, `register_model(ctx, run_id) -> str`
    (version), `run_gate(ctx, run_id, version) -> GateDecision`, `record_rejection(ctx, run_id, version) -> None`,
    `release_model(ctx, run_id, version) -> PointerState`, `smoke_test(ctx, snapshot_id, run_id, version) -> None`
  - `PipelineResult(snapshot_id, run_id, version, promoted: bool, decision: GateDecision,
    champion_version: str | None)`; `run_training_pipeline(ctx) -> PipelineResult`
  - Run artifact paths: `bundle/<BUNDLE_FILES>`, `gate.json`, `grid_scores.json`, `feature_importance.json`,
    `calibration_bins.json`, `smoke/expected.json`

- [ ] **Step 1: Refactor `run_baseline.py`** — move the body after `build_snapshot` into:

```python
def fit_baseline(  # noqa: PLR0913 - store, cfg, stations, snapshot, workdir, root
    store: ObjectStore,
    cfg: TrainingConfig,
    stations: Sequence[Station],
    snapshot_id: str,
    workdir: Path,
    *,
    root: str = "",
) -> EvaluationReport:
    """Fit spec + baseline on an existing snapshot's train split, evaluate valid and test,
    and write `baseline/{feature_spec,baseline,metrics}.json` next to the snapshot."""
    frames = {
        name: load_snapshot_split(store, snapshot_id, name, workdir, root=root) for name in SPLITS
    }
    # … unchanged body using `snapshot_id` instead of `manifest.snapshot_id` …


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
    return fit_baseline(store, cfg, stations, manifest.snapshot_id, workdir, root=root)
```
Run: `uv run pytest tests/unit/test_run_baseline.py -q` → PASS (unchanged behaviour).

- [ ] **Step 2: Write failing tests** — `tests/unit/test_pipeline.py`

```python
import json
from pathlib import Path

import numpy as np
import pytest

from dbdelay.data.stations import load_stations
from dbdelay.errors import ArtifactIntegrityError, DataValidationError
from dbdelay.registry.artifacts import BUNDLE_FILES, models_prefix
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.registry.release import CHALLENGER, CHAMPION
from dbdelay.storage import ObjectStore
from dbdelay.training import pipeline
from dbdelay.training.pipeline import PipelineContext, run_training_pipeline
from tests.builders import SIGNAL_CONFIG, WEAK_LIGHTGBM, put_signal_silver
from tests.fakes import FakeTracker

REPO = Path(__file__).resolve().parents[2]
STATIONS = tuple(load_stations(REPO / "configs" / "stations.yaml"))
MODEL = SIGNAL_CONFIG.registry.model_name


def _ctx(store: ObjectStore, tracker: FakeTracker, tmp: Path, cfg=SIGNAL_CONFIG) -> PipelineContext:  # type: ignore[no-untyped-def]
    return PipelineContext(
        data_store=store, model_store=store, tracker=tracker, cfg=cfg, stations=STATIONS,
        workdir=tmp, git_sha="abc1234",
    )


@pytest.fixture
def signal_store(s3_store: ObjectStore) -> ObjectStore:
    put_signal_silver(s3_store)
    return s3_store


def test_first_run_promotes_second_identical_run_is_rejected(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    tracker = FakeTracker()
    first = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "a"))
    assert first.promoted
    assert first.version == "1"
    assert first.champion_version == "1"
    assert tracker.get_alias(MODEL, CHAMPION) == "1"
    assert tracker.status[first.run_id] == "FINISHED"
    for name in BUNDLE_FILES:
        assert signal_store.exists(models_prefix("1") + name)
    run = tracker.runs[first.run_id]
    assert run["tags"]["snapshot_id"] == first.snapshot_id  # type: ignore[index]
    assert run["tags"]["git_sha"] == "abc1234"  # type: ignore[index]
    assert run["inputs"][0][0] == first.snapshot_id  # type: ignore[index]

    second = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "b"))
    assert not second.promoted
    assert second.version == "2"
    assert second.decision.failed == ["brier_vs_champion"]
    assert second.champion_version == "1"
    assert tracker.get_alias(MODEL, CHALLENGER) == "2"
    assert tracker.get_alias(MODEL, CHAMPION) == "1"
    assert tracker.version_tags[(MODEL, "2")]["gate"] == "rejected"
    assert tracker.status[second.run_id] == "FINISHED"
    assert not signal_store.exists(models_prefix("2") + "manifest.json")


def test_weak_champion_is_replaced_by_better_model(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    tracker = FakeTracker()
    weak = SIGNAL_CONFIG.model_copy(update={"lightgbm": WEAK_LIGHTGBM})
    assert run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "a", weak)).promoted
    better = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "b"))
    assert better.promoted
    state = ObjectStorePointer(signal_store).get()
    assert state is not None
    assert (state.champion_version, state.previous_version) == ("2", "1")


def test_bundle_files_are_consistent(signal_store: ObjectStore, tmp_path: Path) -> None:
    tracker = FakeTracker()
    result = run_training_pipeline(_ctx(signal_store, tracker, tmp_path))
    metrics = json.loads(tracker.load_bytes(result.run_id, "bundle/metrics.json"))
    assert metrics["snapshot_id"] == result.snapshot_id
    assert metrics["champion_version"] is None
    spec = json.loads(tracker.load_bytes(result.run_id, "bundle/feature_spec.json"))
    assert spec["risk_thresholds"] == {"medium": 0.2, "high": 0.45}
    expected = json.loads(tracker.load_bytes(result.run_id, "smoke/expected.json"))
    assert 0 < len(expected) <= pipeline.SMOKE_ROWS
    gate = json.loads(tracker.load_bytes(result.run_id, "gate.json"))
    assert gate["passed"] is True
    card = tracker.load_bytes(result.run_id, "bundle/model_card.md").decode()
    assert "no champion yet" in card


def test_tampered_champion_fails_closed(signal_store: ObjectStore, tmp_path: Path) -> None:
    tracker = FakeTracker()
    run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "a"))
    signal_store.put_bytes(models_prefix("1") + "model.txt", b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "b"))
    assert tracker.status["run2"] == "FAILED"


def test_pointer_to_missing_champion_fails_closed(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    ObjectStorePointer(signal_store).set("9", None)
    tracker = FakeTracker()
    with pytest.raises(ArtifactIntegrityError, match="manifest missing"):
        run_training_pipeline(_ctx(signal_store, tracker, tmp_path))
    assert tracker.status["run1"] == "FAILED"


def test_validate_snapshot_rejects_row_count_mismatch(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    ctx = _ctx(signal_store, FakeTracker(), tmp_path)
    manifest = pipeline.build_training_set(ctx)
    key = pipeline.snapshot_prefix(manifest.snapshot_id) + "snapshot.json"
    broken = manifest.model_copy(
        update={"splits": {**manifest.splits, "test": manifest.splits["test"].model_copy(update={"rows": 1})}}
    )
    signal_store.put_bytes(key, broken.model_dump_json().encode())
    with pytest.raises(DataValidationError, match="test"):
        pipeline.validate_snapshot(ctx, manifest.snapshot_id)


def test_smoke_test_detects_prediction_drift(signal_store: ObjectStore, tmp_path: Path) -> None:
    tracker = FakeTracker()
    result = run_training_pipeline(_ctx(signal_store, tracker, tmp_path))
    expected = np.array(json.loads(tracker.load_bytes(result.run_id, "smoke/expected.json")))
    tracker.log_bytes(result.run_id, "smoke/expected.json", json.dumps((expected + 0.01).tolist()).encode())
    with pytest.raises(ArtifactIntegrityError, match="smoke"):
        pipeline.smoke_test(_ctx(signal_store, tracker, tmp_path), result.snapshot_id, result.run_id, "1")


def test_run_train_main(signal_store: ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml

    from dbdelay.config import get_settings
    from dbdelay.training import run_train

    monkeypatch.setenv("DATA_BUCKET", signal_store.bucket)
    monkeypatch.setenv("MODELS_BUCKET", signal_store.bucket)
    monkeypatch.setenv("STATIONS_FILE", str(REPO / "configs" / "stations.yaml"))
    get_settings.cache_clear()
    tracker = FakeTracker()
    monkeypatch.setattr(run_train, "MlflowTracker", lambda uri: tracker)
    config = tmp_path / "training.yaml"
    config.write_text(yaml.safe_dump(SIGNAL_CONFIG.model_dump(mode="json")), encoding="utf-8")
    assert run_train.main(["--config", str(config)]) == 0
    assert tracker.get_alias(MODEL, CHAMPION) == "1"
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError: dbdelay.training.pipeline`)

Run: `uv run pytest tests/unit/test_pipeline.py -q`

- [ ] **Step 4: Implement `src/dbdelay/training/pipeline.py`**

```python
"""`training_pipeline` steps (architecture §5.2), shared by `make train` and the Airflow DAG.

Steps hand off through one MLflow run: bundle files are logged under `bundle/`, and only ids
(snapshot id, run id, model version) travel between steps.
"""

import io
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import BaseModel

from dbdelay.config import Settings, get_settings
from dbdelay.data.schemas import validate_silver
from dbdelay.data.stations import Station, load_stations
from dbdelay.errors import ArtifactIntegrityError, DataValidationError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS, FeatureSpec, fit_spec
from dbdelay.logging import get_logger
from dbdelay.registry.artifacts import BUNDLE_FILES, build_manifest, load_bundle
from dbdelay.registry.pointer import ObjectStorePointer, PointerState
from dbdelay.registry.release import CHALLENGER, release_version
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.baseline import BaselineModel
from dbdelay.training.calibrate import IsotonicCalibrator
from dbdelay.training.config import TrainingConfig
from dbdelay.training.evaluate import (
    EvaluationReport,
    SplitReport,
    TrainingReport,
    evaluate_split,
    slice_frame,
)
from dbdelay.training.gate import GateDecision, evaluate_gate
from dbdelay.training.model_card import render_model_card
from dbdelay.training.run_baseline import BASELINE_DIR, fit_baseline
from dbdelay.training.split import (
    SNAPSHOT_FILE,
    SPLITS,
    SnapshotManifest,
    build_snapshot,
    load_snapshot_split,
    snapshot_prefix,
)
from dbdelay.training.tracking import Tracker
from dbdelay.training.train import (
    booster_from_text,
    feature_importance,
    predict_raw,
    train_lightgbm,
)

BUNDLE_DIR = "bundle/"
GATE_FILE = "gate.json"
SMOKE_FILE = "smoke/expected.json"
SMOKE_ROWS = 1000
SMOKE_TOLERANCE = 1e-9
_log = get_logger("training")

__all__ = ["snapshot_prefix"]  # re-exported for tests


@dataclass(frozen=True)
class PipelineContext:
    data_store: ObjectStore
    model_store: ObjectStore
    tracker: Tracker
    cfg: TrainingConfig
    stations: tuple[Station, ...]
    workdir: Path
    root: str = ""
    git_sha: str = "unknown"


class PipelineResult(BaseModel):
    snapshot_id: str
    run_id: str
    version: str
    promoted: bool
    decision: GateDecision
    champion_version: str | None


def current_git_sha() -> str:
    """`GIT_SHA` (set in the Airflow image), else the checkout's short sha, else "unknown"."""
    env = os.environ.get("GIT_SHA")
    if env:
        return env
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no user input
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH
            capture_output=True, text=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def make_context(
    cfg: TrainingConfig,
    workdir: Path,
    *,
    settings: Settings | None = None,
    tracker: Tracker | None = None,
) -> PipelineContext:
    """Context from environment settings (MinIO buckets, MLflow server, station list)."""
    from dbdelay.training.tracking import MlflowTracker

    settings = settings or get_settings()
    client = make_s3_client(settings)
    return PipelineContext(
        data_store=ObjectStore(client, settings.data_bucket),
        model_store=ObjectStore(client, settings.models_bucket),
        tracker=tracker or MlflowTracker(settings.mlflow_tracking_uri),
        cfg=cfg,
        stations=tuple(load_stations(settings.stations_file)),
        workdir=workdir,
        git_sha=current_git_sha(),
    )


@contextmanager
def _failing_run(ctx: PipelineContext, run_id: str) -> Iterator[None]:
    """Mark the MLflow run FAILED if a step raises, then re-raise."""
    try:
        yield
    except Exception:
        ctx.tracker.finish_run(run_id, failed=True)
        raise


def _frames(ctx: PipelineContext, snapshot_id: str, names: Sequence[str]) -> dict[str, pd.DataFrame]:
    return {
        name: load_snapshot_split(ctx.data_store, snapshot_id, name, ctx.workdir, root=ctx.root)
        for name in names
    }


def _labels(frame: pd.DataFrame) -> NDArray[np.bool_]:
    return frame["is_late"].to_numpy(dtype=bool)


def _snapshot(ctx: PipelineContext, snapshot_id: str) -> SnapshotManifest:
    key = snapshot_prefix(snapshot_id, ctx.root) + SNAPSHOT_FILE
    return SnapshotManifest.model_validate_json(ctx.data_store.get_bytes(key))


def _json(data: object) -> bytes:
    return json.dumps(data, indent=2, sort_keys=True).encode()


def build_training_set(ctx: PipelineContext) -> SnapshotManifest:
    return build_snapshot(ctx.data_store, ctx.cfg, ctx.workdir / "snapshot", root=ctx.root)


def validate_snapshot(ctx: PipelineContext, snapshot_id: str) -> None:
    """Silver contract + row counts + date ranges of every split against `snapshot.json`.

    Raises:
        DataValidationError: on any mismatch.
    """
    manifest = _snapshot(ctx, snapshot_id)
    for name, frame in _frames(ctx, snapshot_id, SPLITS).items():
        info = manifest.splits[name]
        if frame.empty or len(frame) != info.rows:
            raise DataValidationError(f"split {name}: {len(frame)} rows, manifest says {info.rows}")
        validate_silver(frame)
        days = frame["planned_departure_utc"].dt.date
        if days.min() < info.start or days.max() > info.end:
            raise DataValidationError(f"split {name}: rows outside {info.start}..{info.end}")


def train_baseline(ctx: PipelineContext, snapshot_id: str) -> EvaluationReport:
    return fit_baseline(
        ctx.data_store, ctx.cfg, ctx.stations, snapshot_id, ctx.workdir / "baseline", root=ctx.root
    )


def train_model(ctx: PipelineContext, snapshot_id: str) -> str:
    """Fit spec (+ risk thresholds) and the LightGBM grid; start the MLflow run. Returns its id."""
    cfg = ctx.cfg
    manifest = _snapshot(ctx, snapshot_id)
    frames = _frames(ctx, snapshot_id, ("train", "valid"))
    spec = fit_spec(frames["train"], ctx.stations, cfg.features.min_count).model_copy(
        update={"risk_thresholds": cfg.risk_thresholds}
    )
    features = {name: build_features(frame, spec) for name, frame in frames.items()}
    run_id = ctx.tracker.start_run(
        cfg.registry.experiment,
        {
            "snapshot_id": snapshot_id,
            "git_sha": ctx.git_sha,
            "spec_hash": spec.spec_hash,
            "feature_version": str(spec.version),
        },
    )
    with _failing_run(ctx, run_id):
        source = f"s3://{ctx.data_store.bucket}/{snapshot_prefix(snapshot_id, ctx.root)}"
        ctx.tracker.log_snapshot(run_id, snapshot_id, manifest.content_hash, source)
        result = train_lightgbm(
            features["train"], _labels(frames["train"]),
            features["valid"], _labels(frames["valid"]),
            cfg.lightgbm, cfg.seed,
        )
        ctx.tracker.log_params(
            run_id,
            {
                **result.params,
                "best_iteration": result.best_iteration,
                "train_rows": len(frames["train"]),
                "valid_rows": len(frames["valid"]),
                "window_start": manifest.window_start.isoformat(),
                "window_end": manifest.window_end.isoformat(),
            },
        )
        ctx.tracker.log_metrics(run_id, {"valid_logloss_raw": result.valid_logloss})
        ctx.tracker.log_bytes(run_id, BUNDLE_DIR + "model.txt", result.model_text().encode())
        ctx.tracker.log_bytes(run_id, BUNDLE_DIR + "feature_spec.json", spec.to_json().encode())
        ctx.tracker.log_bytes(
            run_id, "grid_scores.json", _json([s.model_dump() for s in result.grid])
        )
        booster = booster_from_text(result.model_text())
        ctx.tracker.log_bytes(run_id, "feature_importance.json", _json(feature_importance(booster)))
    return run_id


def _load_model(ctx: PipelineContext, run_id: str) -> tuple[object, FeatureSpec]:
    booster = booster_from_text(ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "model.txt").decode())
    spec = FeatureSpec.from_json(ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "feature_spec.json"))
    return booster, spec


def calibrate_model(ctx: PipelineContext, snapshot_id: str, run_id: str) -> None:
    """Isotonic calibrator on the valid split's raw scores → `bundle/calibrator.json`."""
    with _failing_run(ctx, run_id):
        booster, spec = _load_model(ctx, run_id)
        valid = _frames(ctx, snapshot_id, ("valid",))["valid"]
        raw = predict_raw(booster, build_features(valid, spec))  # type: ignore[arg-type]
        calibrator = IsotonicCalibrator.fit(raw, _labels(valid))
        ctx.tracker.log_bytes(run_id, BUNDLE_DIR + "calibrator.json", calibrator.to_json().encode())


def _evaluate(ctx: PipelineContext, labels: NDArray[np.bool_], p: NDArray[np.float64], slices: pd.DataFrame) -> SplitReport:
    ev = ctx.cfg.evaluation
    return evaluate_split(labels, p, slices, ece_bins=ev.ece_bins, slice_min_rows=ev.slice_min_rows)


def _flat_metrics(prefix: str, split: SplitReport) -> dict[str, float]:
    return {
        f"{prefix}_{key}": float(value)
        for key, value in split.overall.model_dump().items()
        if key not in ("n", "base_rate") and value is not None
    }


def _reference_sample(
    ctx: PipelineContext, features: pd.DataFrame, labels: NDArray[np.bool_], p_late: NDArray[np.float64]
) -> bytes:
    """Seeded train sample (features + label + p_late) for drift monitoring; deterministic bytes."""
    n = min(len(features), ctx.cfg.release.reference_sample_rows)
    index = np.sort(np.random.default_rng(ctx.cfg.seed).choice(len(features), n, replace=False))
    sample = features.iloc[index].reset_index(drop=True)
    sample["is_late"] = labels[index]
    sample["p_late"] = p_late[index]
    buffer = io.BytesIO()
    sample.to_parquet(buffer, index=False, compression="zstd")
    return buffer.getvalue()


def evaluate_models(ctx: PipelineContext, snapshot_id: str, run_id: str) -> TrainingReport:
    """Challenger, baseline and champion on the same test rows; completes the run's bundle."""
    cfg = ctx.cfg
    with _failing_run(ctx, run_id):
        booster, spec = _load_model(ctx, run_id)
        calibrator = IsotonicCalibrator.from_json(
            ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "calibrator.json")
        )
        frames = _frames(ctx, snapshot_id, SPLITS)
        labels = {name: _labels(frame) for name, frame in frames.items()}
        features = {name: build_features(frame, spec) for name, frame in frames.items()}
        p_late = {
            name: calibrator.apply(predict_raw(booster, feats))  # type: ignore[arg-type]
            for name, feats in features.items()
        }
        slices = slice_frame(features["test"])

        prefix = snapshot_prefix(snapshot_id, ctx.root) + BASELINE_DIR
        baseline_spec = FeatureSpec.from_json(ctx.data_store.get_bytes(prefix + "feature_spec.json"))
        baseline = BaselineModel.from_json(ctx.data_store.get_bytes(prefix + "baseline.json"))
        baseline_p = baseline.predict(build_features(frames["test"], baseline_spec))

        pointer = ObjectStorePointer(ctx.model_store, ctx.root).get()
        champion_version = pointer.champion_version if pointer else None
        champion_test = None
        if champion_version is not None:
            champion = load_bundle(ctx.model_store, champion_version, ctx.root)
            champion_test = _evaluate(ctx, labels["test"], champion.predict(frames["test"]), slices)

        manifest = _snapshot(ctx, snapshot_id)
        report = TrainingReport(
            snapshot_id=snapshot_id,
            spec_hash=spec.spec_hash,
            git_sha=ctx.git_sha,
            trained_at=datetime.now(UTC),
            train_start=manifest.splits["train"].start,
            train_end=manifest.splits["train"].end,
            test_rows=len(frames["test"]),
            challenger_valid=_evaluate(ctx, labels["valid"], p_late["valid"], slice_frame(features["valid"])),
            challenger_test=_evaluate(ctx, labels["test"], p_late["test"], slices),
            baseline_test=_evaluate(ctx, labels["test"], baseline_p, slices),
            champion_version=champion_version,
            champion_test=champion_test,
        )
        metrics = _flat_metrics("valid", report.challenger_valid)
        metrics |= _flat_metrics("test", report.challenger_test)
        metrics |= _flat_metrics("baseline_test", report.baseline_test)
        if report.champion_test is not None:
            metrics |= _flat_metrics("champion_test", report.champion_test)
        ctx.tracker.log_metrics(run_id, metrics)
        card = render_model_card(
            report,
            model_name=cfg.registry.model_name,
            params=_chosen_params(ctx, run_id),
            risk=cfg.risk_thresholds,
        )
        files = {
            "metrics.json": report.model_dump_json(indent=2).encode(),
            "model_card.md": card.encode(),
            "reference_sample.parquet": _reference_sample(
                ctx, features["train"], labels["train"], p_late["train"]
            ),
        }
        for name, data in files.items():
            ctx.tracker.log_bytes(run_id, BUNDLE_DIR + name, data)
        ctx.tracker.log_bytes(
            run_id, "calibration_bins.json",
            _json([b.model_dump() for b in report.challenger_test.calibration]),
        )
        ctx.tracker.log_bytes(run_id, SMOKE_FILE, _json(p_late["test"][:SMOKE_ROWS].tolist()))
    return report
```
Helper used for the model card (the grid point with the lowest valid log loss):
```python
def _chosen_params(ctx: PipelineContext, run_id: str) -> dict[str, object]:
    grid = json.loads(ctx.tracker.load_bytes(run_id, "grid_scores.json"))
    best = min(grid, key=lambda score: score["valid_logloss"])
    return {**best["params"], "best_iteration": best["best_iteration"]}
```
Continue `pipeline.py`:
```python
def register_model(ctx: PipelineContext, run_id: str) -> str:
    """Register `bundle/` as a new model version with alias @challenger."""
    name = ctx.cfg.registry.model_name
    with _failing_run(ctx, run_id):
        version = ctx.tracker.register_version(name, run_id, BUNDLE_DIR.rstrip("/"))
        ctx.tracker.set_alias(name, CHALLENGER, version)
        ctx.tracker.set_tags(run_id, {"model_version": version})
    return version


def run_gate(ctx: PipelineContext, run_id: str, version: str) -> GateDecision:
    with _failing_run(ctx, run_id):
        report = TrainingReport.model_validate_json(
            ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "metrics.json")
        )
        decision = evaluate_gate(report, ctx.cfg.gate)
        status = "passed" if decision.passed else "rejected"
        ctx.tracker.log_bytes(run_id, GATE_FILE, decision.model_dump_json(indent=2).encode())
        ctx.tracker.set_tags(run_id, {"gate": status})
        ctx.tracker.set_version_tags(ctx.cfg.registry.model_name, version, {"gate": status})
    return decision


def record_rejection(ctx: PipelineContext, run_id: str, version: str) -> None:
    """Rejected challenger: log why and close the run (the pipeline still succeeds)."""
    decision = GateDecision.model_validate_json(ctx.tracker.load_bytes(run_id, GATE_FILE))
    _log.info(
        "challenger rejected",
        extra={"version": version, "failed": decision.failed, "champion": decision.champion_version},
    )
    ctx.tracker.finish_run(run_id)


def release_model(ctx: PipelineContext, run_id: str, version: str) -> PointerState:
    with _failing_run(ctx, run_id):
        files = {name: ctx.tracker.load_bytes(run_id, BUNDLE_DIR + name) for name in BUNDLE_FILES}
        report = TrainingReport.model_validate_json(files["metrics.json"])
        manifest = build_manifest(
            files, model_name=ctx.cfg.registry.model_name, version=version, run_id=run_id, report=report
        )
        return release_version(
            ctx.model_store,
            ObjectStorePointer(ctx.model_store, ctx.root),
            ctx.tracker,
            manifest=manifest,
            files=files,
            root=ctx.root,
        )


def smoke_test(ctx: PipelineContext, snapshot_id: str, run_id: str, version: str) -> None:
    """Released bundle (checksums verified) reproduces the evaluated test predictions.

    Raises:
        ArtifactIntegrityError: if predictions differ or leave [0, 1].
    """
    with _failing_run(ctx, run_id):
        bundle = load_bundle(ctx.model_store, version, ctx.root)
        expected = np.asarray(json.loads(ctx.tracker.load_bytes(run_id, SMOKE_FILE)), dtype=float)
        test = _frames(ctx, snapshot_id, ("test",))["test"].head(len(expected))
        got = bundle.predict(test)
        in_range = bool(np.all((got >= 0) & (got <= 1)))
        if not in_range or float(np.max(np.abs(got - expected))) > SMOKE_TOLERANCE:
            raise ArtifactIntegrityError(f"smoke test failed for models/{version}")
    ctx.tracker.finish_run(run_id)


def run_training_pipeline(ctx: PipelineContext) -> PipelineResult:
    """All steps in one process (`make train`); the DAG runs the same steps as tasks."""
    snapshot_id = build_training_set(ctx).snapshot_id
    validate_snapshot(ctx, snapshot_id)
    train_baseline(ctx, snapshot_id)
    run_id = train_model(ctx, snapshot_id)
    calibrate_model(ctx, snapshot_id, run_id)
    evaluate_models(ctx, snapshot_id, run_id)
    version = register_model(ctx, run_id)
    decision = run_gate(ctx, run_id, version)
    if decision.passed:
        release_model(ctx, run_id, version)
        smoke_test(ctx, snapshot_id, run_id, version)
    else:
        record_rejection(ctx, run_id, version)
    pointer = ObjectStorePointer(ctx.model_store, ctx.root).get()
    return PipelineResult(
        snapshot_id=snapshot_id,
        run_id=run_id,
        version=version,
        promoted=decision.passed,
        decision=decision,
        champion_version=pointer.champion_version if pointer else None,
    )
```
Type the booster as `lgb.Booster` (import `lightgbm as lgb`) in `_load_model` instead of `object` and drop the
two `# type: ignore[arg-type]` comments. Remove the `__all__` line if ruff/mypy do not need it (tests import
`pipeline.snapshot_prefix`, which is a normal module attribute after the import).

`src/dbdelay/training/run_train.py`:
```python
"""`make train`: snapshot → train → calibrate → evaluate → register → gate → release (Phase 4)."""

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path

from dbdelay.config import get_settings
from dbdelay.logging import get_logger
from dbdelay.training.config import load_training_config
from dbdelay.training.pipeline import make_context, run_training_pipeline
from dbdelay.training.tracking import MlflowTracker


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the training pipeline in-process.")
    parser.add_argument("--config", type=Path, default=settings.training_config_file)
    args = parser.parse_args(argv)
    cfg = load_training_config(args.config)
    log = get_logger("training")
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_context(
            cfg, Path(tmp), settings=settings, tracker=MlflowTracker(settings.mlflow_tracking_uri)
        )
        result = run_training_pipeline(ctx)
    log.info(
        "training pipeline finished",
        extra={
            "snapshot_id": result.snapshot_id,
            "run_id": result.run_id,
            "version": result.version,
            "promoted": result.promoted,
            "failed_checks": result.decision.failed,
            "champion_version": result.champion_version,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`Makefile` — add `train` to `.PHONY` and:
```make
train: ## Train, evaluate, gate and (if it passes) release a model — same steps as the DAG (Phase 4)
	uv run python -m dbdelay.training.run_train
```

- [ ] **Step 5: Run — expect PASS**: `uv run pytest tests/unit -q && uv run mypy && uv run ruff check .`

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/dbdelay/training Makefile tests/unit/test_pipeline.py
git commit -m "feat(training): pipeline steps sharing one mlflow run, make train"
```

---

### Task 10: Integration tests against MinIO + MLflow

**Files:**
- Create: `tests/integration/test_training_mlflow.py`

**Interfaces:**
- Consumes: `MlflowTracker`, `run_training_pipeline`, `rollback`, builders (`put_signal_silver`, `SIGNAL_CONFIG`,
  `WEAK_LIGHTGBM`).

- [ ] **Step 1: Start the stack**: `make up` (Docker Desktop running), wait for healthy.

- [ ] **Step 2: Write tests** — `tests/integration/test_training_mlflow.py`

```python
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.registry.release import CHAMPION, rollback
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.config import RegistryConfig
from dbdelay.training.pipeline import PipelineContext, run_training_pipeline
from dbdelay.training.tracking import MlflowTracker
from tests.builders import SIGNAL_CONFIG, WEAK_LIGHTGBM, put_signal_silver

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


def test_tracker_round_trip() -> None:
    tracker = MlflowTracker(get_settings().mlflow_tracking_uri)
    name = f"it-{uuid.uuid4().hex[:8]}"
    run_id = tracker.start_run(name, {"k": "v"})
    tracker.log_bytes(run_id, "bundle/a.txt", b"hello")
    assert tracker.load_bytes(run_id, "bundle/a.txt") == b"hello"
    tracker.log_snapshot(run_id, "2026-03-31_abcdef12", "f" * 64, "s3://bucket/x/")
    assert tracker.get_alias(name, CHAMPION) is None
    version = tracker.register_version(name, run_id, "bundle")
    tracker.set_alias(name, CHAMPION, version)
    tracker.set_version_tags(name, version, {"gate": "passed"})
    assert tracker.get_alias(name, CHAMPION) == version
    tracker.finish_run(run_id)


def test_promote_improve_reject_rollback(scratch: tuple[ObjectStore, str], tmp_path: Path) -> None:
    store, root = scratch
    put_signal_silver(store, root, n=200)
    tag = uuid.uuid4().hex[:8]
    cfg = SIGNAL_CONFIG.model_copy(
        update={"registry": RegistryConfig(model_name=f"it-{tag}", experiment=f"it-{tag}")}
    )
    tracker = MlflowTracker(get_settings().mlflow_tracking_uri)

    def ctx(config, sub):  # type: ignore[no-untyped-def]
        return PipelineContext(
            data_store=store, model_store=store, tracker=tracker, cfg=config,
            stations=tuple(load_stations(REPO / "configs" / "stations.yaml")),
            workdir=tmp_path / sub, root=root, git_sha="it",
        )

    weak = run_training_pipeline(ctx(cfg.model_copy(update={"lightgbm": WEAK_LIGHTGBM}), "1"))
    assert weak.promoted, weak.decision
    better = run_training_pipeline(ctx(cfg, "2"))
    assert better.promoted, better.decision
    same = run_training_pipeline(ctx(cfg, "3"))
    assert not same.promoted
    assert same.decision.failed == ["brier_vs_champion"]

    pointer = ObjectStorePointer(store, root)
    state = pointer.get()
    assert state is not None
    assert (state.champion_version, state.previous_version) == (better.version, weak.version)
    assert tracker.get_alias(cfg.registry.model_name, CHAMPION) == better.version

    back = rollback(store, pointer, tracker, model_name=cfg.registry.model_name, root=root)
    assert back.champion_version == weak.version
    assert tracker.get_alias(cfg.registry.model_name, CHAMPION) == weak.version
```
MLflow experiments/models named `it-<hex>` stay in the local MLflow DB (no delete API used by the project) —
acceptable for a local stack; mention in the phase doc.

- [ ] **Step 3: Run**: `make test-integration`
Expected: all integration tests pass (Phase 2/3 ones too). If MLflow returns a different error code for a
missing alias, adjust `_MISSING_CODES` and its unit test together.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add tests/integration/test_training_mlflow.py src/dbdelay/training/tracking.py tests/unit/test_tracking.py
git commit -m "test(integration): pipeline promote, improve, reject and rollback on minio + mlflow"
```

---

### Task 11: Airflow DAG `training_pipeline` + image

**Files:**
- Create: `pipelines/airflow/dags/training_pipeline.py`
- Modify: `pipelines/airflow/Dockerfile`, `docker-compose.yml`, `pipelines/airflow/tests/check_dags.py`,
  `Makefile` (`train-dag`, export `GIT_SHA`)

**Interfaces:**
- Consumes: `make_context`, every step in `dbdelay.training.pipeline`, `load_training_config`,
  `Settings.training_config_file`.

- [ ] **Step 1: Write the DAG** — `pipelines/airflow/dags/training_pipeline.py`

```python
"""training_pipeline — snapshot → train → calibrate → evaluate → register → gate → release
(docs/architecture.md §5.2). `sync_live_silver` joins in Phase 7.

Thin orchestration only; every step is a function in `dbdelay.training.pipeline`.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from airflow.sdk import dag, task


def _run(step: Callable[..., Any], *args: str) -> Any:
    """Run one pipeline step with a fresh context and temp dir."""
    import tempfile
    from pathlib import Path

    from dbdelay.config import get_settings
    from dbdelay.training.config import load_training_config
    from dbdelay.training.pipeline import make_context

    cfg = load_training_config(get_settings().training_config_file)
    with tempfile.TemporaryDirectory() as tmp:
        return step(make_context(cfg, Path(tmp)), *args)


@dag(
    dag_id="training_pipeline",
    schedule="@monthly",
    catchup=False,
    max_active_runs=1,
    tags=["phase-4", "training"],
    default_args={"retries": 0},
)
def training_pipeline() -> None:
    @task(retries=1, retry_delay=timedelta(minutes=2))
    def build_training_set() -> str:
        from dbdelay.training import pipeline

        return str(_run(pipeline.build_training_set).snapshot_id)

    @task
    def validate_snapshot(snapshot_id: str) -> str:
        from dbdelay.training import pipeline

        _run(pipeline.validate_snapshot, snapshot_id)
        return snapshot_id

    @task
    def train_baseline(snapshot_id: str) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.train_baseline, snapshot_id)

    @task(execution_timeout=timedelta(hours=3))
    def train_lightgbm(snapshot_id: str) -> str:
        from dbdelay.training import pipeline

        return str(_run(pipeline.train_model, snapshot_id))

    @task
    def calibrate(snapshot_id: str, run_id: str) -> str:
        from dbdelay.training import pipeline

        _run(pipeline.calibrate_model, snapshot_id, run_id)
        return run_id

    @task
    def evaluate(snapshot_id: str, run_id: str) -> str:
        from dbdelay.training import pipeline

        _run(pipeline.evaluate_models, snapshot_id, run_id)
        return run_id

    @task
    def register(run_id: str) -> dict[str, str]:
        from dbdelay.training import pipeline

        return {"run_id": run_id, "version": str(_run(pipeline.register_model, run_id))}

    @task.branch
    def gate(registered: dict[str, str]) -> str:
        from dbdelay.training import pipeline

        decision = _run(pipeline.run_gate, registered["run_id"], registered["version"])
        return "release" if decision.passed else "record_rejection"

    @task
    def record_rejection(registered: dict[str, str]) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.record_rejection, registered["run_id"], registered["version"])

    @task
    def release(registered: dict[str, str]) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.release_model, registered["run_id"], registered["version"])

    @task
    def smoke_test(snapshot_id: str, registered: dict[str, str]) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.smoke_test, snapshot_id, registered["run_id"], registered["version"])

    snapshot_id = validate_snapshot(build_training_set())
    baseline = train_baseline(snapshot_id)
    evaluated = evaluate(snapshot_id, calibrate(snapshot_id, train_lightgbm(snapshot_id)))
    baseline >> evaluated
    registered = register(evaluated)
    branch = gate(registered)
    released = release(registered)
    branch >> [record_rejection(registered), released]
    released >> smoke_test(snapshot_id, registered)


training_pipeline()
```

- [ ] **Step 2: Image + compose + Makefile**

`pipelines/airflow/Dockerfile` — before `USER airflow` (first line after `FROM`) insert:
```dockerfile
# LightGBM wheels need the OpenMP runtime.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*
```
change the install target to `"/opt/dbdelay[pipelines,training]"`, update the header comment
("+ dbdelay[pipelines,training] (Phase 2, 4)"), and append at the end:
```dockerfile
# Last, so a new commit only rebuilds this layer. Logged as the `git_sha` run tag.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
```
If pip reports a conflict between the Airflow constraints and `mlflow-skinny`/`lightgbm` dependencies, drop only
the conflicting pin from the constraints (same `grep -v` pattern as pandas) and document it in the comment.

`docker-compose.yml` — in `x-airflow-common.build` add `args: {GIT_SHA: "${GIT_SHA:-unknown}"}`; in
`x-airflow-env` add:
```yaml
  MODELS_BUCKET: ${MODELS_BUCKET:-puenktlich-local}
  MLFLOW_TRACKING_URI: http://mlflow:5000
  TRAINING_CONFIG_FILE: /opt/airflow/configs/training.yaml
```

`Makefile` — under `COMPOSE :=` add:
```make
# Baked into the Airflow image as GIT_SHA (MLflow run tag `git_sha`).
export GIT_SHA := $(shell git rev-parse --short HEAD)
```
add `train-dag` to `.PHONY` and:
```make
train-dag: ## Unpause and trigger training_pipeline in Airflow (Phase 4)
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags unpause training_pipeline
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags trigger training_pipeline
```

- [ ] **Step 3: Extend `pipelines/airflow/tests/check_dags.py`** (before the final `if errors:`):

```python
TRAINING_TASKS = {
    "build_training_set", "validate_snapshot", "train_baseline", "train_lightgbm", "calibrate",
    "evaluate", "register", "gate", "record_rejection", "release", "smoke_test",
}
training = bag.dags.get("training_pipeline")
if training is None:
    errors.append("training_pipeline not found")
else:
    found = {t.task_id for t in training.tasks}
    if found != TRAINING_TASKS:
        errors.append(f"unexpected training tasks: {sorted(found)}")
    else:
        downstream = {t.task_id: set(t.downstream_task_ids) for t in training.tasks}
        if downstream["gate"] != {"release", "record_rejection"}:
            errors.append(f"gate must branch to release/record_rejection: {downstream['gate']}")
        if "evaluate" not in downstream["train_baseline"]:
            errors.append("evaluate must wait for train_baseline")
        if downstream["release"] != {"smoke_test"}:
            errors.append("smoke_test must follow release")
```
Update the module docstring to "…assert the DAGs' shape".

- [ ] **Step 4: Build and check**

```bash
make airflow-env
make airflow-up        # rebuilds the image with lightgbm + mlflow-skinny
make test-dags
```
Expected: `DAG check passed`. Also `uv run ruff check pipelines && uv run ruff format --check pipelines`.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add pipelines/airflow docker-compose.yml Makefile
git commit -m "feat(airflow): training_pipeline dag with gate branch, image with training extra"
```

---

### Task 12: Real runs, notebook, docs, review, branch record

**Files:**
- Create: `scripts/make_training_notebook.py`, `notebooks/03_training.ipynb`, `Phases/phase-4-training.md`
- Modify: `CHANGELOG.md`, `Phases/README.md`, `docs/architecture.md` (§3.2 pointer key, §5.2 note on
  `sync_live_silver`, §5.4 tie rule, §6 manifest covers all files), `docs/phases.md` (tick Phase 4 tasks),
  `README.md` (make train / rollback / train-dag), `CLAUDE.md` (status + session log)

- [ ] **Step 1: Real `make train`** (stack up, PC awake)

Run: `make train` → note snapshot id, run id, version, promoted, test Brier/AUC vs baseline (from the log line and
MLflow UI http://localhost:5000). Expected first run: promoted **only if** test Brier ≤ 0.1444. If not promoted:
stop, report the measured numbers to the owner, do not change the gate.

- [ ] **Step 2: Real DAG runs** — `make train-dag` twice (wait for the first to finish, UI :8080).
Expected: first DAG run → rejected (the host `make train` champion already exists and the model is identical —
or promoted if the Airflow snapshot id differs and the model improved; record what happened), second → rejected,
both runs green. Record the run ids and gate results. Then `make rollback` once and `make rollback` again
(roll forward), recording pointer states.

- [ ] **Step 3: Notebook generator** — `scripts/make_training_notebook.py` (same pattern as
`make_baseline_notebook.py`; dev-only matplotlib):

```python
"""Write notebooks/03_training.ipynb (champion metrics, calibration and importance plots)."""

from pathlib import Path

import nbformat

CELLS = [
    nbformat.v4.new_markdown_cell(
        "# 03 — Training\n\nCurrent champion from `models/_pointer.json`: metrics vs baseline, "
        "calibration curve and feature importance (from its MLflow run). No row data is shown."
    ),
    nbformat.v4.new_code_cell(
        "import json\nimport os\nfrom pathlib import Path\n\n"
        "import matplotlib.pyplot as plt\nimport pandas as pd\n\n"
        "if Path.cwd().name == 'notebooks':\n    os.chdir('..')\n\n"
        "from dbdelay.config import get_settings\n"
        "from dbdelay.registry.artifacts import load_bundle\n"
        "from dbdelay.registry.pointer import ObjectStorePointer\n"
        "from dbdelay.storage import ObjectStore, make_s3_client\n"
        "from dbdelay.training.evaluate import TrainingReport\n"
        "from dbdelay.training.tracking import MlflowTracker\n\n"
        "settings = get_settings()\n"
        "store = ObjectStore(make_s3_client(settings), settings.models_bucket)\n"
        "version = ObjectStorePointer(store).get().champion_version\n"
        "bundle = load_bundle(store, version)\n"
        "tracker = MlflowTracker(settings.mlflow_tracking_uri)\n"
        "report = TrainingReport.model_validate_json(\n"
        "    tracker.load_bytes(bundle.manifest.mlflow_run_id, 'bundle/metrics.json'))\n"
        "version, report.snapshot_id"
    ),
    nbformat.v4.new_code_cell(
        "rows = {'challenger': report.challenger_test, 'baseline': report.baseline_test}\n"
        "if report.champion_test is not None:\n    rows['previous champion'] = report.champion_test\n"
        "pd.DataFrame({k: v.overall.model_dump() for k, v in rows.items()}).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "bins = pd.DataFrame([b.model_dump() for b in report.challenger_test.calibration])\n"
        "fig, ax = plt.subplots(figsize=(5, 5))\n"
        "ax.plot([0, 1], [0, 1], linestyle='--', color='grey', label='perfect')\n"
        "ax.plot(bins['mean_pred'], bins['observed_rate'], marker='o', label='challenger (test)')\n"
        "ax.set_xlabel('mean predicted p_late')\nax.set_ylabel('observed late rate')\n"
        "ax.legend()\nax.set_title('Calibration (test)')\nplt.show()"
    ),
    nbformat.v4.new_code_cell(
        "importance = json.loads(\n"
        "    tracker.load_bytes(bundle.manifest.mlflow_run_id, 'feature_importance.json'))\n"
        "gain = pd.Series(importance['gain']).sort_values()\n"
        "ax = gain.plot.barh(figsize=(6, 4), title='Feature importance (gain)')\nplt.show()"
    ),
]


def main() -> None:
    notebook = nbformat.v4.new_notebook(cells=CELLS)
    path = Path("notebooks/03_training.ipynb")
    nbformat.write(notebook, path)


if __name__ == "__main__":
    main()
```
Run: `uv run python scripts/make_training_notebook.py && uv run jupyter nbconvert --to notebook --execute --inplace notebooks/03_training.ipynb`
(executes against the stack; outputs saved), then `uv run ruff check --fix . && uv run ruff format .`.

- [ ] **Step 4: Docs**
  - `docs/architecture.md`: §3.2 add `models/_pointer.json` (local champion pointer until SSM) under MinIO;
    §5.2 note "Phase 4: `sync_live_silver` skipped until Phase 7; `smoke_test` = artifact smoke test until Phase 5";
    §5.4 add "Tie with the champion (equal Brier) → rejected"; §6 note "`files` lists every file in the folder
    except `manifest.json`" and the pointer object.
  - `docs/phases.md`: tick Phase 4 tasks.
  - `README.md`: commands `make train`, `make train-dag`, `make rollback`.
  - `CHANGELOG.md`: Phase 4 entry — Built · Key decisions · Tested (commands + results) · Known gaps · Docs
    touched — measured numbers only.
  - `Phases/phase-4-training.md` + one line in `Phases/README.md`: goal, what was built (files + why), how it
    works (diagram), key decisions + reasons, how it was tested (commands + real results), how to run, known
    gaps (MLflow test experiments not cleaned up, valid reuse, `month` extrapolation, snapshot id differs in the
    Airflow image, run stays RUNNING if a DAG task is killed), what's next (Phase 5).
  - `CLAUDE.md`: Status (Phase 4 done, waiting for merge) + session log line.

- [ ] **Step 5: Verification** (superpowers:verification-before-completion)

```bash
make lint && make typecheck && make test && make test-integration && make test-dags
uv run pre-commit run --all-files
```
All must pass; record test counts.

- [ ] **Step 6: Final review** — run the `code-reviewer` agent (or a `general-purpose` agent told to follow
`.claude/agents/code-reviewer.md`) on `git diff main...phase-4/training`; fix confirmed Critical/Important
findings (superpowers:receiving-code-review), list Minor ones as deferred in CHANGELOG.

- [ ] **Step 7: Branch record**: commit docs; write `.git/MERGE_SUMMARY.txt` (same summary as CHANGELOG);
`git push -u origin phase-4/training`. Never delete the branch. No Claude co-author trailer.

```bash
git add -A
git commit -m "docs(phase-4): changelog, phase doc, notebook and real training results"
git push -u origin phase-4/training
```

# Phase 2 — HF backfill (`backfill_history`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A re-runnable Airflow 3 DAG that downloads Hugging Face monthly history into MinIO bronze and turns it into validated, deduplicated silver partitions (plus quarantine and a quality report) for the 30 supported stations.

**Architecture:** All logic lives in `src/dbdelay/data/` (`months`, `stations`, `quality`, `silver`, `hf_backfill`) and is unit-tested without Docker or Airflow. DuckDB filters each bronze parquet file down to our stations; pandas conforms rows to the frozen silver contract. A thin DAG (`pipelines/airflow/dags/backfill_history.py`) maps `ingest` and `build_silver` over the requested months. Airflow runs as split services (api-server, scheduler, dag-processor, LocalExecutor) in a compose profile.

**Tech Stack:** Python 3.12, pandas 2.3, pyarrow, DuckDB, Pandera, pydantic v2, huggingface_hub, boto3/moto, Apache Airflow 3.3.2 (Docker), Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-phase-2-hf-backfill-design.md`

## Global Constraints

- Python `>=3.12,<3.13`; run everything via `uv run …` / `make` from `puenktlich/`. In Git Bash first run: `export PATH="$PATH:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe/bin"; unset VIRTUAL_ENV`.
- `mypy --strict` on `src/`; ruff rules `E,F,I,B,UP,S,SIM,RUF,PL`, line length 100. No `print()` in `src/`; log with `dbdelay.logging.get_logger`.
- Silver contract is frozen (`src/dbdelay/data/schemas.py`, architecture §3.3, ADR 0001). **Do not edit `schemas.py`.** Every silver frame written must pass `validate_silver`.
- `event_id` = lowercase SHA-1 hex of `f"{eva}|{ride_id}|{planned_departure_utc:%Y-%m-%dT%H:%MZ}"`.
- Label: `is_late = delay_min >= 6`; cancelled ⇒ `delay_min` and `is_late` null. DST ambiguous/nonexistent planned or changed time ⇒ drop + count.
- Storage keys (bucket from `DATA_BUCKET`, optional `root` prefix for tests):
  - `bronze/hf/month=YYYY-MM/data.parquet`, `bronze/hf/month=YYYY-MM/_manifest.json`
  - `silver/departures/source=hf/date=YYYY-MM-DD/part-0.parquet` — **every day of the month, even if empty**
  - `silver/_quarantine/source=hf/month=YYYY-MM/part-0.parquet` — always written
  - `silver/_quality/source=hf/month=YYYY-MM.json`
- `ObjectStore` has no delete — overwrite by `put_bytes` only.
- Apache Airflow only inside Docker: `apache/airflow:3.3.2-python3.12@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1`. Never add Airflow to `pyproject.toml`.
- `huggingface_hub` stays in the dev group **and** is added to a new optional extra `pipelines`.
- Default window: `2025-12` … `2026-08` (9 months).
- Never read or print `.env`; never commit secrets. No `Co-Authored-By` trailers (owner rule).
- Commits: Conventional Commits on branch `phase-2/hf-backfill`; run `uv run pre-commit run --all-files` before each commit.

## Review Focus

1. **A month whose next month is not published yet (2026-08):** build still succeeds; `edge_complete.next` is `false`; last UTC hours of the month may be missing — pinned in Task 7 (`test_build_without_next_month_marks_edge_incomplete`).
2. **Re-running a month after the station list changes:** a day that had rows before and has none now must be overwritten with an empty file (no stale partition) — pinned in Task 2 (`test_write_silver_month_writes_every_day_even_empty`).
3. **Corrupted or partially uploaded bronze:** a checksum mismatch must stop the month, never produce silver — pinned in Task 7 (`test_checksum_mismatch_stops_the_month`).
4. **HF columns with NULLs** (e.g. `line_number`, `final_destination_station`, `departure_is_canceled`): nullable strings must stay `None` (not `NaN`) so the contract passes; a null cancel flag is quarantined — pinned in Tasks 3–4.
5. **Train crossing the spring-forward DST night** (HF delay computed on naive local times): must be quarantined as `delay_mismatch_utc`, not crash the whole month — pinned in Task 4 (`test_dst_straddling_delay_is_quarantined`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/dbdelay/data/months.py` (new) | `YYYY-MM` validation, month arithmetic, day lists, UTC bounds |
| `src/dbdelay/data/stations.py` (new) | Load/validate `configs/stations.yaml`; alias map |
| `src/dbdelay/config.py` (modify) | `stations_file` setting |
| `src/dbdelay/data/quality.py` (new) | Row-level quarantine split; `QualityReport` + `build_report` |
| `src/dbdelay/data/silver.py` (new) | Raw input contract, `make_event_id`, `conform_hf`, parquet writer, partition keys, `content_hash` |
| `src/dbdelay/data/hf_backfill.py` (new) | HF download → bronze + manifest; DuckDB read of M-1/M/M+1; `build_silver_month` orchestration |
| `tests/builders.py` (new) | Synthetic HF rows/frames/parquet and silver frames for tests |
| `tests/unit/test_months.py`, `test_stations.py`, `test_quality.py`, `test_silver.py`, `test_conform.py`, `test_report.py`, `test_hf_ingest.py`, `test_hf_build.py`, `test_ensure_airflow_env.py` (new) | Unit tests |
| `tests/integration/test_hf_backfill_minio.py` (new) | End-to-end month twice against MinIO |
| `pipelines/airflow/Dockerfile`, `pipelines/airflow/requirements.txt`, `pipelines/airflow/init/create_db.py` (new) | Airflow image with `dbdelay[pipelines]` |
| `pipelines/airflow/dags/backfill_history.py` (new) | Thin DAG |
| `pipelines/airflow/tests/check_dags.py` (new) | DAG import/structure check run inside the container |
| `scripts/ensure_airflow_env.py` (new) | Append missing Airflow secrets to `.env` without printing them |
| `docker-compose.yml`, `Makefile`, `.env.example`, `pyproject.toml`, `uv.lock` (modify) | Airflow services, targets, placeholders, extra |

---

### Task 1: Month helpers, `stations_file` setting, station loader

**Files:**
- Create: `src/dbdelay/data/months.py`, `src/dbdelay/data/stations.py`
- Modify: `src/dbdelay/config.py` (add field after `mlflow_tracking_uri`)
- Test: `tests/unit/test_months.py`, `tests/unit/test_stations.py`, `tests/unit/test_config.py` (append one test)

**Interfaces:**
- Produces: `validate_month(month: str) -> str`, `shift_month(month: str, delta: int) -> str`, `month_days(month: str) -> list[datetime.date]`, `month_bounds_utc(month: str) -> tuple[pd.Timestamp, pd.Timestamp]`; `Station(eva, name, state, hf_aliases: tuple[str, ...])`, `load_stations(path: Path) -> list[Station]`, `alias_map(stations: Sequence[Station]) -> dict[str, str]`; `Settings.stations_file: Path` (default `Path("configs/stations.yaml")`).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_months.py`:
```python
from datetime import date

import pandas as pd
import pytest

from dbdelay.data.months import month_bounds_utc, month_days, shift_month, validate_month
from dbdelay.errors import ConfigError


@pytest.mark.parametrize("month", ["2026-01", "2025-12"])
def test_validate_month_accepts_yyyy_mm(month: str) -> None:
    assert validate_month(month) == month


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-01", "2026-01\n", ""])
def test_validate_month_rejects_other_strings(month: str) -> None:
    with pytest.raises(ConfigError):
        validate_month(month)


@pytest.mark.parametrize(
    ("month", "delta", "expected"),
    [("2026-01", -1, "2025-12"), ("2025-12", 1, "2026-01"), ("2026-03", 0, "2026-03")],
)
def test_shift_month_crosses_years(month: str, delta: int, expected: str) -> None:
    assert shift_month(month, delta) == expected


def test_month_days_covers_the_whole_month() -> None:
    days = month_days("2026-02")
    assert days[0] == date(2026, 2, 1)
    assert days[-1] == date(2026, 2, 28)
    assert len(days) == 28


def test_month_bounds_are_utc_half_open() -> None:
    start, end = month_bounds_utc("2026-03")
    assert start == pd.Timestamp("2026-03-01", tz="UTC")
    assert end == pd.Timestamp("2026-04-01", tz="UTC")
```

`tests/unit/test_stations.py`:
```python
from pathlib import Path

import pytest

from dbdelay.data.stations import alias_map, load_stations
from dbdelay.errors import ConfigError

REPO_STATIONS = Path(__file__).resolve().parents[2] / "configs" / "stations.yaml"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stations.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_repo_station_list_loads() -> None:
    stations = load_stations(REPO_STATIONS)
    assert len(stations) == 30
    berlin = next(s for s in stations if s.eva == "8098160")
    assert berlin.hf_aliases == ("8011160",)


def test_alias_map_points_aliases_and_evas_to_the_canonical_eva() -> None:
    mapping = alias_map(load_stations(REPO_STATIONS))
    assert mapping["8011160"] == "8098160"
    assert mapping["8098160"] == "8098160"
    assert len(mapping) == 31


@pytest.mark.parametrize(
    "body",
    [
        'schema_version: 1\nstations:\n  - {eva: "08000105", name: "F", state: "HE"}\n',
        'schema_version: 1\nstations:\n  - {eva: "8000105", name: "F", state: "HE", x: 1}\n',
        "schema_version: 2\nstations: []\n",
        'schema_version: 1\nstations:\n  - {eva: "8000105", name: "F", state: "HE"}\n'
        '  - {eva: "8000261", name: "M", state: "BY", hf_aliases: ["8000105"]}\n',
    ],
)
def test_invalid_station_files_raise_config_error(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_stations(_write(tmp_path, body))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_stations(tmp_path / "nope.yaml")
```

Append to `tests/unit/test_config.py`:
```python
def test_stations_file_defaults_to_repo_config_and_reads_env(monkeypatch) -> None:
    from pathlib import Path

    from dbdelay.config import Settings

    assert Settings(_env_file=None).stations_file == Path("configs/stations.yaml")  # type: ignore[call-arg]
    monkeypatch.setenv("STATIONS_FILE", "/opt/airflow/configs/stations.yaml")
    assert Settings(_env_file=None).stations_file == Path("/opt/airflow/configs/stations.yaml")  # type: ignore[call-arg]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_months.py tests/unit/test_stations.py tests/unit/test_config.py -q`
Expected: collection errors `ModuleNotFoundError: No module named 'dbdelay.data.months'` / `'dbdelay.data.stations'`, and `AttributeError: ... stations_file`.

- [ ] **Step 3: Implement**

`src/dbdelay/data/months.py`:
```python
"""Month strings ("YYYY-MM") that address HF files and silver partitions."""

import re
from datetime import date, timedelta

import pandas as pd

from dbdelay.errors import ConfigError

_MONTH = re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])")


def validate_month(month: str) -> str:
    """Return ``month`` unchanged if it is ``YYYY-MM``.

    Raises:
        ConfigError: for anything else.
    """
    if not _MONTH.fullmatch(month):
        raise ConfigError(f"month must look like YYYY-MM, got {month!r}")
    return month


def shift_month(month: str, delta: int) -> str:
    """Return the month ``delta`` months after ``month`` (negative = before)."""
    year, mon = (int(part) for part in validate_month(month).split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_days(month: str) -> list[date]:
    """All calendar days of ``month``."""
    first = date.fromisoformat(f"{validate_month(month)}-01")
    following = date.fromisoformat(f"{shift_month(month, 1)}-01")
    return [first + timedelta(days=offset) for offset in range((following - first).days)]


def month_bounds_utc(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Half-open UTC interval ``[first day 00:00, next month's first day 00:00)``."""
    start = pd.Timestamp(f"{validate_month(month)}-01", tz="UTC")
    end = pd.Timestamp(f"{shift_month(month, 1)}-01", tz="UTC")
    return start, end
```

`src/dbdelay/data/stations.py`:
```python
"""Supported stations from ``configs/stations.yaml`` (Phase 1 decision, ADR 0001)."""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from dbdelay.errors import ConfigError

_EVA = re.compile(r"[1-9][0-9]{6}")


def _check_eva(value: str) -> str:
    if not _EVA.fullmatch(value):
        raise ValueError("EVA must be 7 digits without a leading zero")
    return value


class Station(BaseModel):
    """One supported station; ``hf_aliases`` are retired EVAs whose history belongs to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eva: str
    name: str
    state: str
    hf_aliases: tuple[str, ...] = ()

    @field_validator("eva")
    @classmethod
    def _eva_form(cls, value: str) -> str:
        return _check_eva(value)

    @field_validator("hf_aliases")
    @classmethod
    def _alias_form(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_check_eva(alias) for alias in value)


class _StationsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    stations: list[Station]


def load_stations(path: Path) -> list[Station]:
    """Load and validate the station list.

    Raises:
        ConfigError: if the file is missing, malformed, or EVAs/aliases collide.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read station list {path.name}") from exc
    try:
        parsed = _StationsFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid station list {path.name}: {exc.error_count()} errors") from None
    evas = [s.eva for s in parsed.stations] + [a for s in parsed.stations for a in s.hf_aliases]
    if len(set(evas)) != len(evas):
        raise ConfigError(f"duplicate EVA or alias in {path.name}")
    return parsed.stations


def alias_map(stations: Sequence[Station]) -> dict[str, str]:
    """Map every EVA and alias to the station's canonical EVA."""
    mapping = {s.eva: s.eva for s in stations}
    mapping.update({alias: s.eva for s in stations for alias in s.hf_aliases})
    return mapping
```

`src/dbdelay/config.py` — add `from pathlib import Path` to imports and, after `mlflow_tracking_uri: str = "http://localhost:5000"`:
```python

    # Station list (relative to the working directory; containers set an absolute path).
    stations_file: Path = Path("configs/stations.yaml")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_months.py tests/unit/test_stations.py tests/unit/test_config.py -q && uv run mypy`
Expected: all pass; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/data/months.py src/dbdelay/data/stations.py src/dbdelay/config.py tests/unit/test_months.py tests/unit/test_stations.py tests/unit/test_config.py
git commit -m "feat(data): month helpers and validated station loader"
```

---

### Task 2: Test builders, raw input contract, event id, silver writer

**Files:**
- Create: `src/dbdelay/data/silver.py` (first part), `tests/builders.py`
- Test: `tests/unit/test_silver.py`

**Interfaces:**
- Consumes: `month_days` (Task 1).
- Produces (in `dbdelay.data.silver`): `RAW_COLUMNS: tuple[str, ...]`, `RAW_DTYPES: dict[str, str]`, `SILVER_COLUMNS: tuple[str, ...]`, `SILVER_ARROW_SCHEMA: pa.Schema`, `make_event_id(eva: str, ride_id: str, planned_utc: pd.Timestamp) -> str`, `silver_key(day: date, root: str = "") -> str`, `quarantine_key(month: str, root: str = "") -> str`, `quality_key(month: str, root: str = "") -> str`, `to_parquet_bytes(df: pd.DataFrame, schema: pa.Schema | None = None) -> bytes`, `write_silver_month(silver: pd.DataFrame, store: ObjectStore, month: str, root: str = "") -> dict[str, int]`, `content_hash(df: pd.DataFrame) -> str`.
- Produces (in `tests.builders`): `hf_row(**overrides) -> dict[str, Any]`, `hf_frame(*rows) -> pd.DataFrame`, `hf_parquet_bytes(df) -> bytes`, `silver_frame(n: int = 3, day: str = "2026-03-10") -> pd.DataFrame`, `read_parquet_bytes(data: bytes) -> pd.DataFrame`.

- [ ] **Step 1: Write the builders and the failing tests**

`tests/builders.py`:
```python
"""Synthetic HF-shaped and silver-shaped frames for unit and integration tests."""

import io
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dbdelay.data.silver import RAW_COLUMNS, RAW_DTYPES, SILVER_ARROW_SCHEMA, make_event_id

HF_FILE_COLUMNS = RAW_COLUMNS[:-2]  # without ingested_at, _file_month


def hf_row(**overrides: Any) -> dict[str, Any]:
    """A valid, on-time Frankfurt departure on 2026-03-10 08:15 local (07:15 UTC)."""
    row: dict[str, Any] = {
        "eva": "08000105",
        "train_number": "15675",
        "line_number": "RB61",
        "final_destination_station": "Dieburg",
        "delay_in_min": 0,
        "departure_is_canceled": False,
        "train_type": "RB",
        "id": "-3640391175194580346-2603100815-1",
        "departure_planned_time": datetime(2026, 3, 10, 8, 15),
        "departure_change_time": datetime(2026, 3, 10, 8, 15),
        "ingested_at": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        "_file_month": "2026-03",
    }
    row.update(overrides)
    return row


def hf_frame(*rows: dict[str, Any]) -> pd.DataFrame:
    """Rows → frame with exactly the raw input dtypes ``conform_hf`` expects."""
    return pd.DataFrame(list(rows), columns=list(RAW_COLUMNS)).astype(RAW_DTYPES)


def hf_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Serialize HF-file columns the way the real monthly files look (ns timestamps)."""
    table = pa.Table.from_pandas(df[list(HF_FILE_COLUMNS)], preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def silver_frame(n: int = 3, day: str = "2026-03-10") -> pd.DataFrame:
    """``n`` valid on-time silver rows on ``day`` (UTC), distinct rides."""
    planned = [pd.Timestamp(f"{day} 07:00", tz="UTC") + pd.Timedelta(minutes=i) for i in range(n)]
    rides = [f"{100 + i}-2603100600" for i in range(n)]
    frame = pd.DataFrame(
        {
            "event_id": [make_event_id("8000105", r, p) for r, p in zip(rides, planned, strict=True)],
            "eva": ["8000105"] * n,
            "station_name": ["Frankfurt (Main) Hbf"] * n,
            "ride_id": rides,
            "stop_index": pd.Series([1] * n, dtype="int16"),
            "train_type": ["RB"] * n,
            "train_number": ["1"] * n,
            "line_number": [None] * n,
            "final_destination": ["Dieburg"] * n,
            "planned_departure_utc": pd.Series(planned, dtype="datetime64[us, UTC]"),
            "changed_departure_utc": pd.Series(planned, dtype="datetime64[us, UTC]"),
            "delay_min": pd.Series([0] * n, dtype="Int16"),
            "is_cancelled": [False] * n,
            "is_late": pd.Series([False] * n, dtype="boolean"),
            "source": ["hf"] * n,
            "ingested_at": pd.Series(
                [pd.Timestamp("2026-09-26 10:00", tz="UTC")] * n, dtype="datetime64[us, UTC]"
            ),
        }
    )
    return frame


def read_parquet_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(data))


__all__ = [
    "SILVER_ARROW_SCHEMA",
    "hf_frame",
    "hf_parquet_bytes",
    "hf_row",
    "read_parquet_bytes",
    "silver_frame",
]
```

`tests/unit/test_silver.py`:
```python
import hashlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import content_hash, make_event_id, silver_key, write_silver_month
from dbdelay.storage import ObjectStore, make_s3_client
from tests.builders import read_parquet_bytes, silver_frame

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    with mock_aws():
        client: S3Client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, BUCKET)


def test_event_id_is_sha1_of_the_frozen_format() -> None:
    planned = pd.Timestamp("2026-03-10 07:15:42", tz="UTC")
    expected = hashlib.sha1(
        b"8000105|123-2603100600|2026-03-10T07:15Z", usedforsecurity=False
    ).hexdigest()
    assert make_event_id("8000105", "123-2603100600", planned) == expected


def test_event_id_rejects_non_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="UTC"):
        make_event_id("8000105", "1-2603100600", pd.Timestamp("2026-03-10 08:15"))
    with pytest.raises(ValueError, match="UTC"):
        make_event_id(
            "8000105", "1-2603100600", pd.Timestamp("2026-03-10 08:15", tz="Europe/Berlin")
        )


def test_write_silver_month_writes_every_day_even_empty(store: ObjectStore) -> None:
    counts = write_silver_month(silver_frame(3), store, "2026-03")

    assert len(counts) == 31
    assert counts["2026-03-10"] == 3
    assert counts["2026-03-11"] == 0
    empty = read_parquet_bytes(store.get_bytes(silver_key(pd.Timestamp("2026-03-11").date())))
    assert empty.empty
    assert list(empty.columns) == list(silver_frame(1).columns)


def test_written_partition_round_trips_through_the_contract(store: ObjectStore) -> None:
    write_silver_month(silver_frame(3), store, "2026-03")

    back = read_parquet_bytes(store.get_bytes(silver_key(pd.Timestamp("2026-03-10").date())))

    validate_silver(back)
    assert content_hash(back) == content_hash(silver_frame(3))


def test_rewrite_produces_identical_bytes(store: ObjectStore) -> None:
    key = silver_key(pd.Timestamp("2026-03-10").date())
    write_silver_month(silver_frame(3), store, "2026-03")
    first = store.get_bytes(key)
    write_silver_month(silver_frame(3).iloc[::-1], store, "2026-03")  # different row order
    assert store.get_bytes(key) == first


def test_content_hash_ignores_row_order_but_not_values() -> None:
    frame = silver_frame(3)
    assert content_hash(frame) == content_hash(frame.iloc[::-1])
    changed = frame.copy()
    changed.loc[0, "train_number"] = "999"
    assert content_hash(changed) != content_hash(frame)


def test_root_prefix_is_applied() -> None:
    assert silver_key(pd.Timestamp("2026-03-01").date(), root="_t/") == (
        "_t/silver/departures/source=hf/date=2026-03-01/part-0.parquet"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_silver.py -q`
Expected: `ModuleNotFoundError: No module named 'dbdelay.data.silver'`.

- [ ] **Step 3: Implement `src/dbdelay/data/silver.py` (part 1)**

```python
"""HF → silver conform and silver partition writing (architecture §3.3, ADR 0001)."""

import hashlib
import io
from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dbdelay.data.months import month_days
from dbdelay.storage import ObjectStore

BERLIN = "Europe/Berlin"

# Raw input: HF file columns (as read by DuckDB) + provenance added by hf_backfill.
RAW_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_number",
    "line_number",
    "final_destination_station",
    "delay_in_min",
    "departure_is_canceled",
    "train_type",
    "id",
    "departure_planned_time",
    "departure_change_time",
    "ingested_at",
    "_file_month",
)
RAW_DTYPES: dict[str, str] = {
    "eva": "object",
    "train_number": "object",
    "line_number": "object",
    "final_destination_station": "object",
    "delay_in_min": "Int32",
    "departure_is_canceled": "boolean",
    "train_type": "object",
    "id": "object",
    "departure_planned_time": "datetime64[ns]",
    "departure_change_time": "datetime64[ns]",
    "ingested_at": "datetime64[us, UTC]",
    "_file_month": "object",
}
SILVER_COLUMNS: tuple[str, ...] = (
    "event_id",
    "eva",
    "station_name",
    "ride_id",
    "stop_index",
    "train_type",
    "train_number",
    "line_number",
    "final_destination",
    "planned_departure_utc",
    "changed_departure_utc",
    "delay_min",
    "is_cancelled",
    "is_late",
    "source",
    "ingested_at",
)
_TS = pa.timestamp("us", tz="UTC")
SILVER_ARROW_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("eva", pa.string()),
        ("station_name", pa.string()),
        ("ride_id", pa.string()),
        ("stop_index", pa.int16()),
        ("train_type", pa.string()),
        ("train_number", pa.string()),
        ("line_number", pa.string()),
        ("final_destination", pa.string()),
        ("planned_departure_utc", _TS),
        ("changed_departure_utc", _TS),
        ("delay_min", pa.int16()),
        ("is_cancelled", pa.bool_()),
        ("is_late", pa.bool_()),
        ("source", pa.string()),
        ("ingested_at", _TS),
    ]
)


def make_event_id(eva: str, ride_id: str, planned_utc: pd.Timestamp) -> str:
    """Primary key: SHA-1 hex of ``eva|ride_id|YYYY-MM-DDTHH:MMZ`` (architecture §3.3).

    Raises:
        ValueError: if ``planned_utc`` is not a UTC timestamp.
    """
    if planned_utc.tzinfo is None or planned_utc.utcoffset() != pd.Timedelta(0):
        raise ValueError("planned_utc must be a UTC timestamp")
    key = f"{eva}|{ride_id}|{planned_utc:%Y-%m-%dT%H:%M}Z"
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()


def silver_key(day: date, root: str = "") -> str:
    return f"{root}silver/departures/source=hf/date={day:%Y-%m-%d}/part-0.parquet"


def quarantine_key(month: str, root: str = "") -> str:
    return f"{root}silver/_quarantine/source=hf/month={month}/part-0.parquet"


def quality_key(month: str, root: str = "") -> str:
    return f"{root}silver/_quality/source=hf/month={month}.json"


def to_parquet_bytes(df: pd.DataFrame, schema: pa.Schema | None = None) -> bytes:
    """Deterministic parquet bytes (no index, zstd)."""
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def write_silver_month(
    silver: pd.DataFrame, store: ObjectStore, month: str, root: str = ""
) -> dict[str, int]:
    """Overwrite one partition per day of ``month`` (empty days included).

    Returns:
        Rows written per ISO day.
    """
    days = silver["planned_departure_utc"].dt.date
    counts: dict[str, int] = {}
    for day in month_days(month):
        part = silver[days == day].sort_values("event_id").reset_index(drop=True)
        part = part[list(SILVER_COLUMNS)]
        store.put_bytes(silver_key(day, root), to_parquet_bytes(part, SILVER_ARROW_SCHEMA))
        counts[day.isoformat()] = len(part)
    return counts


def content_hash(df: pd.DataFrame) -> str:
    """Order-independent SHA-256 of a silver frame's values."""
    ordered = df[list(SILVER_COLUMNS)].sort_values("event_id").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_silver.py -q && uv run mypy`
Expected: 7 passed; mypy success. If `test_written_partition_round_trips_through_the_contract` fails on dtypes, print `back.dtypes` and fix the writer (not the contract).

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/data/silver.py tests/builders.py tests/unit/test_silver.py
git commit -m "feat(data): silver partition writer, event id and content hash"
```

---

### Task 3: Row-level quarantine split

**Files:**
- Create: `src/dbdelay/data/quality.py` (first part)
- Test: `tests/unit/test_quality.py`

**Interfaces:**
- Consumes: raw frame shaped by `tests.builders.hf_frame` / `RAW_DTYPES` (Task 2).
- Produces: `QUARANTINE_REASONS: tuple[str, ...] = ("bad_id", "missing_train_type", "missing_cancel_flag", "missing_delay", "delay_mismatch")`, `split_quarantine(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]` (second frame = quarantined rows with extra column `quarantine_reason`; first matching reason wins in tuple order).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_quality.py`:
```python
from datetime import datetime

import pandas as pd
import pytest

from dbdelay.data.quality import QUARANTINE_REASONS, split_quarantine
from tests.builders import hf_frame, hf_row


def test_valid_rows_pass_through_unchanged() -> None:
    raw = hf_frame(hf_row(), hf_row(id="1-2603100815-2", delay_in_min=7,
                                    departure_change_time=datetime(2026, 3, 10, 8, 22)))
    ok, quarantined = split_quarantine(raw)
    pd.testing.assert_frame_equal(ok, raw)
    assert quarantined.empty
    assert "quarantine_reason" in quarantined.columns


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"id": "abc"}, "bad_id"),
        ({"id": None}, "bad_id"),
        ({"id": "1-2603100815-0"}, "bad_id"),
        ({"train_type": None}, "missing_train_type"),
        ({"train_type": "  "}, "missing_train_type"),
        ({"departure_is_canceled": None}, "missing_cancel_flag"),
        ({"delay_in_min": None}, "missing_delay"),
        ({"delay_in_min": 3}, "delay_mismatch"),
        ({"departure_change_time": None}, "delay_mismatch"),
    ],
)
def test_each_rule_quarantines_with_its_reason(overrides: dict[str, object], reason: str) -> None:
    ok, quarantined = split_quarantine(hf_frame(hf_row(**overrides)))
    assert ok.empty
    assert quarantined["quarantine_reason"].tolist() == [reason]


def test_first_reason_wins() -> None:
    _, quarantined = split_quarantine(hf_frame(hf_row(id="bad", train_type=None)))
    assert quarantined["quarantine_reason"].tolist() == ["bad_id"]


def test_cancelled_rows_need_no_delay_or_change_time() -> None:
    raw = hf_frame(hf_row(departure_is_canceled=True, delay_in_min=None,
                          departure_change_time=None))
    ok, quarantined = split_quarantine(raw)
    assert len(ok) == 1
    assert quarantined.empty


def test_reason_catalogue_is_stable() -> None:
    assert QUARANTINE_REASONS == (
        "bad_id", "missing_train_type", "missing_cancel_flag", "missing_delay", "delay_mismatch",
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_quality.py -q`
Expected: `ModuleNotFoundError: No module named 'dbdelay.data.quality'`.

- [ ] **Step 3: Implement `src/dbdelay/data/quality.py` (part 1)**

```python
"""Row-level quarantine and the per-month quality report (rules.md §5.3)."""

import pandas as pd

# s@id: "<trip hash>-<YYMMDDHHmm>-<stop ≥ 1>"
_ID_PATTERN = r"-?[0-9]+-[0-9]{10}-[1-9][0-9]*"
QUARANTINE_REASONS: tuple[str, ...] = (
    "bad_id",
    "missing_train_type",
    "missing_cancel_flag",
    "missing_delay",
    "delay_mismatch",
)


def _as_bool(mask: "pd.Series[bool]") -> "pd.Series[bool]":
    return mask.fillna(False).astype(bool)


def split_quarantine(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split HF-shaped rows into (usable, quarantined + ``quarantine_reason``).

    Never modifies values: rows either pass as they are or are set aside with a reason.
    """
    ids = raw["id"].astype("string")
    train_type = raw["train_type"].astype("string").str.strip()
    cancelled = raw["departure_is_canceled"]
    not_cancelled = _as_bool(cancelled.eq(False))
    delay = raw["delay_in_min"]
    expected = raw["departure_planned_time"] + pd.to_timedelta(
        delay.astype("Float64"), unit="min"
    )
    rules: dict[str, pd.Series] = {
        "bad_id": ~_as_bool(ids.str.fullmatch(_ID_PATTERN)),
        "missing_train_type": _as_bool(train_type.isna() | train_type.eq("")),
        "missing_cancel_flag": _as_bool(cancelled.isna()),
        "missing_delay": not_cancelled & _as_bool(delay.isna()),
        "delay_mismatch": not_cancelled
        & ~_as_bool(raw["departure_change_time"].eq(expected)),
    }
    reason = pd.Series(pd.NA, index=raw.index, dtype="object")
    for name in QUARANTINE_REASONS:
        reason = reason.mask(reason.isna() & rules[name], name)
    flagged = reason.notna()
    quarantined = raw[flagged].assign(quarantine_reason=reason[flagged].astype("object"))
    return raw[~flagged], quarantined
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_quality.py -q -W error && uv run mypy`
Expected: all pass, no warnings. (`missing_delay` must win over `delay_mismatch` for a null delay — it does because it comes first.)

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/data/quality.py tests/unit/test_quality.py
git commit -m "feat(data): row-level quarantine rules for HF rows"
```

---

### Task 4: `conform_hf` — HF rows → silver

**Files:**
- Modify: `src/dbdelay/data/silver.py` (append)
- Test: `tests/unit/test_conform.py`

**Interfaces:**
- Consumes: `split_quarantine` (Task 3), `alias_map`/`Station` (Task 1), `month_bounds_utc` (Task 1), `make_event_id`/`RAW_COLUMNS`/`SILVER_COLUMNS` (Task 2), `validate_silver`, `LATE_THRESHOLD_MIN` (Phase 1).
- Produces: `DROP_REASONS: tuple[str, ...] = ("not_supported_station", "dst_ambiguous_or_nonexistent", "out_of_month", "duplicate")`, dataclass `ConformResult(silver: pd.DataFrame, quarantine: pd.DataFrame, drops: dict[str, int])`, `conform_hf(raw: pd.DataFrame, stations: Sequence[Station], month: str) -> ConformResult`. Quarantine reasons may additionally include `"delay_mismatch_utc"`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_conform.py`:
```python
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from dbdelay.data.silver import conform_hf, make_event_id
from dbdelay.data.stations import load_stations
from dbdelay.errors import DataValidationError
from tests.builders import hf_frame, hf_row

STATIONS = load_stations(Path(__file__).resolve().parents[2] / "configs" / "stations.yaml")


def _one(month: str = "2026-03", **overrides: object) -> pd.Series:
    result = conform_hf(hf_frame(hf_row(**overrides)), STATIONS, month)
    assert len(result.silver) == 1, (result.drops, result.quarantine)
    return result.silver.iloc[0]


def test_eva_without_leading_zero_and_station_name_from_config() -> None:
    row = _one()
    assert row["eva"] == "8000105"
    assert row["station_name"] == "Frankfurt (Main) Hbf"


def test_alias_eva_maps_to_canonical_station() -> None:
    row = _one(eva="08011160")
    assert row["eva"] == "8098160"
    assert row["station_name"] == "Berlin Hauptbahnhof"


def test_unsupported_station_is_dropped_and_counted() -> None:
    result = conform_hf(hf_frame(hf_row(eva="08000001")), STATIONS, "2026-03")
    assert result.silver.empty
    assert result.drops["not_supported_station"] == 1


def test_ride_id_and_stop_index_come_from_the_id() -> None:
    row = _one(id="-3640391175194580346-2603100815-12")
    assert row["ride_id"] == "-3640391175194580346-2603100815"
    assert row["stop_index"] == 12


def test_event_id_uses_the_frozen_format() -> None:
    row = _one()
    assert row["event_id"] == make_event_id(
        "8000105", "-3640391175194580346-2603100815", pd.Timestamp("2026-03-10 07:15", tz="UTC")
    )


@pytest.mark.parametrize(
    ("local", "utc"),
    [
        (datetime(2026, 3, 10, 8, 15), "2026-03-10 07:15"),  # CET, +1
        (datetime(2026, 3, 30, 8, 15), "2026-03-30 06:15"),  # CEST, +2
    ],
)
def test_planned_time_is_converted_to_utc(local: datetime, utc: str) -> None:
    row = _one(departure_planned_time=local, departure_change_time=local)
    assert row["planned_departure_utc"] == pd.Timestamp(utc, tz="UTC")
    assert str(row["planned_departure_utc"].unit) == "us"


@pytest.mark.parametrize(
    ("month", "local"),
    [
        ("2025-10", datetime(2025, 10, 26, 2, 30)),  # autumn: 02:30 happens twice
        ("2026-03", datetime(2026, 3, 29, 2, 30)),  # spring: 02:30 does not exist
    ],
)
def test_dst_edge_times_are_dropped_and_counted(month: str, local: datetime) -> None:
    raw = hf_frame(hf_row(departure_planned_time=local, departure_change_time=local,
                          _file_month=month))
    result = conform_hf(raw, STATIONS, month)
    assert result.silver.empty
    assert result.drops["dst_ambiguous_or_nonexistent"] == 1
    assert result.quarantine.empty


def test_dst_straddling_delay_is_quarantined() -> None:
    # HF computes delay on naive local times: 01:50 → 03:05 = 75, but only 15 real minutes.
    raw = hf_frame(hf_row(departure_planned_time=datetime(2026, 3, 29, 1, 50),
                          departure_change_time=datetime(2026, 3, 29, 3, 5), delay_in_min=75))
    result = conform_hf(raw, STATIONS, "2026-03")
    assert result.silver.empty
    assert result.quarantine["quarantine_reason"].tolist() == ["delay_mismatch_utc"]


def test_cancelled_has_no_delay_or_label_but_keeps_changed_time() -> None:
    row = _one(departure_is_canceled=True, delay_in_min=12,
               departure_change_time=datetime(2026, 3, 10, 8, 27))
    assert pd.isna(row["delay_min"])
    assert pd.isna(row["is_late"])
    assert row["is_cancelled"]
    assert row["changed_departure_utc"] == pd.Timestamp("2026-03-10 07:27", tz="UTC")


@pytest.mark.parametrize(("delay", "late"), [(-1, False), (5, False), (6, True), (40, True)])
def test_label_threshold(delay: int, late: bool) -> None:
    planned = datetime(2026, 3, 10, 8, 15)
    row = _one(delay_in_min=delay,
               departure_change_time=planned + pd.Timedelta(minutes=delay))
    assert row["delay_min"] == delay
    assert row["is_late"] == late


def test_train_type_is_stripped_and_upper_cased() -> None:
    assert _one(train_type=" erx ")["train_type"] == "ERX"


def test_null_strings_stay_none() -> None:
    row = _one(line_number=None, final_destination_station=None)
    assert row["line_number"] is None
    assert row["final_destination"] is None


def test_rows_are_kept_by_utc_date_of_the_month() -> None:
    inside = hf_row(id="1-2604010030-1", departure_planned_time=datetime(2026, 4, 1, 0, 30),
                    departure_change_time=datetime(2026, 4, 1, 0, 30), _file_month="2026-04")
    outside = hf_row(id="2-2603010030-1", departure_planned_time=datetime(2026, 3, 1, 0, 30),
                     departure_change_time=datetime(2026, 3, 1, 0, 30))
    result = conform_hf(hf_frame(inside, outside), STATIONS, "2026-03")
    # 2026-04-01 00:30 CEST = 2026-03-31 22:30 UTC (in March); 2026-03-01 00:30 CET = Feb 28 UTC.
    assert result.silver["ride_id"].tolist() == ["1-2604010030"]
    assert result.drops["out_of_month"] == 1


def test_duplicates_keep_the_latest_ingested_copy() -> None:
    older = hf_row(ingested_at=datetime(2026, 9, 1, tzinfo=UTC), train_number="old")
    newer = hf_row(ingested_at=datetime(2026, 9, 2, tzinfo=UTC), train_number="new",
                   _file_month="2026-04")
    result = conform_hf(hf_frame(older, newer), STATIONS, "2026-03")
    assert result.silver["train_number"].tolist() == ["new"]
    assert result.drops["duplicate"] == 1


def test_quarantine_only_keeps_rows_of_this_month() -> None:
    raw = hf_frame(
        hf_row(train_type=None),
        hf_row(train_type=None, id="9-2604150815-1",
               departure_planned_time=datetime(2026, 4, 15, 8, 15), _file_month="2026-04"),
    )
    result = conform_hf(raw, STATIONS, "2026-03")
    assert len(result.quarantine) == 1


def test_missing_input_column_fails_loudly() -> None:
    with pytest.raises(DataValidationError, match="line_number"):
        conform_hf(hf_frame(hf_row()).drop(columns="line_number"), STATIONS, "2026-03")


def test_empty_input_gives_empty_valid_silver() -> None:
    result = conform_hf(hf_frame(), STATIONS, "2026-03")
    assert result.silver.empty
    assert list(result.silver.columns)[0] == "event_id"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_conform.py -q`
Expected: `ImportError: cannot import name 'conform_hf'`.

- [ ] **Step 3: Implement — append to `src/dbdelay/data/silver.py`**

Add imports at the top: `from collections.abc import Sequence`, `from dataclasses import dataclass`, `from dbdelay.data.months import month_bounds_utc, month_days`, `from dbdelay.data.quality import split_quarantine`, `from dbdelay.data.schemas import LATE_THRESHOLD_MIN, validate_silver`, `from dbdelay.data.stations import Station, alias_map`, `from dbdelay.errors import DataValidationError`. Then append:

```python
DROP_REASONS: tuple[str, ...] = (
    "not_supported_station",
    "dst_ambiguous_or_nonexistent",
    "out_of_month",
    "duplicate",
)


@dataclass(frozen=True)
class ConformResult:
    """Silver rows, quarantined raw rows (+ ``quarantine_reason``) and drop counts."""

    silver: pd.DataFrame
    quarantine: pd.DataFrame
    drops: dict[str, int]


def _nullable_str(values: pd.Series) -> pd.Series:
    """Object column with ``None`` for missing values (the contract rejects NaN floats)."""
    return values.astype("object").where(values.notna(), None)


def _in_month_local(raw: pd.DataFrame, month: str) -> "pd.Series[bool]":
    start = pd.Timestamp(f"{month}-01")
    end = start + pd.offsets.MonthBegin(1)
    planned = raw["departure_planned_time"]
    in_month = (planned >= start) & (planned < end)
    return (in_month | (planned.isna() & raw["_file_month"].eq(month))).astype(bool)


def conform_hf(raw: pd.DataFrame, stations: Sequence[Station], month: str) -> ConformResult:
    """Turn HF rows (``RAW_COLUMNS``) into silver rows for ``month`` (UTC dates).

    Raises:
        DataValidationError: if input columns are missing or the output breaks the contract.
    """
    missing = [c for c in RAW_COLUMNS if c not in raw.columns]
    if missing:
        raise DataValidationError(f"HF input missing columns: {', '.join(missing)}")
    drops = dict.fromkeys(DROP_REASONS, 0)
    names = {s.eva: s.name for s in stations}

    frame = raw.assign(eva=raw["eva"].astype("string").str.lstrip("0").map(alias_map(stations)))
    supported = frame["eva"].notna()
    drops["not_supported_station"] = int((~supported).sum())
    ok, quarantine = split_quarantine(frame[supported])

    planned = ok["departure_planned_time"].dt.tz_localize(
        BERLIN, ambiguous="NaT", nonexistent="NaT"
    )
    changed = ok["departure_change_time"].dt.tz_localize(
        BERLIN, ambiguous="NaT", nonexistent="NaT"
    )
    dst_bad = planned.isna() | (ok["departure_change_time"].notna() & changed.isna())
    drops["dst_ambiguous_or_nonexistent"] = int(dst_bad.sum())
    ok, planned, changed = ok[~dst_bad], planned[~dst_bad], changed[~dst_bad]

    cancelled = ok["departure_is_canceled"].astype(bool)
    delay = ok["delay_in_min"].astype("Int16").mask(cancelled)
    planned_utc = planned.dt.tz_convert("UTC").dt.as_unit("us")
    changed_utc = changed.dt.tz_convert("UTC").dt.as_unit("us")

    # HF delays are naive local differences; across a DST switch they disagree with UTC.
    utc_expected = planned_utc + pd.to_timedelta(delay.astype("Float64"), unit="min")
    straddle = ~cancelled & ~changed_utc.eq(utc_expected).fillna(False).astype(bool)
    quarantine = pd.concat(
        [quarantine, ok[straddle].assign(quarantine_reason="delay_mismatch_utc")]
    )
    keep = ~straddle
    ok, delay, planned_utc, changed_utc = ok[keep], delay[keep], planned_utc[keep], changed_utc[keep]

    ride_id = ok["id"].str.replace(r"-[0-9]+$", "", regex=True)
    silver = pd.DataFrame(
        {
            "eva": ok["eva"].astype("object"),
            "station_name": ok["eva"].map(names).astype("object"),
            "ride_id": ride_id.astype("object"),
            "stop_index": ok["id"].str.extract(r"-([0-9]+)$", expand=False).astype("int16"),
            "train_type": ok["train_type"].str.strip().str.upper().astype("object"),
            "train_number": _nullable_str(ok["train_number"]),
            "line_number": _nullable_str(ok["line_number"]),
            "final_destination": _nullable_str(ok["final_destination_station"]),
            "planned_departure_utc": planned_utc,
            "changed_departure_utc": changed_utc,
            "delay_min": delay,
            "is_cancelled": ok["departure_is_canceled"].astype(bool),
            "is_late": (delay >= LATE_THRESHOLD_MIN).astype("boolean"),
            "source": "hf",
            "ingested_at": ok["ingested_at"],
            "_id": ok["id"],
        },
        index=ok.index,
    )

    start, end = month_bounds_utc(month)
    in_month = (silver["planned_departure_utc"] >= start) & (silver["planned_departure_utc"] < end)
    drops["out_of_month"] = int((~in_month).sum())
    silver = silver[in_month]

    silver.insert(
        0,
        "event_id",
        [
            make_event_id(e, r, p)
            for e, r, p in zip(
                silver["eva"], silver["ride_id"], silver["planned_departure_utc"], strict=True
            )
        ],
    )
    silver = silver.sort_values(
        ["event_id", "ingested_at", "_id"], ascending=[True, False, True], kind="stable"
    )
    duplicate = silver["event_id"].duplicated()
    drops["duplicate"] = int(duplicate.sum())
    silver = silver[~duplicate][list(SILVER_COLUMNS)].reset_index(drop=True)
    validate_silver(silver)

    quarantine = quarantine[_in_month_local(quarantine, month)].reset_index(drop=True)
    return ConformResult(silver=silver, quarantine=quarantine, drops=drops)
```

Note: `month_days` stays imported for `write_silver_month`. If pandas-stubs rejects `ambiguous="NaT"` typing, add `# type: ignore[arg-type]` on that argument line only with a comment `# pandas-stubs lacks "NaT"`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_conform.py tests/unit/test_silver.py tests/unit/test_quality.py -q -W error && uv run mypy`
Expected: all pass; no warnings; mypy success. For an empty input the `str.extract(...).astype("int16")` must still yield `int16` (the `test_empty_input_gives_empty_valid_silver` test proves it).

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/data/silver.py tests/unit/test_conform.py
git commit -m "feat(data): conform HF rows to the silver contract"
```

---

### Task 5: Quality report

**Files:**
- Modify: `src/dbdelay/data/quality.py` (append)
- Test: `tests/unit/test_report.py`

**Interfaces:**
- Consumes: `month_days` (Task 1), `SILVER_SCHEMA_VERSION` (Phase 1), silver frames (Task 4 shape).
- Produces: `StationQuality(eva: str, rows: int, volume_drop_days: list[str])`, `QualityReport(schema_version, source, month, rows_read, rows_out, drops, quarantined, null_rates, late_rate, cancelled_rate, stations, low_volume_hours, edge_complete, content_hash)`, `build_report(*, month: str, rows_read: int, silver: pd.DataFrame, quarantine: pd.DataFrame, drops: dict[str, int], edge_complete: dict[str, bool], content_hash: str, station_evas: Sequence[str]) -> QualityReport`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_report.py`:
```python
import pandas as pd

from dbdelay.data.quality import QUARANTINE_REASONS, build_report
from tests.builders import silver_frame


def _silver_for_month() -> pd.DataFrame:
    """Frankfurt: 10 departures/day at 08:00–08:09 local on every March day except 20 (1)."""
    frames = []
    for day in pd.date_range("2026-03-01", "2026-03-31"):
        n = 1 if day.day == 20 else 10
        frame = silver_frame(n, day=day.strftime("%Y-%m-%d"))
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _report(silver: pd.DataFrame, quarantine: pd.DataFrame | None = None):
    return build_report(
        month="2026-03",
        rows_read=500,
        silver=silver,
        quarantine=quarantine if quarantine is not None else pd.DataFrame({"quarantine_reason": []}),
        drops={"duplicate": 2},
        edge_complete={"prev": True, "next": False},
        content_hash="abc",
        station_evas=["8000105", "8000261"],
    )


def test_counts_rates_and_passthrough_fields() -> None:
    silver = _silver_for_month()
    silver.loc[0, "is_late"] = True
    report = _report(silver)
    assert report.rows_read == 500
    assert report.rows_out == len(silver) == 301
    assert report.drops == {"duplicate": 2}
    assert report.edge_complete == {"prev": True, "next": False}
    assert report.late_rate == 1 / 301
    assert report.cancelled_rate == 0.0
    assert report.null_rates["line_number"] == 1.0
    assert report.null_rates["eva"] == 0.0
    assert report.content_hash == "abc"
    assert report.schema_version == 1


def test_quarantine_counts_list_every_reason() -> None:
    quarantine = pd.DataFrame({"quarantine_reason": ["bad_id", "bad_id", "delay_mismatch_utc"]})
    report = _report(_silver_for_month(), quarantine)
    assert report.quarantined["bad_id"] == 2
    assert report.quarantined["delay_mismatch_utc"] == 1
    assert set(QUARANTINE_REASONS) <= set(report.quarantined)


def test_volume_drop_days_and_silent_stations() -> None:
    report = _report(_silver_for_month())
    frankfurt = next(s for s in report.stations if s.eva == "8000105")
    munich = next(s for s in report.stations if s.eva == "8000261")
    assert frankfurt.rows == 301
    assert frankfurt.volume_drop_days == ["2026-03-20"]
    assert munich.rows == 0
    assert munich.volume_drop_days == []


def test_low_volume_hours_flag_collection_gaps() -> None:
    report = _report(_silver_for_month())
    # silver_frame rows sit at 07:00 UTC = 08:00 CET / 09:00 CEST; the thin day is the 20th.
    assert "2026-03-20T08" in report.low_volume_hours


def test_empty_month_has_no_rates_and_no_flags() -> None:
    report = _report(silver_frame(0))
    assert report.rows_out == 0
    assert report.late_rate is None
    assert report.cancelled_rate is None
    assert report.low_volume_hours == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_report.py -q`
Expected: `ImportError: cannot import name 'build_report'`.

- [ ] **Step 3: Implement — append to `src/dbdelay/data/quality.py`**

Add imports: `from collections.abc import Sequence`, `from typing import Literal`, `from pydantic import BaseModel`, `from dbdelay.data.months import month_days`, `from dbdelay.data.schemas import SILVER_SCHEMA_VERSION`. Append:

```python
VOLUME_DROP_RATIO = 0.5  # a station-day below half the station's monthly median
LOW_HOUR_RATIO = 0.25  # an hour below a quarter of that hour-of-day's monthly median
DAY_HOURS = range(6, 22)  # 06:00–21:59 Europe/Berlin
_BERLIN = "Europe/Berlin"


class StationQuality(BaseModel):
    eva: str
    rows: int
    volume_drop_days: list[str]


class QualityReport(BaseModel):
    schema_version: int = SILVER_SCHEMA_VERSION
    source: Literal["hf"] = "hf"
    month: str
    rows_read: int
    rows_out: int
    drops: dict[str, int]
    quarantined: dict[str, int]
    null_rates: dict[str, float]
    late_rate: float | None
    cancelled_rate: float | None
    stations: list[StationQuality]
    low_volume_hours: list[str]
    edge_complete: dict[str, bool]
    content_hash: str


def _station_quality(
    silver: pd.DataFrame, month: str, station_evas: Sequence[str]
) -> list[StationQuality]:
    days = month_days(month)
    if silver.empty:
        return [StationQuality(eva=eva, rows=0, volume_drop_days=[]) for eva in station_evas]
    daily = (
        silver.groupby(["eva", silver["planned_departure_utc"].dt.date])
        .size()
        .unstack(fill_value=0)
        .reindex(index=list(station_evas), columns=days, fill_value=0)
    )
    result = []
    for eva, counts in daily.iterrows():
        median = float(counts.median())
        drops = [d.isoformat() for d, n in counts.items() if median > 0 and n < VOLUME_DROP_RATIO * median]
        result.append(StationQuality(eva=str(eva), rows=int(counts.sum()), volume_drop_days=drops))
    return result


def _low_volume_hours(silver: pd.DataFrame, month: str) -> list[str]:
    if silver.empty:
        return []
    days = month_days(month)
    hours = pd.date_range(
        pd.Timestamp(days[0]).tz_localize(_BERLIN),
        pd.Timestamp(days[-1]).tz_localize(_BERLIN) + pd.Timedelta(hours=23),
        freq="h",
    )
    hours = hours[hours.hour.isin(list(DAY_HOURS))]
    local = silver["planned_departure_utc"].dt.tz_convert(_BERLIN).dt.floor("h")
    counts = local.value_counts().reindex(hours, fill_value=0)
    median_by_hour = counts.groupby(counts.index.hour).transform("median")
    low = counts[(median_by_hour > 0) & (counts < LOW_HOUR_RATIO * median_by_hour)]
    return [f"{ts:%Y-%m-%dT%H}" for ts in low.index]


def build_report(
    *,
    month: str,
    rows_read: int,
    silver: pd.DataFrame,
    quarantine: pd.DataFrame,
    drops: dict[str, int],
    edge_complete: dict[str, bool],
    content_hash: str,
    station_evas: Sequence[str],
) -> QualityReport:
    """Summarise one built month (rules.md §5.3)."""
    quarantined = dict.fromkeys(QUARANTINE_REASONS, 0)
    quarantined.update(
        {str(k): int(v) for k, v in quarantine["quarantine_reason"].value_counts().items()}
    )
    has_rows = not silver.empty
    labelled = silver["is_late"].dropna()
    return QualityReport(
        month=month,
        rows_read=rows_read,
        rows_out=len(silver),
        drops=drops,
        quarantined=quarantined,
        null_rates={c: float(silver[c].isna().mean()) if has_rows else 0.0 for c in silver.columns},
        late_rate=float(labelled.astype(bool).mean()) if len(labelled) else None,
        cancelled_rate=float(silver["is_cancelled"].mean()) if has_rows else None,
        stations=_station_quality(silver, month, station_evas),
        low_volume_hours=_low_volume_hours(silver, month),
        edge_complete=edge_complete,
        content_hash=content_hash,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_report.py -q -W error && uv run mypy && uv run ruff check src tests`
Expected: all pass. (If ruff flags the long list comprehension line, wrap it.)

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/data/quality.py tests/unit/test_report.py
git commit -m "feat(data): per-month quality report with volume flags"
```

---

### Task 6: Bronze ingest (HF download + manifest) and the `pipelines` extra

**Files:**
- Create: `src/dbdelay/data/hf_backfill.py` (first part)
- Modify: `pyproject.toml`, `uv.lock`
- Test: `tests/unit/test_hf_ingest.py`

**Interfaces:**
- Consumes: `validate_month` (Task 1), `ObjectStore`.
- Produces: `HF_REPO = "piebro/deutsche-bahn-data"`, `BRONZE_FILE = "data.parquet"`, `MANIFEST_FILE = "_manifest.json"`, `hf_path(month) -> str`, `bronze_prefix(month, root="") -> str`, `BronzeManifest` (pydantic: `month, hf_repo, hf_path, hf_revision, sha256, size_bytes, downloaded_at: AwareDatetime`), `Downloader = Callable[[str, Path], tuple[Path, str]]`, `hf_download(path_in_repo: str, dest: Path) -> tuple[Path, str]`, `load_manifest(store, month, root="") -> BronzeManifest`, `ingest_month(month, store, *, force=False, download=hf_download, now=utc_now, root="") -> BronzeManifest`.

- [ ] **Step 1: Add the extra**

In `pyproject.toml`, after the `dependencies = [...]` block add:
```toml
[project.optional-dependencies]
# Installed in the Airflow image (and in the dev venv via the dev group); not in Lambda images.
pipelines = ["huggingface-hub>=2.0.0"]
```
Run: `uv lock && uv sync`
Expected: lock updated, no new packages installed (already in dev group).

Check the error class names used below exist:
`uv run python -c "from huggingface_hub import HfApi, hf_hub_download; from huggingface_hub.errors import HfHubHTTPError; print('ok')"` → `ok`.

- [ ] **Step 2: Write the failing tests** — `tests/unit/test_hf_ingest.py`:
```python
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.data import hf_backfill
from dbdelay.data.hf_backfill import BronzeManifest, bronze_prefix, ingest_month, load_manifest
from dbdelay.errors import ConfigError, ExternalServiceError
from dbdelay.storage import ObjectStore, make_s3_client

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    with mock_aws():
        client: S3Client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, BUCKET)


class FakeDownloader:
    def __init__(self, payload: bytes = b"PAR1-fake") -> None:
        self.payload = payload
        self.calls: list[str] = []

    def __call__(self, path_in_repo: str, dest: Path) -> tuple[Path, str]:
        self.calls.append(path_in_repo)
        target = dest / "file.parquet"
        target.write_bytes(self.payload)
        return target, "rev123"


def test_ingest_stores_file_and_manifest(store: ObjectStore) -> None:
    fake = FakeDownloader()
    manifest = ingest_month("2026-03", store, download=fake, now=lambda: NOW)

    prefix = bronze_prefix("2026-03")
    assert store.get_bytes(prefix + "data.parquet") == b"PAR1-fake"
    assert fake.calls == ["monthly_processed_data/data-2026-03.parquet"]
    assert manifest == BronzeManifest(
        month="2026-03",
        hf_repo="piebro/deutsche-bahn-data",
        hf_path="monthly_processed_data/data-2026-03.parquet",
        hf_revision="rev123",
        sha256=hashlib.sha256(b"PAR1-fake").hexdigest(),
        size_bytes=9,
        downloaded_at=NOW,
    )
    assert load_manifest(store, "2026-03") == manifest


def test_existing_bronze_is_not_downloaded_again(store: ObjectStore) -> None:
    first = ingest_month("2026-03", store, download=FakeDownloader(), now=lambda: NOW)
    fake = FakeDownloader(b"other")
    again = ingest_month("2026-03", store, download=fake, now=lambda: datetime.now(UTC))
    assert fake.calls == []
    assert again == first


def test_force_downloads_again(store: ObjectStore) -> None:
    ingest_month("2026-03", store, download=FakeDownloader(), now=lambda: NOW)
    fake = FakeDownloader(b"newer")
    manifest = ingest_month("2026-03", store, force=True, download=fake, now=lambda: NOW)
    assert fake.calls
    assert manifest.size_bytes == 5


def test_invalid_month_is_rejected_before_downloading(store: ObjectStore) -> None:
    fake = FakeDownloader()
    with pytest.raises(ConfigError):
        ingest_month("2026-3", store, download=fake)
    assert fake.calls == []


def test_hf_download_pins_the_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeApi:
        def dataset_info(self, repo_id: str) -> object:
            assert repo_id == "piebro/deutsche-bahn-data"
            return type("Info", (), {"sha": "abc123"})()

    seen: dict[str, object] = {}

    def fake_download(repo_id: str, filename: str, **kwargs: object) -> str:
        seen.update(kwargs, filename=filename)
        target = tmp_path / "x.parquet"
        target.write_bytes(b"x")
        return str(target)

    monkeypatch.setattr(hf_backfill, "HfApi", FakeApi)
    monkeypatch.setattr(hf_backfill, "hf_hub_download", fake_download)

    path, revision = hf_backfill.hf_download("monthly_processed_data/data-2026-03.parquet", tmp_path)

    assert revision == "abc123"
    assert seen["revision"] == "abc123"
    assert seen["repo_type"] == "dataset"
    assert path.read_bytes() == b"x"


def test_hf_download_errors_become_external_service_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenApi:
        def dataset_info(self, repo_id: str) -> object:
            raise OSError("network down")

    monkeypatch.setattr(hf_backfill, "HfApi", BrokenApi)
    with pytest.raises(ExternalServiceError):
        hf_backfill.hf_download("monthly_processed_data/data-2026-03.parquet", tmp_path)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_hf_ingest.py -q`
Expected: `ModuleNotFoundError: No module named 'dbdelay.data.hf_backfill'`.

- [ ] **Step 4: Implement `src/dbdelay/data/hf_backfill.py` (part 1)**

```python
"""Hugging Face history → bronze (MinIO) → silver, one month at a time (architecture §5.1)."""

import hashlib
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
from pydantic import AwareDatetime, BaseModel, ConfigDict

from dbdelay.data.months import validate_month
from dbdelay.errors import ExternalServiceError
from dbdelay.storage import ObjectStore

HF_REPO = "piebro/deutsche-bahn-data"
BRONZE_FILE = "data.parquet"
MANIFEST_FILE = "_manifest.json"

Downloader = Callable[[str, Path], tuple[Path, str]]


class BronzeManifest(BaseModel):
    """Provenance of one bronze file; ``downloaded_at`` doubles as silver ``ingested_at``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    month: str
    hf_repo: str
    hf_path: str
    hf_revision: str
    sha256: str
    size_bytes: int
    downloaded_at: AwareDatetime


def hf_path(month: str) -> str:
    return f"monthly_processed_data/data-{validate_month(month)}.parquet"


def bronze_prefix(month: str, root: str = "") -> str:
    return f"{root}bronze/hf/month={validate_month(month)}/"


def utc_now() -> datetime:
    return datetime.now(UTC)


def hf_download(path_in_repo: str, dest: Path) -> tuple[Path, str]:
    """Download one dataset file at the current, pinned revision.

    Raises:
        ExternalServiceError: on any network/Hub failure.
    """
    try:
        revision = HfApi().dataset_info(HF_REPO).sha
        if revision is None:
            raise ExternalServiceError(f"{HF_REPO} reported no revision")
        local = hf_hub_download(
            HF_REPO, path_in_repo, repo_type="dataset", revision=revision, local_dir=dest
        )
    except (HfHubHTTPError, OSError) as exc:
        raise ExternalServiceError(f"download of {path_in_repo} from {HF_REPO} failed") from exc
    return Path(local), revision


def load_manifest(store: ObjectStore, month: str, root: str = "") -> BronzeManifest:
    """Read a bronze manifest. Raises ``NotFoundError`` if the month is not ingested."""
    raw = store.get_bytes(bronze_prefix(month, root) + MANIFEST_FILE)
    return BronzeManifest.model_validate_json(raw)


def ingest_month(
    month: str,
    store: ObjectStore,
    *,
    force: bool = False,
    download: Downloader = hf_download,
    now: Callable[[], datetime] = utc_now,
    root: str = "",
) -> BronzeManifest:
    """Copy one HF month into bronze unless it is already there (manifest written last)."""
    prefix = bronze_prefix(month, root)
    if not force and store.exists(prefix + MANIFEST_FILE):
        return load_manifest(store, month, root)
    with tempfile.TemporaryDirectory() as tmp:
        local, revision = download(hf_path(month), Path(tmp))
        data = local.read_bytes()
    manifest = BronzeManifest(
        month=month,
        hf_repo=HF_REPO,
        hf_path=hf_path(month),
        hf_revision=revision,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        downloaded_at=now(),
    )
    store.put_bytes(prefix + BRONZE_FILE, data)
    store.put_bytes(
        prefix + MANIFEST_FILE, manifest.model_dump_json(indent=2).encode(), "application/json"
    )
    return manifest
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_hf_ingest.py -q -W error && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/dbdelay/data/hf_backfill.py tests/unit/test_hf_ingest.py
git commit -m "feat(data): ingest HF months into bronze with a manifest"
```

---

### Task 7: Read bronze with DuckDB and build a silver month

**Files:**
- Modify: `src/dbdelay/data/hf_backfill.py` (append)
- Test: `tests/unit/test_hf_build.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `HF_COLUMNS: tuple[str, ...]` (the 10 HF file columns), dataclass `RawMonth(frame: pd.DataFrame, edge_complete: dict[str, bool])`, `read_departures(store, month, stations, workdir: Path, root="") -> RawMonth`, `build_silver_month(store, month, stations, workdir: Path, root="") -> QualityReport` (writes silver days, quarantine, report).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_hf_build.py`:
```python
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.data.hf_backfill import bronze_prefix, build_silver_month, ingest_month
from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import quality_key, quarantine_key, silver_key
from dbdelay.data.stations import load_stations
from dbdelay.errors import DataValidationError, NotFoundError
from dbdelay.storage import ObjectStore, make_s3_client
from tests.builders import hf_frame, hf_parquet_bytes, hf_row, read_parquet_bytes

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"
STATIONS = load_stations(Path(__file__).resolve().parents[2] / "configs" / "stations.yaml")


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    with mock_aws():
        client: S3Client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, BUCKET)


def _put_month(store: ObjectStore, month: str, *rows: dict[str, object]) -> None:
    payload = hf_parquet_bytes(hf_frame(*rows))

    def download(path_in_repo: str, dest: Path) -> tuple[Path, str]:
        target = dest / "f.parquet"
        target.write_bytes(payload)
        return target, "rev"

    ingest_month(month, store, download=download,
                 now=lambda: datetime(2026, 9, 27, tzinfo=UTC))


def _march_rows() -> list[dict[str, object]]:
    return [
        hf_row(),  # Frankfurt 2026-03-10
        hf_row(id="2-2603100900-1", eva="08000001"),  # not our station
        hf_row(id="3-2603100900-1", train_type=None),  # quarantined
    ]


def test_build_writes_days_quarantine_and_report(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", *_march_rows())
    april_first = hf_row(id="4-2604010030-1", departure_planned_time=datetime(2026, 4, 1, 0, 30),
                         departure_change_time=datetime(2026, 4, 1, 0, 30))
    _put_month(store, "2026-04", april_first)

    report = build_silver_month(store, "2026-03", STATIONS, tmp_path)

    day10 = read_parquet_bytes(store.get_bytes(silver_key(pd.Timestamp("2026-03-10").date())))
    day31 = read_parquet_bytes(store.get_bytes(silver_key(pd.Timestamp("2026-03-31").date())))
    validate_silver(day10)
    validate_silver(day31)
    assert len(day10) == 1
    assert day31["ride_id"].tolist() == ["4-2604010030"]  # from the April file
    quarantined = read_parquet_bytes(store.get_bytes(quarantine_key("2026-03")))
    assert quarantined["quarantine_reason"].tolist() == ["missing_train_type"]
    stored = json.loads(store.get_bytes(quality_key("2026-03")))
    assert stored["rows_out"] == report.rows_out == 2
    assert report.edge_complete == {"prev": False, "next": True}


def test_build_is_deterministic(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", *_march_rows())
    first = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    key = silver_key(pd.Timestamp("2026-03-10").date())
    bytes_before = store.get_bytes(key)
    second = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert second.content_hash == first.content_hash
    assert store.get_bytes(key) == bytes_before


def test_build_without_next_month_marks_edge_incomplete(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", hf_row())
    report = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert report.edge_complete == {"prev": False, "next": False}
    assert report.rows_out == 1


def test_missing_bronze_for_the_month_raises(store: ObjectStore, tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        build_silver_month(store, "2026-03", STATIONS, tmp_path)


def test_checksum_mismatch_stops_the_month(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", hf_row())
    store.put_bytes(bronze_prefix("2026-03") + "data.parquet", b"corrupted")
    with pytest.raises(DataValidationError, match="checksum"):
        build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert not store.exists(silver_key(pd.Timestamp("2026-03-10").date()))


def test_bronze_missing_a_column_stops_the_month(store: ObjectStore, tmp_path: Path) -> None:
    payload = hf_parquet_bytes_without(hf_frame(hf_row()), "line_number")

    def download(path_in_repo: str, dest: Path) -> tuple[Path, str]:
        target = dest / "f.parquet"
        target.write_bytes(payload)
        return target, "rev"

    ingest_month("2026-03", store, download=download)
    with pytest.raises(DataValidationError, match="line_number"):
        build_silver_month(store, "2026-03", STATIONS, tmp_path)


def hf_parquet_bytes_without(frame: pd.DataFrame, column: str) -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    keep = [c for c in frame.columns if c not in {column, "ingested_at", "_file_month"}]
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame[keep], preserve_index=False), buffer)
    return buffer.getvalue()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_hf_build.py -q`
Expected: `ImportError: cannot import name 'build_silver_month'`.

- [ ] **Step 3: Implement — append to `src/dbdelay/data/hf_backfill.py`**

Add imports: `from collections.abc import Sequence`, `from dataclasses import dataclass`, `import duckdb`, `import pandas as pd`, `from dbdelay.data.months import shift_month`, `from dbdelay.data.quality import QualityReport, build_report`, `from dbdelay.data.silver import RAW_DTYPES, conform_hf, content_hash, quality_key, quarantine_key, to_parquet_bytes, write_silver_month`, `from dbdelay.data.stations import Station, alias_map`, `from dbdelay.errors import DataValidationError, NotFoundError`, `from dbdelay.logging import get_logger`. Append:

```python
HF_COLUMNS: tuple[str, ...] = (
    "eva",
    "train_number",
    "line_number",
    "final_destination_station",
    "delay_in_min",
    "departure_is_canceled",
    "train_type",
    "id",
    "departure_planned_time",
    "departure_change_time",
)
_SELECT = """
select eva, train_number, line_number, final_destination_station, delay_in_min,
       departure_is_canceled, train_type, id, departure_planned_time, departure_change_time
from hf
where list_contains($evas, ltrim(eva, '0'))
  and departure_planned_time >= $lo
  and departure_planned_time < $hi
"""

_log = get_logger("backfill")


@dataclass(frozen=True)
class RawMonth:
    """Candidate HF rows for one month (from bronze M-1, M, M+1) + which neighbours existed."""

    frame: pd.DataFrame
    edge_complete: dict[str, bool]


def _query_file(path: Path, evas: list[str], lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        con.read_parquet(str(path)).create_view("hf")
        present = {row[0] for row in con.execute("describe hf").fetchall()}
        missing = sorted(set(HF_COLUMNS) - present)
        if missing:
            raise DataValidationError(f"{path.name} lacks columns: {', '.join(missing)}")
        params = {"evas": evas, "lo": lo.to_pydatetime(), "hi": hi.to_pydatetime()}
        return con.execute(_SELECT, params).df()
    finally:
        con.close()


def read_departures(
    store: ObjectStore, month: str, stations: Sequence[Station], workdir: Path, root: str = ""
) -> RawMonth:
    """Filter bronze M-1, M, M+1 to our stations' departures planned (local) near month M.

    Raises:
        NotFoundError: if month M itself is not in bronze.
        DataValidationError: on checksum mismatch or missing HF columns.
    """
    evas = sorted(alias_map(stations))
    lo = pd.Timestamp(f"{month}-01") - pd.Timedelta(days=1)
    hi = pd.Timestamp(f"{shift_month(month, 1)}-01") + pd.Timedelta(days=1)
    frames: list[pd.DataFrame] = []
    edge_complete: dict[str, bool] = {}
    for label, source_month in (
        ("prev", shift_month(month, -1)),
        ("this", month),
        ("next", shift_month(month, 1)),
    ):
        prefix = bronze_prefix(source_month, root)
        if not store.exists(prefix + MANIFEST_FILE):
            if label == "this":
                raise NotFoundError(f"bronze for {month} is missing; run ingest first")
            edge_complete[label] = False
            continue
        manifest = load_manifest(store, source_month, root)
        data = store.get_bytes(prefix + BRONZE_FILE)
        if hashlib.sha256(data).hexdigest() != manifest.sha256:
            raise DataValidationError(f"bronze {source_month} checksum mismatch")
        path = workdir / f"hf-{source_month}.parquet"
        path.write_bytes(data)
        del data
        part = _query_file(path, evas, lo, hi)
        part["ingested_at"] = pd.Timestamp(manifest.downloaded_at).tz_convert("UTC")
        part["_file_month"] = source_month
        frames.append(part)
        path.unlink()
        if label != "this":
            edge_complete[label] = True
    frame = pd.concat(frames, ignore_index=True).astype(RAW_DTYPES)
    return RawMonth(frame=frame, edge_complete=edge_complete)


def build_silver_month(
    store: ObjectStore, month: str, stations: Sequence[Station], workdir: Path, root: str = ""
) -> QualityReport:
    """Rebuild silver, quarantine and quality report for one month (idempotent)."""
    raw = read_departures(store, month, stations, workdir, root)
    result = conform_hf(raw.frame, stations, month)
    write_silver_month(result.silver, store, month, root)
    store.put_bytes(quarantine_key(month, root), to_parquet_bytes(result.quarantine))
    report = build_report(
        month=month,
        rows_read=len(raw.frame),
        silver=result.silver,
        quarantine=result.quarantine,
        drops=result.drops,
        edge_complete=raw.edge_complete,
        content_hash=content_hash(result.silver),
        station_evas=[s.eva for s in stations],
    )
    store.put_bytes(
        quality_key(month, root), report.model_dump_json(indent=2).encode(), "application/json"
    )
    _log.info("silver month built", extra=report.model_dump(mode="json", exclude={"stations"}))
    return report
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_hf_build.py -q -W error && make check`
Expected: all pass; lint/typecheck clean; coverage ≥ 80 % (target stays near 100 %).

- [ ] **Step 5: Commit**

```bash
git add src/dbdelay/data/hf_backfill.py tests/unit/test_hf_build.py
git commit -m "feat(data): build a silver month from bronze with DuckDB"
```

---

### Task 8: Integration test against MinIO

**Files:**
- Create: `tests/integration/test_hf_backfill_minio.py`

**Interfaces:**
- Consumes: `ingest_month`, `build_silver_month`, `load_stations`, builders.

- [ ] **Step 1: Start the stack**

Run: `make up`
Expected: minio, postgres, mlflow healthy.

- [ ] **Step 2: Write the test**

```python
"""End-to-end month build against the local MinIO (`make up`)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from dbdelay.config import Settings
from dbdelay.data.hf_backfill import build_silver_month, ingest_month
from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import silver_key
from dbdelay.data.stations import load_stations
from dbdelay.storage import ObjectStore, make_s3_client
from tests.builders import hf_frame, hf_parquet_bytes, hf_row, read_parquet_bytes

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO_ROOT / "configs" / "stations.yaml")


@pytest.fixture
def scratch() -> Iterator[tuple[ObjectStore, str]]:
    settings = Settings(_env_file=REPO_ROOT / ".env")  # type: ignore[call-arg]
    if settings.storage_endpoint_url is None:
        pytest.fail("STORAGE_ENDPOINT_URL must point to local MinIO (see .env.example)")
    client = make_s3_client(settings)
    root = f"_integration/{uuid.uuid4()}/"
    yield ObjectStore(client, settings.data_bucket), root
    # Test-only cleanup of our own scratch prefix (the shared bucket holds real data).
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.data_bucket, Prefix=root):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])


def test_month_builds_twice_identically(scratch: tuple[ObjectStore, str], tmp_path: Path) -> None:
    store, root = scratch
    payload = hf_parquet_bytes(hf_frame(hf_row(), hf_row(id="5-2603101000-3", delay_in_min=9,
                                                     departure_change_time=datetime(2026, 3, 10, 8, 24))))

    def download(path_in_repo: str, dest: Path) -> tuple[Path, str]:
        target = dest / "f.parquet"
        target.write_bytes(payload)
        return target, "rev"

    ingest_month("2026-03", store, download=download,
                 now=lambda: datetime(2026, 9, 27, tzinfo=UTC), root=root)

    first = build_silver_month(store, "2026-03", STATIONS, tmp_path, root=root)
    key = silver_key(pd.Timestamp("2026-03-10").date(), root=root)
    first_bytes = store.get_bytes(key)
    second = build_silver_month(store, "2026-03", STATIONS, tmp_path, root=root)

    assert first.rows_out == second.rows_out == 2
    assert first.content_hash == second.content_hash
    assert store.get_bytes(key) == first_bytes
    day = read_parquet_bytes(first_bytes)
    validate_silver(day)
    assert sorted(day["is_late"].tolist()) == [False, True]
```

- [ ] **Step 3: Run it**

Run: `make test-integration`
Expected: 3 passed (2 existing + 1 new).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_hf_backfill_minio.py
git commit -m "test(integration): build an HF month twice against MinIO"
```

---

### Task 9: Airflow image, compose services, secrets helper, Make targets

**Files:**
- Create: `pipelines/airflow/Dockerfile`, `pipelines/airflow/requirements.txt` (generated), `pipelines/airflow/init/create_db.py`, `scripts/ensure_airflow_env.py`, `tests/unit/test_ensure_airflow_env.py`
- Modify: `docker-compose.yml`, `Makefile`, `.env.example`

- [ ] **Step 1: Test for the secrets helper** — `tests/unit/test_ensure_airflow_env.py`:
```python
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ensure_airflow_env.py"
spec = importlib.util.spec_from_file_location("ensure_airflow_env", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_adds_only_missing_keys_and_keeps_existing_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("APP_ENV=local\nAIRFLOW_SECRET_KEY=keep-me\n", encoding="utf-8")

    added = module.ensure_keys(env)

    text = env.read_text(encoding="utf-8")
    assert added == ["AIRFLOW_ADMIN_PASSWORD", "AIRFLOW_FERNET_KEY"]
    assert "AIRFLOW_SECRET_KEY=keep-me" in text
    assert text.count("AIRFLOW_ADMIN_PASSWORD=") == 1
    assert module.ensure_keys(env) == []


def test_fernet_key_is_urlsafe_base64_of_32_bytes(tmp_path: Path) -> None:
    import base64

    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    module.ensure_keys(env)
    line = next(
        entry for entry in env.read_text(encoding="utf-8").splitlines()
        if entry.startswith("AIRFLOW_FERNET_KEY=")
    )
    assert len(base64.urlsafe_b64decode(line.split("=", 1)[1])) == 32
```
Run: `uv run pytest tests/unit/test_ensure_airflow_env.py -q` → FAIL (script missing).

- [ ] **Step 2: Implement `scripts/ensure_airflow_env.py`**
```python
"""Append missing Airflow secrets to `.env` without printing their values.

Usage: uv run python scripts/ensure_airflow_env.py [path-to-.env]
"""

import base64
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

GENERATORS: dict[str, Callable[[], str]] = {
    "AIRFLOW_ADMIN_PASSWORD": lambda: secrets.token_urlsafe(24),
    "AIRFLOW_SECRET_KEY": lambda: secrets.token_urlsafe(32),
    "AIRFLOW_FERNET_KEY": lambda: base64.urlsafe_b64encode(os.urandom(32)).decode(),
}


def ensure_keys(env_file: Path) -> list[str]:
    """Add each missing key with a fresh random value; return the names added."""
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    present = {line.split("=", 1)[0].strip() for line in text.splitlines() if "=" in line}
    added = [key for key in GENERATORS if key not in present]
    if added:
        lines = [f"{key}={GENERATORS[key]()}" for key in added]
        prefix = "" if not text or text.endswith("\n") else "\n"
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\n".join(lines) + "\n")
    return added


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".env")
    names = ensure_keys(target)
    sys.stdout.write(f"added: {', '.join(names) or 'nothing'}\n")
```
Run the test again → PASS.

- [ ] **Step 3: `.env.example` additions** (append):
```
# --- Airflow (Phase 2). `make airflow-env` fills real random values into .env. ---
AIRFLOW_ADMIN_PASSWORD=change-me-airflow-admin
AIRFLOW_SECRET_KEY=change-me-airflow-secret
AIRFLOW_FERNET_KEY=change-me-44-char-urlsafe-base64-key
```

- [ ] **Step 4: Requirements for the image**

Run: `uv export --no-dev --extra pipelines --no-hashes --no-emit-project --format requirements-txt -o pipelines/airflow/requirements.txt`
Expected: file lists pandas, pyarrow, duckdb, pandera, pyyaml, huggingface-hub, boto3, pydantic-settings, aws-lambda-powertools and their pins. Add a first line comment manually: `# Generated: uv export --no-dev --extra pipelines --no-hashes --no-emit-project (re-run after uv.lock changes)`.

- [ ] **Step 5: `pipelines/airflow/init/create_db.py`**
```python
"""Create the `airflow` metadata database in the shared Postgres if it is missing."""

import os

import psycopg2
from psycopg2 import sql

conn = psycopg2.connect(
    host="postgres",
    dbname="postgres",
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("select 1 from pg_database where datname = 'airflow'")
    if cur.fetchone() is None:
        cur.execute(sql.SQL("create database {}").format(sql.Identifier("airflow")))
conn.close()
```

- [ ] **Step 6: `pipelines/airflow/Dockerfile`**
```dockerfile
# Airflow 3.3.2 + dbdelay[pipelines] (Phase 2). Build context: repo root.
FROM apache/airflow:3.3.2-python3.12@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1

USER airflow
COPY --chown=airflow:root pipelines/airflow/requirements.txt /tmp/requirements.txt
# Keep Airflow pinned while installing our locked deps; pip fails loudly on conflicts.
RUN pip install --no-cache-dir "apache-airflow==3.3.2" -r /tmp/requirements.txt

COPY --chown=airflow:root pyproject.toml README.md /opt/dbdelay/
COPY --chown=airflow:root src /opt/dbdelay/src
RUN pip install --no-cache-dir --no-deps /opt/dbdelay

COPY --chown=airflow:root pipelines/airflow/init /opt/airflow/init
```

- [ ] **Step 7: Compose services** — in `docker-compose.yml` add these top-level extension blocks **above** `services:`, the four services **inside** `services:` (after `mlflow`), and `airflow-logs:` under `volumes:`.

Extension blocks (above `services:`):
```yaml
x-airflow-common: &airflow-common
  profiles: ["airflow"]
  build:
    context: .
    dockerfile: pipelines/airflow/Dockerfile
  image: puenktlich/airflow:local
  volumes:
    - ./pipelines/airflow/dags:/opt/airflow/dags:ro
    - ./pipelines/airflow/tests:/opt/airflow/tests:ro
    - ./configs:/opt/airflow/configs:ro
    - airflow-logs:/opt/airflow/logs

x-airflow-env: &airflow-env
  AIRFLOW__CORE__EXECUTOR: LocalExecutor
  AIRFLOW__CORE__AUTH_MANAGER: airflow.providers.fab.auth_manager.fab_auth_manager.FabAuthManager
  AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/airflow
  AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY:?run make airflow-env}
  AIRFLOW__API__SECRET_KEY: ${AIRFLOW_SECRET_KEY:?run make airflow-env}
  AIRFLOW__API_AUTH__JWT_SECRET: ${AIRFLOW_SECRET_KEY:?run make airflow-env}
  AIRFLOW__CORE__EXECUTION_API_SERVER_URL: http://airflow-api-server:8080/execution/
  AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: "true"
  AIRFLOW__CORE__LOAD_EXAMPLES: "false"
  AIRFLOW__SCHEDULER__ENABLE_HEALTH_CHECK: "true"
  POSTGRES_USER: ${POSTGRES_USER}
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  STORAGE_ENDPOINT_URL: http://minio:9000
  STORAGE_ACCESS_KEY_ID: ${STORAGE_ACCESS_KEY_ID}
  STORAGE_SECRET_ACCESS_KEY: ${STORAGE_SECRET_ACCESS_KEY}
  DATA_BUCKET: ${DATA_BUCKET:-puenktlich-local}
  STATIONS_FILE: /opt/airflow/configs/stations.yaml
  LOG_LEVEL: ${LOG_LEVEL:-INFO}
```

Services (inside `services:`):
```yaml
  # ---------------- Airflow 3 (Phase 2) — `make airflow-up` (profile "airflow") ----------------
  airflow-init:
    <<: *airflow-common
    environment:
      <<: *airflow-env
      _AIRFLOW_DB_MIGRATE: "true"
      _AIRFLOW_WWW_USER_CREATE: "true"
      _AIRFLOW_WWW_USER_USERNAME: admin
      _AIRFLOW_WWW_USER_PASSWORD: ${AIRFLOW_ADMIN_PASSWORD:?run make airflow-env}
    depends_on:
      postgres:
        condition: service_healthy
      minio-init:
        condition: service_completed_successfully
    user: "0:0"
    entrypoint: /bin/bash
    command:
      - -c
      - |
        python /opt/airflow/init/create_db.py &&
        mkdir -p /opt/airflow/logs && chown -R 50000:0 /opt/airflow/logs &&
        /entrypoint airflow version
    restart: "no"

  airflow-api-server:
    <<: *airflow-common
    environment: *airflow-env
    command: api-server
    ports:
      - "127.0.0.1:8080:8080"
    depends_on: &after-init
      airflow-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/api/v2/monitor/health"]
      interval: 15s
      timeout: 10s
      retries: 10
      start_period: 30s
    restart: unless-stopped

  airflow-scheduler:
    <<: *airflow-common
    environment: *airflow-env
    command: scheduler
    depends_on: *after-init
    healthcheck:
      test: ["CMD-SHELL", 'airflow jobs check --job-type SchedulerJob --hostname "$${HOSTNAME}"']
      interval: 30s
      timeout: 20s
      retries: 5
      start_period: 30s
    restart: unless-stopped

  airflow-dag-processor:
    <<: *airflow-common
    environment: *airflow-env
    command: dag-processor
    depends_on: *after-init
    healthcheck:
      test: ["CMD-SHELL", 'airflow jobs check --job-type DagProcessorJob --hostname "$${HOSTNAME}"']
      interval: 30s
      timeout: 20s
      retries: 5
      start_period: 30s
    restart: unless-stopped
```
Verify with `docker compose --profile airflow config --quiet` (exit 0) and `docker compose --profile airflow config | grep -c _AIRFLOW_WWW_USER_PASSWORD` → `1` (only the init service carries it). The long-running services use the image's default user (50000) and entrypoint.

- [ ] **Step 8: Make targets** — add to `.PHONY` and append:
```make
airflow-env: ## Add missing Airflow secrets to .env (values never printed)
	uv run python scripts/ensure_airflow_env.py

airflow-up: ## Start Airflow 3 (api-server :8080, scheduler, dag-processor) + core stack
	$(COMPOSE) --profile airflow up -d --build --wait

airflow-down: ## Stop Airflow and the core stack (volumes kept)
	$(COMPOSE) --profile airflow down

test-dags: ## Parse the DAGs inside the Airflow image
	$(COMPOSE) --profile airflow run --rm --no-deps --entrypoint python airflow-scheduler /opt/airflow/tests/check_dags.py

backfill: ## Unpause and trigger backfill_history with its default 9 months
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags unpause backfill_history
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags trigger backfill_history
```

- [ ] **Step 9: Build and start**

Run: `make airflow-env` → prints `added: AIRFLOW_ADMIN_PASSWORD, AIRFLOW_SECRET_KEY, AIRFLOW_FERNET_KEY` (names only).
Run: `docker compose --profile airflow config --quiet && make airflow-up`
Expected: image builds (pip resolves without conflicts); `docker compose --profile airflow ps` shows api-server, scheduler, dag-processor `healthy`; http://localhost:8080 shows the login page (user `admin`, password in `.env` — the owner reads it, not Claude).
If pip reports a dependency conflict: **stop and report** the package and both requirements; do not loosen pins without the owner's OK.

- [ ] **Step 10: Commit**

```bash
git add pipelines/airflow/Dockerfile pipelines/airflow/requirements.txt pipelines/airflow/init/create_db.py scripts/ensure_airflow_env.py tests/unit/test_ensure_airflow_env.py docker-compose.yml Makefile .env.example
git commit -m "feat(airflow): Airflow 3.3.2 services with LocalExecutor and dbdelay image"
```

---

### Task 10: `backfill_history` DAG + DAG check

**Files:**
- Create: `pipelines/airflow/dags/backfill_history.py`, `pipelines/airflow/tests/check_dags.py`

**Interfaces:**
- Consumes: `ingest_month`, `build_silver_month`, `validate_month`, `load_stations`, `get_settings`, `make_s3_client`, `ObjectStore`.

- [ ] **Step 1: Write the check first** — `pipelines/airflow/tests/check_dags.py`:
```python
"""Parse DAGs inside the Airflow image and assert the backfill DAG's shape (`make test-dags`)."""

import sys

try:
    from airflow.dag_processing.dagbag import DagBag
except ImportError:  # older 3.x location
    from airflow.models.dagbag import DagBag

EXPECTED_MONTHS = [
    "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
    "2026-05", "2026-06", "2026-07", "2026-08",
]

bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
errors = []
if bag.import_errors:
    errors.append(f"import errors: {bag.import_errors}")
dag = bag.dags.get("backfill_history")
if dag is None:
    errors.append("backfill_history not found")
else:
    tasks = {t.task_id for t in dag.tasks}
    if tasks != {"plan_months", "ingest", "build_silver", "summarize"}:
        errors.append(f"unexpected tasks: {sorted(tasks)}")
    if dag.params["months"] != EXPECTED_MONTHS:
        errors.append(f"unexpected default months: {dag.params['months']}")
    if dag.params["force_download"] is not False:
        errors.append("force_download must default to False")
if errors:
    sys.stderr.write("\n".join(errors) + "\n")
    sys.exit(1)
sys.stdout.write("DAG check passed\n")
```
Run: `make test-dags` → FAIL (`backfill_history not found`).

- [ ] **Step 2: Implement the DAG** — `pipelines/airflow/dags/backfill_history.py`:
```python
"""backfill_history — HF monthly history → bronze → silver (docs/architecture.md §5.1).

Thin orchestration only; all logic lives in `dbdelay.data`.
"""

from datetime import timedelta
from typing import Any

from airflow.sdk import Param, dag, get_current_context, task

DEFAULT_MONTHS = [
    "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
    "2026-05", "2026-06", "2026-07", "2026-08",
]


def _store() -> Any:
    from dbdelay.config import get_settings
    from dbdelay.storage import ObjectStore, make_s3_client

    return ObjectStore(make_s3_client(), get_settings().data_bucket)


@dag(
    dag_id="backfill_history",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["phase-2", "etl", "hf"],
    params={
        "months": Param(DEFAULT_MONTHS, type="array", description="Months to (re)build, YYYY-MM"),
        "force_download": Param(False, type="boolean", description="Re-download bronze"),
    },
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
    },
)
def backfill_history() -> None:
    @task
    def plan_months() -> list[str]:
        from dbdelay.data.months import validate_month

        months = get_current_context()["params"]["months"]
        return sorted({validate_month(str(m)) for m in months})

    @task(max_active_tis_per_dag=2)
    def ingest(month: str) -> str:
        from dbdelay.data.hf_backfill import ingest_month

        force = bool(get_current_context()["params"]["force_download"])
        ingest_month(month, _store(), force=force)
        return month

    @task(max_active_tis_per_dag=2, retries=0)
    def build_silver(month: str) -> dict[str, Any]:
        import tempfile
        from pathlib import Path

        from dbdelay.config import get_settings
        from dbdelay.data.hf_backfill import build_silver_month
        from dbdelay.data.stations import load_stations

        stations = load_stations(get_settings().stations_file)
        with tempfile.TemporaryDirectory() as tmp:
            report = build_silver_month(_store(), month, stations, Path(tmp))
        return report.model_dump(mode="json", exclude={"stations"})

    @task
    def summarize(reports: list[dict[str, Any]]) -> None:
        from dbdelay.logging import get_logger

        log = get_logger("backfill")
        for r in sorted(reports, key=lambda item: item["month"]):
            log.info(
                "month summary",
                extra={k: r[k] for k in ("month", "rows_out", "late_rate", "edge_complete",
                                         "quarantined", "content_hash")},
            )

    months = plan_months()
    ingested = ingest.expand(month=months)
    summarize(build_silver.expand(month=ingested))


backfill_history()
```
(`build_silver` has `retries=0`: a data validation failure must not be retried silently.)

- [ ] **Step 3: Run the check and lint**

Run: `make test-dags && uv run ruff check pipelines && uv run ruff format --check pipelines`
Expected: `DAG check passed`; ruff clean. Open http://localhost:8080 → DAG `backfill_history` visible, paused, no import errors.

- [ ] **Step 4: Commit**

```bash
git add pipelines/airflow/dags/backfill_history.py pipelines/airflow/tests/check_dags.py
git commit -m "feat(airflow): backfill_history DAG with dynamic month mapping"
```

---

### Task 11: Real 9-month backfill and determinism check

**Files:** none (evidence only). Keep notes for Task 12.

- [ ] **Step 1: Trigger**

Run: `make backfill`
Monitor: `docker compose --profile airflow exec airflow-scheduler airflow dags list-runs backfill_history` until the run is `success` (expect tens of minutes: ~5.5 GB download). Watch memory in Docker Desktop.

- [ ] **Step 2: Verify outputs**

Run (read-only MinIO check from the venv):
```bash
uv run python - <<'EOF'
import json
from dbdelay.config import get_settings
from dbdelay.storage import ObjectStore, make_s3_client
s = get_settings(); store = ObjectStore(make_s3_client(s), s.data_bucket)
for m in ["2025-12","2026-01","2026-02","2026-03","2026-04","2026-05","2026-06","2026-07","2026-08"]:
    r = json.loads(store.get_bytes(f"silver/_quality/source=hf/month={m}.json"))
    print(m, r["rows_out"], round(r["late_rate"], 4), r["edge_complete"], sum(r["quarantined"].values()),
          r["drops"], len(r["low_volume_hours"]), r["content_hash"][:12])
print(sum(1 for _ in store.iter_keys("silver/departures/source=hf/")), "silver day files")
EOF
```
Expected: 9 lines; `rows_out` roughly 1.1–1.3 M per month; late rate in the 0.2–0.3 range (EDA: 22.5–26.3 %); 273 day files (Dec 31 + Jan 31 + Feb 28 + Mar 31 + Apr 30 + May 31 + Jun 30 + Jul 31 + Aug 31). Record the real numbers.

- [ ] **Step 3: Determinism**

Trigger again for one month without re-download: in the UI "Trigger DAG w/ config" `{"months": ["2026-03"], "force_download": false}` (or CLI `airflow dags trigger backfill_history -c '{"months": ["2026-03"]}'` inside the scheduler container). Re-run the Step 2 script for 2026-03.
Expected: identical `rows_out` and `content_hash` as the first run. Record both.

---

### Task 12: Review, docs and phase-end deliverables

**Files:**
- Create: `CHANGELOG.md`, `Phases/phase-2-hf-backfill.md`
- Modify: `Phases/README.md`, `docs/architecture.md` (§3.2 bronze manifest + `_quarantine` month partition + `_quality` prefixes; §14 layout `pipelines/airflow/{Dockerfile,init/,tests/}`), `docs/phases.md` (tick Phase 2 tasks), `docs/superpowers/specs/2026-09-26-phase-2-hf-backfill-design.md` (quarantine per month + final reason names), `README.md` (Airflow URL row stays; add `make airflow-up`/`make backfill` to useful commands), `CLAUDE.md` (status + session log + Airflow notes)

- [ ] **Step 1: Full verification** — `make check && make test-integration && make test-dags && uv run pre-commit run --all-files` → all green; record counts and coverage.
- [ ] **Step 2: Code review** — run the reviewer (`general-purpose` agent following `.claude/agents/code-reviewer.md`) on `git diff main...HEAD`; handle findings with `superpowers:receiving-code-review`; re-run Step 1.
- [ ] **Step 3: Docs** — update the files listed above with the **real** numbers from Task 11 (never estimates; `TBD` if something was not measured).
- [ ] **Step 4: CHANGELOG + phase doc** — `CHANGELOG.md` entry "Phase 2 — Historical ETL" with sections *Built · Key decisions · Tested · Known gaps / open items · Docs touched*; `Phases/phase-2-hf-backfill.md` (goal, what was built + why, how it works with the DAG diagram, decisions, tests + real results, how to run, gaps, next); add the row to `Phases/README.md`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "docs: phase 2 record (changelog, phase doc, architecture)"`.
- [ ] **Step 6: Merge message** — write `.git/MERGE_SUMMARY.txt` (same summary as the CHANGELOG entry); check `git merge-tree --write-tree origin/main HEAD` for conflicts; push the branch; give the owner the merge command `git merge --no-ff phase-2/hf-backfill -F .git/MERGE_SUMMARY.txt`. Never delete branches.

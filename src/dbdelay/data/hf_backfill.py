"""Hugging Face history → bronze (MinIO) → silver, one month at a time (architecture §5.1)."""

import hashlib
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
from pydantic import AwareDatetime, BaseModel, ConfigDict

from dbdelay.data.months import shift_month, validate_month
from dbdelay.data.quality import QualityReport, build_report
from dbdelay.data.silver import (
    RAW_DTYPES,
    conform_hf,
    quality_key,
    quarantine_key,
    to_parquet_bytes,
    write_silver_month,
)
from dbdelay.data.stations import Station, alias_map
from dbdelay.errors import DataValidationError, ExternalServiceError, NotFoundError
from dbdelay.logging import get_logger
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


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    """SHA-256 of a file, read in chunks (bronze months are ~650 MB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(store: ObjectStore, month: str, root: str = "") -> BronzeManifest:
    """Read a bronze manifest. Raises ``NotFoundError`` if the month is not ingested."""
    raw = store.get_bytes(bronze_prefix(month, root) + MANIFEST_FILE)
    return BronzeManifest.model_validate_json(raw)


def ingest_month(  # noqa: PLR0913 - keyword-only seams for tests (download, now, root)
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
        manifest = BronzeManifest(
            month=month,
            hf_repo=HF_REPO,
            hf_path=hf_path(month),
            hf_revision=revision,
            sha256=sha256_file(local),
            size_bytes=local.stat().st_size,
            downloaded_at=now(),
        )
        store.upload_file(prefix + BRONZE_FILE, local)
    store.put_bytes(
        prefix + MANIFEST_FILE, manifest.model_dump_json(indent=2).encode(), "application/json"
    )
    return manifest


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


def _query_file(path: Path, evas: list[str], lo: datetime, hi: datetime) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        con.read_parquet(str(path)).create_view("hf")
        present = {row[0] for row in con.execute("describe hf").fetchall()}
        missing = sorted(set(HF_COLUMNS) - present)
        if missing:
            raise DataValidationError(f"{path.name} lacks columns: {', '.join(missing)}")
        return con.execute(_SELECT, {"evas": evas, "lo": lo, "hi": hi}).df()
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
    lo = datetime.fromisoformat(f"{month}-01") - timedelta(days=1)
    hi = datetime.fromisoformat(f"{shift_month(month, 1)}-01") + timedelta(days=1)
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
        path = workdir / f"hf-{source_month}.parquet"
        store.download_file(prefix + BRONZE_FILE, path)
        if sha256_file(path) != manifest.sha256:
            raise DataValidationError(f"bronze {source_month} checksum mismatch")
        part = _query_file(path, evas, lo, hi)
        part["ingested_at"] = pd.Timestamp(manifest.downloaded_at).tz_convert("UTC")
        part["_file_month"] = source_month
        frames.append(part)
        path.unlink()
        if label != "this":
            edge_complete[label] = True
    # Empty parts are skipped: concat with an empty frame trips a pandas/NumPy deprecation.
    parts = [f for f in frames if not f.empty] or frames[:1]
    frame = pd.concat(parts, ignore_index=True).astype(RAW_DTYPES)
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
        month,
        rows_read=len(raw.frame),
        conform=result,
        edge_complete=raw.edge_complete,
        station_evas=[s.eva for s in stations],
    )
    store.put_bytes(
        quality_key(month, root), report.model_dump_json(indent=2).encode(), "application/json"
    )
    _log.info("silver month built", extra=report.model_dump(mode="json", exclude={"stations"}))
    return report

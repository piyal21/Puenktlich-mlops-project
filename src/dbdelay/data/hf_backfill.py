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

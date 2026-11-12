"""Read Hugging Face `raw_data` (the scraper's raw DB API responses) for our stations.

Layout (checked 2026-11-12 on `raw_data/year=2026/month=8/day=24`): one parquet file per scrape
run, folders `raw_data/year=YYYY/month=M/day=D` (no zero padding). Columns: `timestamp` (naive
**UTC** — DB message times in the XML are 2 h later in CEST), `url`, `api_name`
(`timetables/v1/fchg`, `timetables/v1/plan`, plus non-timetable APIs), `query_params`,
`response_data` (raw XML), `status_code` (string, "200" or null), `error`, `duration_ms`.
EVAs in URLs keep HF's leading zero (`/fchg/08000199`). A run (~5 per day) fetches `fchg` for every
station once, then `plan` for the hours of its window; a day's last plan hours (Berlin 00-01 of
the next day) sit in that day's folder, the following `fchg` in the next day's folder.

Laptop-only (parity and reconciliation); the jobs image never imports this module.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import duckdb

from dbdelay.data.hf_backfill import HF_REPO

Kind = Literal["plan", "fchg"]
_URL_PATTERN = r"/timetables/v1/(plan|fchg)/0*([1-9][0-9]{6})(?:/|$)"


class _TreeApi(Protocol):
    def list_repo_tree(self, repo_id: str, *, path_in_repo: str, repo_type: str) -> Any: ...


def hf_raw_folder(day: date) -> str:
    return f"raw_data/year={day.year}/month={day.month}/day={day.day}"


def hf_raw_paths(day: date, *, api: _TreeApi | None = None) -> list[str]:
    """Parquet files needed for UTC ``day``: its own folder plus the next day's (later `fchg`)."""
    if api is None:
        from huggingface_hub import HfApi  # noqa: PLC0415 - dev/pipelines dependency only

        api = HfApi()
    paths: list[str] = []
    for folder_day in (day, day + timedelta(days=1)):
        entries = api.list_repo_tree(
            HF_REPO, path_in_repo=hf_raw_folder(folder_day), repo_type="dataset"
        )
        paths.extend(sorted(e.path for e in entries if e.path.endswith(".parquet")))
    return paths


def iter_hf_raw(
    path: Path, eva_map: Mapping[str, str]
) -> Iterator[tuple[Kind, str, datetime, bytes]]:
    """Successful plan/fchg responses for EVAs in ``eva_map`` (mapped to current EVAs), by time."""
    pattern = "|".join(sorted(eva_map))
    if not pattern:
        return
    query = f"""
        SELECT timestamp, url, response_data FROM read_parquet(?)
        WHERE api_name IN ('timetables/v1/plan', 'timetables/v1/fchg')
          AND status_code = '200' AND response_data IS NOT NULL
          AND regexp_matches(url, '/(plan|fchg)/0*({pattern})(/|$)')
        ORDER BY timestamp
    """  # noqa: S608 - pattern is built from validated 7-digit EVAs only
    rows: Sequence[tuple[datetime, str, str]] = (
        duckdb.connect().execute(query, [path.as_posix()]).fetchall()
    )

    url_re = re.compile(_URL_PATTERN)
    for timestamp, url, body in rows:
        match = url_re.search(url)
        if match is None or match.group(2) not in eva_map:
            continue
        kind: Kind = "plan" if match.group(1) == "plan" else "fchg"
        yield kind, eva_map[match.group(2)], timestamp.replace(tzinfo=UTC), body.encode("utf-8")

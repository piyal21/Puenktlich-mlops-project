"""backfill_history — HF monthly history → bronze → silver (docs/architecture.md §5.1).

Thin orchestration only; all logic lives in `dbdelay.data`.
"""

from datetime import timedelta
from typing import Any

from airflow.sdk import Param, dag, get_current_context, task

DEFAULT_MONTHS = [
    "2025-12",
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
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

    # No retries: a data-validation failure must not be retried silently.
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
        keys = ("month", "rows_out", "late_rate", "edge_complete", "quarantined", "content_hash")
        for report in sorted(reports, key=lambda item: item["month"]):
            log.info("month summary", extra={k: report[k] for k in keys})

    months = plan_months()
    ingested = ingest.expand(month=months)
    summarize(build_silver.expand(month=ingested))


backfill_history()

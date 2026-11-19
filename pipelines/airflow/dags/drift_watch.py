"""drift_watch — pick up a retrain request and run `training_pipeline` for it (Phase 9, spec §4.5).

Every 30 min: if local MinIO holds `control/retrain_requested.json` in state `requested` (copied
from AWS by `make retrain-sync`), mark it `picked_up`, trigger `training_pipeline` with its
`request_id` and wait. Afterwards mark it `done`; if the pipeline wrote no decision (it failed
before the gate), record a `failed` decision. Airflow never talks to AWS (route A).

A short-circuit task on the 30-min schedule replaces the sensor of architecture §5.1: same effect,
no worker slot held between checks and no triggerer service needed.
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import dag, task


def _stores() -> tuple[object, object]:
    from dbdelay.config import get_settings
    from dbdelay.storage import ObjectStore, make_s3_client

    settings = get_settings()
    client = make_s3_client(settings)
    return ObjectStore(client, settings.data_bucket), ObjectStore(client, settings.models_bucket)


@dag(
    dag_id="drift_watch",
    schedule=timedelta(minutes=30),
    start_date=datetime(2026, 11, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["phase-9", "monitoring"],
    default_args={"retries": 0},
)
def drift_watch() -> None:
    @task.short_circuit
    def pick_up() -> str | bool:
        from dbdelay.monitoring.control import mark, pending_request

        data, _ = _stores()
        request = pending_request(data)  # type: ignore[arg-type]
        if request is None:
            return False
        mark(data, request, "picked_up", datetime.now(UTC))  # type: ignore[arg-type]
        return request.request_id

    run_training = TriggerDagRunOperator(
        task_id="run_training",
        trigger_dag_id="training_pipeline",
        conf={"request_id": "{{ ti.xcom_pull(task_ids='pick_up') }}"},
        wait_for_completion=True,
        poke_interval=60,
    )

    @task(trigger_rule="all_done")
    def finish(request_id: str) -> None:
        from dbdelay.monitoring.control import mark, record_failure
        from dbdelay.monitoring.job import read_flag
        from dbdelay.registry.pointer import ObjectStorePointer

        data, models = _stores()
        flag = read_flag(data)  # type: ignore[arg-type]
        if flag is None or flag.request_id != request_id:
            return
        state = ObjectStorePointer(models).get()  # type: ignore[arg-type]
        champion = state.champion_version if state else None
        now = datetime.now(UTC)
        record_failure(data, flag, champion=champion, now=now)  # type: ignore[arg-type]
        mark(data, flag, "done", now)  # type: ignore[arg-type]

    picked = pick_up()
    picked >> run_training >> finish(picked)  # type: ignore[arg-type]


drift_watch()

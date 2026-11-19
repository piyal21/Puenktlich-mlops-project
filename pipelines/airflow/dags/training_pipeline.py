"""training_pipeline — snapshot → train → calibrate → evaluate → register → gate → release
(docs/architecture.md §5.2). `sync_live_silver` joins in Phase 7.

Thin orchestration only; every step is a function in `dbdelay.training.pipeline`. The MLflow run
carries the model between tasks; XCom carries ids only. Task arguments must not use Airflow
context names (`run_id` is the DAG run's id), hence `mlflow_run_id`. A rejected challenger
ends green. Phase 9: `record_decision` writes `control/decisions/<request_id>.json` after either
branch; `request_id` comes from the triggering `drift_watch` run's conf (else "scheduled-<date>").
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from airflow.sdk import dag, task


def _run(step: Callable[..., Any], *args: Any) -> Any:
    """Run one pipeline step with a fresh context and temp dir."""
    import tempfile
    from pathlib import Path

    from dbdelay.config import get_settings
    from dbdelay.training.config import load_training_config
    from dbdelay.training.pipeline import make_context

    cfg = load_training_config(get_settings().training_config_file)
    with tempfile.TemporaryDirectory() as tmp:
        return step(make_context(cfg, Path(tmp)), *args)


def _record_decision(registered: dict[str, str], conf: dict[str, Any]) -> None:
    """Decision record for this run; `request_id` from drift_watch's conf, else scheduled."""
    from datetime import UTC, datetime

    from dbdelay.training import pipeline

    now = datetime.now(UTC)
    request_id = str(conf.get("request_id") or f"scheduled-{now:%Y-%m-%d}")
    _run(
        pipeline.record_decision,
        request_id,
        registered["mlflow_run_id"],
        registered["version"],
        now,
    )


@dag(
    dag_id="training_pipeline",
    schedule="@monthly",
    catchup=False,
    max_active_runs=1,
    tags=["phase-4", "training"],
    # No retries by default: a retried step would start a second MLflow run / version.
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
    def calibrate(snapshot_id: str, mlflow_run_id: str) -> str:
        from dbdelay.training import pipeline

        _run(pipeline.calibrate_model, snapshot_id, mlflow_run_id)
        return mlflow_run_id

    @task
    def evaluate(snapshot_id: str, mlflow_run_id: str) -> str:
        from dbdelay.training import pipeline

        _run(pipeline.evaluate_models, snapshot_id, mlflow_run_id)
        return mlflow_run_id

    @task
    def register(mlflow_run_id: str) -> dict[str, str]:
        from dbdelay.training import pipeline

        version = str(_run(pipeline.register_model, mlflow_run_id))
        return {"mlflow_run_id": mlflow_run_id, "version": version}

    @task.branch
    def gate(registered: dict[str, str]) -> str:
        from dbdelay.training import pipeline

        decision = _run(pipeline.run_gate, registered["mlflow_run_id"], registered["version"])
        return "release" if decision.passed else "record_rejection"

    @task
    def record_rejection(registered: dict[str, str]) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.record_rejection, registered["mlflow_run_id"], registered["version"])

    @task
    def release(registered: dict[str, str]) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.release_model, registered["mlflow_run_id"], registered["version"])

    @task
    def smoke_test(snapshot_id: str, registered: dict[str, str]) -> None:
        from dbdelay.training import pipeline

        _run(pipeline.smoke_test, snapshot_id, registered["mlflow_run_id"], registered["version"])

    @task(trigger_rule="none_failed_min_one_success")
    def record_decision(registered: dict[str, str]) -> None:
        from airflow.sdk import get_current_context

        _record_decision(registered, get_current_context()["dag_run"].conf or {})

    snapshot_id = validate_snapshot(build_training_set())
    baseline = train_baseline(snapshot_id)
    evaluated = evaluate(snapshot_id, calibrate(snapshot_id, train_lightgbm(snapshot_id)))
    baseline >> evaluated
    registered = register(evaluated)
    branch = gate(registered)
    released = release(registered)
    rejected = record_rejection(registered)
    branch >> [rejected, released]
    smoked = smoke_test(snapshot_id, registered)
    released >> smoked
    [rejected, smoked] >> record_decision(registered)


training_pipeline()

"""Parse DAGs inside the Airflow image and assert the DAGs' shape (`make test-dags`)."""

import inspect
import sys

from airflow.sdk.definitions.context import Context

try:
    from airflow.dag_processing.dagbag import DagBag
except ImportError:  # older 3.x location
    from airflow.models.dagbag import DagBag

EXPECTED_MONTHS = [
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

bag = DagBag(dag_folder="/opt/airflow/dags")  # examples are off via AIRFLOW__CORE__LOAD_EXAMPLES
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
TRAINING_TASKS = {
    "build_training_set",
    "validate_snapshot",
    "train_baseline",
    "train_lightgbm",
    "calibrate",
    "evaluate",
    "register",
    "gate",
    "record_rejection",
    "release",
    "smoke_test",
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
# TaskFlow arguments must not be named like context keys (e.g. `run_id`): Airflow raises
# "is a part of kwargs and therefore reserved" only when the task runs.
reserved = set(Context.__annotations__)
for parsed in bag.dags.values():
    for parsed_task in parsed.tasks:
        func = getattr(parsed_task, "python_callable", None)
        if func is not None:
            clash = reserved & set(inspect.signature(func).parameters)
            if clash:
                errors.append(f"{parsed.dag_id}.{parsed_task.task_id} uses reserved names {clash}")
if errors:
    sys.stderr.write("\n".join(errors) + "\n")
    sys.exit(1)
sys.stdout.write("DAG check passed\n")

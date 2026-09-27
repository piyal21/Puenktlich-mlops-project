"""Parse DAGs inside the Airflow image and assert the backfill DAG's shape (`make test-dags`)."""

import sys

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
if errors:
    sys.stderr.write("\n".join(errors) + "\n")
    sys.exit(1)
sys.stdout.write("DAG check passed\n")

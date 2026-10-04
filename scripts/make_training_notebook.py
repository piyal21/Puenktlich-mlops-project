"""Write notebooks/03_training.ipynb (champion metrics, calibration and importance plots)."""

from pathlib import Path

import nbformat

CELLS = [
    nbformat.v4.new_markdown_cell(
        "# 03 — Training\n\nCurrent champion from `models/_pointer.json`: test metrics vs the "
        "baseline, calibration curve and feature importance (from its MLflow run). "
        "No row data is shown. Needs the local stack (`make up`)."
    ),
    nbformat.v4.new_code_cell(
        "import json\nimport os\nfrom pathlib import Path\n\n"
        "import matplotlib.pyplot as plt\nimport pandas as pd\n\n"
        "# Settings read `.env` from the working directory: run from the repo root.\n"
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
        "run_id = bundle.manifest.mlflow_run_id\n"
        "tracker = MlflowTracker(settings.mlflow_tracking_uri)\n"
        "report = TrainingReport.model_validate_json(tracker.load_bytes(run_id, "
        "'bundle/metrics.json'))\n"
        "version, report.snapshot_id"
    ),
    nbformat.v4.new_code_cell(
        "rows = {'challenger': report.challenger_test, 'baseline': report.baseline_test}\n"
        "if report.champion_test is not None:\n"
        "    rows['previous champion'] = report.champion_test\n"
        "pd.DataFrame({k: v.overall.model_dump() for k, v in rows.items()}).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "slices = {\n"
        "    name: {k: m.roc_auc for k, m in split.slices['train_type'].items()}\n"
        "    for name, split in rows.items()\n"
        "}\n"
        "pd.DataFrame(slices).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "bins = pd.DataFrame([b.model_dump() for b in report.challenger_test.calibration])\n"
        "fig, ax = plt.subplots(figsize=(5, 5))\n"
        "ax.plot([0, 1], [0, 1], linestyle='--', color='grey', label='perfect')\n"
        "ax.plot(bins['mean_pred'], bins['observed_rate'], marker='o', label='champion (test)')\n"
        "ax.set_xlabel('mean predicted p_late')\nax.set_ylabel('observed late rate')\n"
        "ax.set_title('Calibration (test split)')\nax.legend()\nplt.show()"
    ),
    nbformat.v4.new_code_cell(
        "importance = json.loads(tracker.load_bytes(run_id, 'feature_importance.json'))\n"
        "gain = pd.Series(importance['gain']).sort_values()\n"
        "gain.plot.barh(figsize=(6, 4), title='Feature importance (gain)')\nplt.show()"
    ),
]


def main() -> None:
    notebook = nbformat.v4.new_notebook(cells=CELLS)
    nbformat.write(notebook, Path("notebooks/03_training.ipynb"))


if __name__ == "__main__":
    main()

"""Write notebooks/02_baseline.ipynb (metrics table + calibration plot of the latest snapshot)."""

from pathlib import Path

import nbformat

CELLS = [
    nbformat.v4.new_markdown_cell(
        "# 02 — Baseline\n\nLate-rate lookup baseline on the Phase 3 gold snapshot "
        "(`make baseline`). Reads `metrics.json` from MinIO; no row data is shown."
    ),
    nbformat.v4.new_code_cell(
        "import json\nimport os\nfrom pathlib import Path\n\n"
        "import matplotlib.pyplot as plt\nimport pandas as pd\n\n"
        "# Settings read `.env` from the working directory: run from the repo root.\n"
        "if Path.cwd().name == 'notebooks':\n    os.chdir('..')\n\n"
        "from dbdelay.config import get_settings\n"
        "from dbdelay.storage import ObjectStore, make_s3_client\n\n"
        "settings = get_settings()\n"
        "store = ObjectStore(make_s3_client(settings), settings.data_bucket)\n"
        "keys = sorted(\n"
        "    k\n"
        "    for k in store.iter_keys('gold/training_sets/')\n"
        "    if k.endswith('baseline/metrics.json')\n"
        ")\n"
        "report = json.loads(store.get_bytes(keys[-1]))\nreport['snapshot_id']"
    ),
    nbformat.v4.new_code_cell(
        "overall = {name: split['overall'] for name, split in report['splits'].items()}\n"
        "pd.DataFrame(overall).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "rows = [\n"
        "    {'train_type': k, **v}\n"
        "    for k, v in report['splits']['test']['slices']['train_type'].items()\n"
        "]\n"
        "pd.DataFrame(rows).sort_values('n', ascending=False).round(4)"
    ),
    nbformat.v4.new_code_cell(
        "fig, ax = plt.subplots(figsize=(5, 5))\n"
        "for name, split in report['splits'].items():\n"
        "    bins = pd.DataFrame(split['calibration'])\n"
        "    ax.plot(bins['mean_pred'], bins['observed_rate'], marker='o', label=name)\n"
        "ax.plot([0, 1], [0, 1], linestyle='--', color='grey', label='perfect')\n"
        "ax.set(\n"
        "    xlabel='predicted late probability',\n"
        "    ylabel='observed late rate',\n"
        "    title='Baseline calibration',\n"
        ")\n"
        "ax.legend()\n"
        "plt.show()"
    ),
]

notebook = nbformat.v4.new_notebook(cells=CELLS)
Path("notebooks/02_baseline.ipynb").write_text(nbformat.writes(notebook), encoding="utf-8")

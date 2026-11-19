"""The serving path (API image) must import without packages that image does not install.

scikit-learn (slim API image, Phase 5 spec §5); jobs-image and training-only packages (Phase 9:
the monitor runs on the API image)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# Later tasks append the serving and API modules.
SERVING_MODULES = [
    "dbdelay.registry.artifacts",
    "dbdelay.training.calibrate",
    "dbdelay.serving.model_loader",
    "dbdelay.serving.board",
    "dbdelay.serving.explain",
    "dbdelay.serving.scoring",
    "dbdelay.serving.stations",
    "dbdelay.data.gaps",
    "dbdelay.monitoring.config",
    "dbdelay.monitoring.performance",
    "dbdelay.monitoring.drift",
    "dbdelay.monitoring.history",
    "dbdelay.monitoring.triggers",
    "dbdelay.monitoring.job",
    "monitor_handler",
    "app.main",
]

_BLOCK_SKLEARN = """
import importlib, importlib.abc, sys

class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"sklearn", "defusedxml", "httpx", "tenacity", "mlflow"}:
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Block())
importlib.import_module(sys.argv[1])
"""


@pytest.mark.parametrize("module", SERVING_MODULES)
def test_imports_without_sklearn(module: str) -> None:
    paths = [str(REPO / "src"), str(REPO / "services" / "api")]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and inline script
        [sys.executable, "-c", _BLOCK_SKLEARN, module],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(paths)},
    )
    assert result.returncode == 0, result.stderr[-2000:]

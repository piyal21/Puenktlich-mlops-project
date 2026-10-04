"""Model card draft (markdown) written with every trained model (prd §8)."""

from collections.abc import Mapping

from dbdelay.features.spec import RiskThresholds
from dbdelay.training.evaluate import SplitReport, TrainingReport
from dbdelay.training.gate import SLICE_COLUMN

_METRICS = (("Brier", "brier"), ("ROC-AUC", "roc_auc"), ("PR-AUC", "pr_auc"), ("ECE", "ece"))
LIMITATIONS = (
    "Timetable/calendar features only (v1): no live context, weather or incidents.",
    "Trained on December to August history; September to November months were never seen "
    "(`month` is extrapolated).",
    "The valid split drives early stopping, grid selection and calibration; only the test "
    "split is untouched.",
    "Label: departure ≥ 6 min late; cancelled departures are excluded, not predicted.",
)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _overall(split: SplitReport | None, name: str) -> float | None:
    if split is None:
        return None
    value = split.overall.model_dump()[name]
    return None if value is None else float(value)


def _slice_auc(split: SplitReport | None, key: str) -> float | None:
    if split is None:
        return None
    metrics = split.slices.get(SLICE_COLUMN, {}).get(key)
    return metrics.roc_auc if metrics else None


def _row(label: str, values: list[float | None]) -> str:
    return f"| {label} | " + " | ".join(_fmt(v) for v in values) + " |"


def render_model_card(
    report: TrainingReport,
    *,
    model_name: str,
    params: Mapping[str, object],
    risk: RiskThresholds,
) -> str:
    """Markdown card: use, data, metrics vs baseline/champion, slices, risk, limitations."""
    champion = (
        f"champion v{report.champion_version}" if report.champion_version else "no champion yet"
    )
    columns: list[tuple[str, SplitReport | None]] = [
        ("challenger", report.challenger_test),
        ("baseline", report.baseline_test),
        (champion, report.champion_test),
    ]
    header = "| {} | " + " | ".join(name for name, _ in columns) + " |"
    rule = "|---|" + "---|" * len(columns)
    lines = [
        f"# Model card — {model_name}",
        "",
        "Draft written at training time; the version is assigned at registration and the gate "
        "result is recorded in MLflow (`gate.json`).",
        "",
        "## Intended use",
        "Probability that a Deutsche Bahn departure at a supported station leaves ≥ 6 minutes "
        "late, shown as a risk badge to riders. Not for operational or safety decisions.",
        "",
        "## Data",
        f"- Snapshot: `{report.snapshot_id}`",
        f"- Train window: {report.train_start} → {report.train_end}",
        f"- Test rows: {report.test_rows:,}",
        f"- Trained at: {report.trained_at:%Y-%m-%d %H:%M} UTC · git `{report.git_sha}`",
        "- Parameters: " + ", ".join(f"`{k}={v}`" for k, v in sorted(params.items())),
        "",
        "## Metrics (test split)",
        header.format("Metric"),
        rule,
        *(_row(label, [_overall(s, field) for _, s in columns]) for label, field in _METRICS),
        "",
        "## Slices (ROC-AUC by train type)",
        header.format("Train type"),
        rule,
        *(
            _row(key, [_slice_auc(s, key) for _, s in columns])
            for key in sorted(report.challenger_test.slices.get(SLICE_COLUMN, {}))
        ),
        "",
        "## Risk levels",
        f"Low < {risk.medium:.2f} ≤ Medium < {risk.high:.2f} ≤ High (calibrated `p_late`).",
        "",
        "## Limitations",
        *(f"- {item}" for item in LIMITATIONS),
        "",
    ]
    return "\n".join(lines)

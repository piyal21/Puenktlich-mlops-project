"""Promotion gate: challenger vs baseline and champion on the same test rows (architecture §5.4)."""

from pydantic import BaseModel

from dbdelay.training.config import GateConfig
from dbdelay.training.evaluate import SplitReport, TrainingReport

SLICE_COLUMN = "train_type"


class Check(BaseModel):
    name: str
    passed: bool
    value: float | None
    limit: float | None
    detail: str = ""


class GateDecision(BaseModel):
    """`gate.json`."""

    passed: bool
    champion_version: str | None
    checks: list[Check]

    @property
    def failed(self) -> list[str]:
        return [check.name for check in self.checks if not check.passed]


def _missing(name: str) -> Check:
    return Check(name=name, passed=False, value=None, limit=None, detail="metric missing")


def _beats_baseline(challenger: SplitReport, baseline: SplitReport, cfg: GateConfig) -> Check:
    mine, theirs = challenger.overall.brier, baseline.overall.brier
    if mine is None or theirs is None:
        return _missing("beats_baseline")
    limit = theirs * (1 - cfg.min_brier_improvement_vs_baseline)
    return Check(name="beats_baseline", passed=mine <= limit, value=mine, limit=limit)


def _brier_vs_champion(challenger: SplitReport, champion: SplitReport, cfg: GateConfig) -> Check:
    mine, theirs = challenger.overall.brier, champion.overall.brier
    if mine is None or theirs is None:
        return _missing("brier_vs_champion")
    limit = theirs + cfg.max_brier_regression_vs_champion
    # Strict: an equal Brier is "no improvement" -> rejected (owner decision 2026-10-04).
    return Check(name="brier_vs_champion", passed=mine < limit, value=mine, limit=limit)


def _auc_vs_champion(challenger: SplitReport, champion: SplitReport, cfg: GateConfig) -> Check:
    mine, theirs = challenger.overall.roc_auc, champion.overall.roc_auc
    if mine is None or theirs is None:
        return _missing("auc_vs_champion")
    limit = theirs - cfg.max_auc_drop_vs_champion
    return Check(name="auc_vs_champion", passed=mine >= limit, value=mine, limit=limit)


def _slice_auc(challenger: SplitReport, reference: SplitReport, cfg: GateConfig) -> Check:
    """Per-`train_type` AUC drop vs the reference; slices without AUC on either side are skipped."""
    ours = challenger.slices.get(SLICE_COLUMN, {})
    theirs = reference.slices.get(SLICE_COLUMN, {})
    failed: list[str] = []
    skipped: list[str] = []
    worst: float | None = None
    for key in sorted(ours):
        mine = ours[key].roc_auc
        other = theirs[key].roc_auc if key in theirs else None
        if mine is None or other is None:
            skipped.append(key)
            continue
        drop = other - mine
        worst = drop if worst is None else max(worst, drop)
        if drop > cfg.max_slice_auc_drop:
            failed.append(f"{key} ({drop:.4f})")
    parts = []
    if failed:
        parts.append(f"failed: {', '.join(failed)}")
    if skipped:
        parts.append(f"skipped: {', '.join(skipped)}")
    return Check(
        name="slice_auc",
        passed=not failed,
        value=worst,
        limit=cfg.max_slice_auc_drop,
        detail="; ".join(parts),
    )


def evaluate_gate(report: TrainingReport, cfg: GateConfig) -> GateDecision:
    """All gate checks; passes only if every check passes.

    Without a champion only the row-count, baseline and slice (vs baseline) checks apply.
    """
    challenger = report.challenger_test
    checks = [
        Check(
            name="min_test_rows",
            passed=report.test_rows >= cfg.min_test_rows,
            value=float(report.test_rows),
            limit=float(cfg.min_test_rows),
        ),
        _beats_baseline(challenger, report.baseline_test, cfg),
    ]
    reference = report.baseline_test
    if report.champion_test is not None:
        checks.append(_brier_vs_champion(challenger, report.champion_test, cfg))
        checks.append(_auc_vs_champion(challenger, report.champion_test, cfg))
        reference = report.champion_test
    checks.append(_slice_auc(challenger, reference, cfg))
    return GateDecision(
        passed=all(check.passed for check in checks),
        champion_version=report.champion_version,
        checks=checks,
    )

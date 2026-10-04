from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from dbdelay.training.config import GateConfig
from dbdelay.training.evaluate import Metrics, SplitReport, TrainingReport
from dbdelay.training.gate import GateDecision, evaluate_gate

GATE = GateConfig(
    min_brier_improvement_vs_baseline=0.05,
    max_brier_regression_vs_champion=0.0,
    max_auc_drop_vs_champion=0.005,
    max_slice_auc_drop=0.02,
    min_test_rows=1000,
)


def split(brier: float | None, auc: float | None, slices: dict[str, float | None]) -> SplitReport:
    return SplitReport(
        overall=Metrics(n=2000, base_rate=0.3, brier=brier, roc_auc=auc),
        calibration=[],
        slices={
            "train_type": {k: Metrics(n=500, base_rate=0.3, roc_auc=v) for k, v in slices.items()}
        },
    )


def report(
    challenger: SplitReport,
    baseline: SplitReport,
    champion: SplitReport | None = None,
    test_rows: int = 2000,
) -> TrainingReport:
    return TrainingReport(
        snapshot_id="2026-08-31_abcdef12",
        spec_hash="0" * 64,
        git_sha="abc1234",
        trained_at=datetime(2026, 10, 4, tzinfo=UTC),
        train_start=date(2025, 12, 1),
        train_end=date(2026, 8, 3),
        test_rows=test_rows,
        challenger_valid=challenger,
        challenger_test=challenger,
        baseline_test=baseline,
        champion_version="3" if champion else None,
        champion_test=champion,
    )


BASE = split(0.1520, 0.77, {"ICE": 0.70, "RE": 0.75})
GOOD = split(0.1400, 0.80, {"ICE": 0.72, "RE": 0.78})


def names(decision: GateDecision) -> dict[str, bool]:
    return {c.name: c.passed for c in decision.checks}


def test_first_run_passes_with_baseline_and_slice_checks_only() -> None:
    decision = evaluate_gate(report(GOOD, BASE), GATE)
    assert decision.passed
    assert names(decision) == {"min_test_rows": True, "beats_baseline": True, "slice_auc": True}


def test_baseline_margin_is_relative() -> None:
    limit = 0.1520 * 0.95
    just_over = split(limit + 1e-6, 0.80, {"ICE": 0.72})
    exactly = split(limit, 0.80, {"ICE": 0.72})
    assert not evaluate_gate(report(just_over, BASE), GATE).passed
    assert evaluate_gate(report(exactly, BASE), GATE).passed


def test_tie_with_champion_is_rejected() -> None:
    decision = evaluate_gate(report(GOOD, BASE, champion=GOOD), GATE)
    assert not decision.passed
    assert decision.failed == ["brier_vs_champion"]
    assert decision.champion_version == "3"


def test_better_than_champion_passes() -> None:
    champion = split(0.1410, 0.80, {"ICE": 0.72, "RE": 0.78})
    assert evaluate_gate(report(GOOD, BASE, champion=champion), GATE).passed


def test_auc_drop_vs_champion_fails() -> None:
    champion = split(0.1450, 0.81, {"ICE": 0.72, "RE": 0.78})
    assert evaluate_gate(report(GOOD, BASE, champion=champion), GATE).failed == ["auc_vs_champion"]


def test_slice_regression_fails_and_names_slice() -> None:
    worse_ice = split(0.1400, 0.80, {"ICE": 0.67, "RE": 0.78})
    decision = evaluate_gate(report(worse_ice, BASE), GATE)
    assert decision.failed == ["slice_auc"]
    assert "ICE" in decision.checks[-1].detail


def test_null_or_unknown_slices_are_skipped_not_failed() -> None:
    challenger = split(0.1400, 0.80, {"ICE": None, "RE": 0.78, "S": 0.6})
    decision = evaluate_gate(report(challenger, BASE), GATE)
    assert decision.passed
    detail = decision.checks[-1].detail
    assert "skipped" in detail
    assert "ICE" in detail
    assert "S" in detail


def test_too_few_test_rows_fail() -> None:
    assert evaluate_gate(report(GOOD, BASE, test_rows=999), GATE).failed == ["min_test_rows"]


def test_missing_overall_metric_fails_closed() -> None:
    decision = evaluate_gate(report(split(None, None, {}), BASE), GATE)
    assert "beats_baseline" in decision.failed


def test_champion_pair_must_be_complete() -> None:
    with pytest.raises(ValidationError, match="come together"):
        TrainingReport.model_validate(report(GOOD, BASE).model_dump() | {"champion_version": "1"})

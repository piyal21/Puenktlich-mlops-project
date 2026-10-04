from dbdelay.features.spec import RiskThresholds
from dbdelay.training.model_card import render_model_card
from tests.unit.test_gate import BASE, GOOD, report

RISK = RiskThresholds(medium=0.2, high=0.45)


def test_card_has_required_sections_and_numbers() -> None:
    card = render_model_card(
        report(GOOD, BASE, champion=BASE),
        model_name="puenktlich-delay",
        params={"num_leaves": 31},
        risk=RISK,
    )
    for heading in (
        "# Model card — puenktlich-delay",
        "## Intended use",
        "## Data",
        "## Metrics (test split)",
        "## Slices (ROC-AUC by train type)",
        "## Risk levels",
        "## Limitations",
    ):
        assert heading in card
    assert "0.1400" in card  # challenger Brier
    assert "0.1520" in card  # baseline Brier
    assert "champion v3" in card
    assert "2026-08-31_abcdef12" in card
    assert "`num_leaves=31`" in card
    assert "| ICE | 0.7200 | 0.7000 | 0.7000 |" in card


def test_card_without_champion_says_first_model() -> None:
    card = render_model_card(report(GOOD, BASE), model_name="m", params={}, risk=RISK)
    assert "no champion yet" in card
    assert "| Brier | 0.1400 | 0.1520 | — |" in card

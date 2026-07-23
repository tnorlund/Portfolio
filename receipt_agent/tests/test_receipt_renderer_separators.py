from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridWord,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    _inferred_policy_separator_baselines,
)


def _row(text: str) -> list[GridWord]:
    return [
        GridWord(
            left=20.0,
            top=680.0,
            right=300.0,
            bottom=700.0,
            text=text,
            ink=(0, 0, 0),
        )
    ]


def test_infers_rule_in_blank_row_before_lower_policy_copy() -> None:
    result = _inferred_policy_separator_baselines(
        [_row("by email at Grocer.example"), _row("Please keep your receipt")],
        ["HEADER", "FOOTER"],
        [700.0, 760.0],
        min_pitch=30.0,
        cap_h=20.0,
        content_top=10.0,
        content_height=1000.0,
    )

    assert result == [740.0]


def test_does_not_infer_rule_without_website_boundary_or_blank_row() -> None:
    common = {
        "sections": ["HEADER", "FOOTER"],
        "min_pitch": 30.0,
        "cap_h": 20.0,
        "content_top": 10.0,
        "content_height": 1000.0,
    }

    no_website = _inferred_policy_separator_baselines(
        [_row("save money save paper"), _row("Please keep your receipt")],
        baselines=[700.0, 760.0],
        **common,
    )
    no_blank_row = _inferred_policy_separator_baselines(
        [_row("visit Grocer.example"), _row("Please keep your receipt")],
        baselines=[700.0, 740.0],
        **common,
    )

    assert no_website == []
    assert no_blank_row == []


def test_does_not_infer_rule_in_top_header() -> None:
    result = _inferred_policy_separator_baselines(
        [_row("visit Grocer.example"), _row("Please keep your receipt")],
        ["HEADER", "FOOTER"],
        [300.0, 360.0],
        min_pitch=30.0,
        cap_h=20.0,
        content_top=10.0,
        content_height=1000.0,
    )

    assert result == []

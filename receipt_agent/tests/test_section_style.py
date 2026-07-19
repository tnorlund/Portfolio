"""section_scale schema: legacy float vs {height_scale, condense} mapping."""

from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    section_style,
)


def test_legacy_float_is_height_only():
    assert section_style(0.8) == (0.8, 1.0)


def test_mapping_carries_height_and_condense():
    assert section_style({"height_scale": 0.9, "condense": 0.85}) == (
        0.9,
        0.85,
    )


def test_mapping_defaults_missing_keys_to_noop():
    assert section_style({"condense": 0.85}) == (1.0, 0.85)
    assert section_style({"height_scale": 0.9}) == (0.9, 1.0)
    assert section_style({}) == (1.0, 1.0)


def test_date_time_header_vote_is_positional():
    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        section_for_labels,
    )

    # In the header zone (default) DATE/TIME are header typography.
    assert section_for_labels(["DATE", "TIME"]) == "HEADER"
    # Low on the paper they are transaction-block reprints, not headers.
    assert section_for_labels(["DATE"], in_header_zone=False) is None
    assert section_for_labels(["TIME"], in_header_zone=False) is None
    # Non-positional header labels still vote HEADER anywhere.
    assert (
        section_for_labels(["MERCHANT_NAME"], in_header_zone=False) == "HEADER"
    )
    # A word with another section's label keeps that section.
    assert (
        section_for_labels(["DATE", "PAYMENT_METHOD"], in_header_zone=False)
        == "PAYMENT"
    )

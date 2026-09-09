"""Explicit receipt units must agree with the existing decoder's arithmetic."""

from decimal import Decimal

import pytest

from receipt_nutrition.quantity import resolve_explicit_quantity


@pytest.mark.parametrize(
    ("raw", "quantity", "rate", "price", "value", "unit"),
    [
        ("1.23 lb @ 1.99/lb", "1.23", "1.99", "2.45", "557.9186151", "g"),
        ("2 kg @ 3.00 6.00", "2", "3", "6", "2000", "g"),
        ("2 kg @ 3.00\n6.00", "2", "3", "6", "2000", "g"),
        ("2 kg @ 3 6.00", "2", "3", "6", None, None),
        ("PEARS\n2 kg @ 3.00/kg", "2", "3", "6", "2000", "g"),
        ("2 kg @ 3.00/kg", "2", "3", "6", "2000", "g"),
        ("8 oz @ 1.00/oz", "8", "1", "8", "226.796185", "g"),
        ("2 pk @ $3.49", "2", "3.49", "6.98", "2", "package"),
        ("3 each @ .50", "3", ".5", "1.50", None, None),
        ("3 each @ 0.50", "3", ".5", "1.50", "3", "each"),
        ("2 fl oz @ 0.50/fl oz", "2", ".5", "1", "59.147059125", "ml"),
        ("2 fl.oz @ 0.50", "2", ".5", "1", "59.147059125", "ml"),
        (
            "1.125 fl oz @ 4.00/fl oz",
            "1.125",
            "4",
            "4.50",
            "33.2702207578125",
            "ml",
        ),
        ("2 ml @ 0.10/ml", "2", ".10", ".20", "2", "ml"),
        ("1 l @ 3.00/l", "1", "3", "3", "1000", "ml"),
    ],
)
def test_explicit_rates(
    raw: str,
    quantity: str,
    rate: str,
    price: str,
    value: str | None,
    unit: str | None,
) -> None:
    result = resolve_explicit_quantity(raw, quantity, rate, price)
    if value is None:
        assert result.status == "unknown"
    else:
        assert result.status == "known"
        assert result.quantity is not None
        assert result.quantity.value == Decimal(value)
        assert result.quantity.unit == unit
        assert result.quantity.reference in raw


@pytest.mark.parametrize(
    ("raw", "quantity", "rate", "price", "status"),
    [
        ("2 @ 3.49", "2", "3.49", "6.98", "unknown"),
        ("12 EGGS 2.99", None, None, "2.99", "unknown"),
        ("2 kg @ 3.00", "3", "3", "6", "conflict"),
        ("2 kg @ 3.00", "2", "4", "6", "conflict"),
        ("2 kg @ 3.00", "2", "3", "5", "conflict"),
        ("2 kg @ 3.00/lb", "2", "3", "6", "conflict"),
        ("2 kg @ 3.00/box", "2", "3", "6", "conflict"),
        ("2 kg @ 3.00 1 kg @ 1.00", "2", "3", "6", "conflict"),
        ("2 kg @ 3.00", None, "3", "6", "unknown"),
        ("-2 kg @ 3.00", "2", "3", "6", "unknown"),
        ("1/2 lb @ 4.00/lb", "2", "4", "8", "unknown"),
        ("1 / 2 lb @ 4.00/lb", "2", "4", "8", "unknown"),
        ("1. 23 lb @ 1.99/lb", "23", "1.99", "45.77", "unknown"),
        ("1'234 g @ 1.00/g", "234", "1", "234", "unknown"),
        ("−2 kg @ 3.00", "2", "3", "6", "unknown"),
        ("1,234 g @ 1.00/g", "234", "1", "234", "unknown"),
        ("1 234 g @ 1.00/g", "234", "1", "234", "unknown"),
        ("2 kg @ 1,000.00/kg", "2", "1", "2", "unknown"),
        ("2 kg @ 1 000.00/kg", "2", "1", "2", "unknown"),
        ("2 lb @ 1 1/2/lb", "2", "1", "2", "unknown"),
        ("2 lb @ 1½/lb", "2", "1", "2", "unknown"),
        ("2 lb @ 1 ½/lb", "2", "1", "2", "unknown"),
        ("2 lb @ 1e2/lb", "2", "1", "2", "unknown"),
        ("1⁄2 lb @ 4.00/lb", "2", "4", "8", "unknown"),
        ("－2 kg @ 3.00", "2", "3", "6", "unknown"),
        ("2 kg @ 1\u202f000.00/kg", "2", "1", "2", "unknown"),
        ("2 kg @ 3.00/(100 g)", "2", "3", "6", "unknown"),
        ("2 kg @ 3.00 per lb", "2", "3", "6", "unknown"),
        ("PEARS 2 kg @ 3.00", "2", "3", "6", "unknown"),
        ("2 kg\n@ 3.00", "2", "3", "6", "unknown"),
        ("2 kg @ 3.00 9.00", "2", "3", "6", "conflict"),
        ("2 kg @ 3.00\n9.00", "2", "3", "6", "conflict"),
        ("2 kg @ 1 002.00", "2", "1", "2", "unknown"),
        ("0 kg @ 3.00", "0", "3", "0", "conflict"),
    ],
)
def test_abstention(
    raw: str, quantity: str | None, rate: str | None, price: str, status: str
) -> None:
    result = resolve_explicit_quantity(raw, quantity, rate, price)
    assert result.status == status
    assert result.quantity is None


def test_adjacent_numeric_fragment_abstains() -> None:
    """A split fraction must not be confirmed from its rate half alone."""
    result = resolve_explicit_quantity("1/\n2 lb @ 4.00/lb", "2", "4", "8")
    assert result.status == "unknown"
    assert result.reason == "adjacent_numeric_fragment"


def test_spaced_fraction_fragment_abstains() -> None:
    result = resolve_explicit_quantity("1 /\n2 lb @ 4.00/lb", "2", "4", "8")
    assert result.status == "unknown"
    assert result.reason == "adjacent_numeric_fragment"


@pytest.mark.parametrize(
    "raw",
    ["1\u2044\n2 lb @ 4.00/lb", "1,\n234 g @ 1.00/g", "1 /\n2 lb @ 4.00/lb"],
)
def test_letterless_numeric_fragments_abstain(raw: str) -> None:
    quantity, rate, price = (
        ("2", "4", "8") if "lb" in raw else ("234", "1", "234")
    )
    result = resolve_explicit_quantity(raw, quantity, rate, price)
    assert result.status == "unknown"
    assert result.reason == "adjacent_numeric_fragment"


def test_decoder_agreement_is_exact_before_cents() -> None:
    quantity = "0.1098528174305033809917355371909090909091"
    rate = "999999999989"
    raw = f"{quantity} each @ {rate}"
    exact = resolve_explicit_quantity(raw, quantity, rate, "109852817429.29")
    wrong = resolve_explicit_quantity(raw, quantity, rate, "109852817429.30")
    assert exact.status == "known"
    assert wrong.status == "conflict"


@pytest.mark.parametrize(
    "raw",
    [
        "PEARS 1/\n2 lb @ 4.00/lb",
        "PEARS 1\n2 lb @ 4.00/lb",
        "PEARS 1/2 lb\n2 lb @ 4.00/lb",
    ],
)
def test_name_lines_with_partial_quantities_abstain(raw: str) -> None:
    result = resolve_explicit_quantity(raw, "2", "4", "8")
    assert result.status == "unknown"
    assert result.reason == "adjacent_numeric_fragment"


def test_plain_name_line_still_resolves() -> None:
    result = resolve_explicit_quantity("PEARS\n2 lb @ 4.00/lb", "2", "4", "8")
    assert result.status == "known"


def test_spaced_fragment_on_name_line_abstains() -> None:
    result = resolve_explicit_quantity(
        "PEARS 1 /\n2 lb @ 4.00/lb", "2", "4", "8"
    )
    assert result.status == "unknown"
    assert result.reason == "adjacent_numeric_fragment"


def test_wrapped_denominator_keeps_the_unit_check() -> None:
    wrapped = resolve_explicit_quantity("2 oz @ 3.00\n/fl oz", "2", "3", "6")
    inline = resolve_explicit_quantity("2 oz @ 3.00/fl oz", "2", "3", "6")
    assert wrapped.status == inline.status == "conflict"
    agreeing = resolve_explicit_quantity("2 oz @ 3.00\n/oz", "2", "3", "6")
    assert agreeing.status == "known"


def test_parenthesised_wrapped_denominator_is_not_confirmed() -> None:
    wrapped = resolve_explicit_quantity("2 oz @ 3.00\n/(fl oz)", "2", "3", "6")
    inline = resolve_explicit_quantity("2 oz @ 3.00/(fl oz)", "2", "3", "6")
    assert wrapped.status == inline.status == "unknown"

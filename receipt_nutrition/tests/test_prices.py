"""A superseded price is never authoritative; conversions stay exact."""

from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest
from receipt_dynamo.entities.price_observation import (
    PriceEvidence,
    PriceObservation,
)

from receipt_nutrition.prices import (
    RateChoice,
    RateSelection,
    owner_rate,
    price_per_kg,
    select_rate,
)

PURCHASE = date(2026, 9, 4)
PER_KG_699 = Fraction(Decimal("6.99")) / Fraction(Decimal("0.45359237"))


def observation(
    price: str,
    *,
    observed_on: date = PURCHASE,
    source: str = "tracker",
    seq: int = 0,
    unit: str = "lb",
    **changes: Any,
) -> PriceObservation:
    fields: dict[str, Any] = dict(
        merchant_slug="costco-wholesale",
        key_kind="ITEM",
        key_text="36946",
        observed_on=observed_on,
        seq=seq,
        unit=unit,
        price_per_unit=Decimal(price),
        source=source,
        verification="user" if source == "owner" else "unverified",
        evidence=PriceEvidence(f"{source}:{price}", observed_on),
    )
    fields.update(changes)
    return PriceObservation(**fields)


def sks(choices: list[RateChoice]) -> list[str | None]:
    return [choice.sk for choice in choices]


def test_two_tracker_prices_give_a_primary_and_a_band() -> None:
    low, high = observation("6.99"), observation("7.49", seq=1)
    selection = select_rate([high, low], purchase_date=PURCHASE)
    assert selection.primary is not None
    # Same day, same source: the later seq is the tracker's latest value.
    assert selection.primary.sk == high.sort_key
    assert {choice.price_per_kg for choice in selection.band} == {
        PER_KG_699,
        Fraction(Decimal("7.49")) / Fraction(Decimal("0.45359237")),
    }
    assert selection.excluded == []
    earlier = observation("6.99", observed_on=date(2026, 8, 30))
    selection = select_rate([high, earlier], purchase_date=PURCHASE)
    assert selection.primary is not None
    assert selection.primary.price_per_kg == Fraction(
        Decimal("7.49")
    ) / Fraction(Decimal("0.45359237"))
    assert sks(selection.band) == [high.sort_key, earlier.sort_key]


def test_card_gate_699_primary_with_both_in_band() -> None:
    low = observation("6.99")
    high = observation("7.49", observed_on=date(2026, 8, 20))
    selection = select_rate([high, low], purchase_date=PURCHASE)
    assert selection.primary is not None
    assert selection.primary.price_per_kg == PER_KG_699
    assert selection.primary.effective_on == PURCHASE
    assert sks(selection.band) == [low.sort_key, high.sort_key]
    assert selection.excluded == []


def test_superseded_row_is_excluded_even_if_it_would_win() -> None:
    stale = observation("6.49", observed_on=date(2026, 9, 3), seq=2)
    current = observation("6.99")
    correction = observation(
        "7.49",
        observed_on=date(2026, 9, 3),
        seq=3,
        supersedes=stale.sort_key,
    )
    selection = select_rate(
        [stale, current, correction], purchase_date=PURCHASE
    )
    assert (stale.sort_key, "superseded") in selection.excluded
    assert stale.sort_key not in sks(selection.band)
    assert selection.primary is not None
    assert selection.primary.sk == current.sort_key
    assert sks(selection.band) == [current.sort_key, correction.sort_key]


def test_correction_after_purchase_supersedes_the_purchase_day_row() -> None:
    original = observation("6.49")
    correction = observation(
        "6.99",
        observed_on=date(2026, 9, 10),
        effective_on=PURCHASE,
        supersedes=original.sort_key,
    )
    selection = select_rate([original, correction], purchase_date=PURCHASE)
    assert selection.primary is not None
    assert selection.primary.sk == correction.sort_key
    assert selection.primary.price_per_kg == PER_KG_699
    assert selection.primary.effective_on == PURCHASE
    assert selection.primary.observed_on == date(2026, 9, 10)
    assert selection.excluded == [(original.sort_key, "superseded")]
    assert sks(selection.band) == [correction.sort_key]
    # Without the effective_on backdate the same correction is future-dated.
    late = observation(
        "6.99", observed_on=date(2026, 9, 10), supersedes=original.sort_key
    )
    selection = select_rate([original, late], purchase_date=PURCHASE)
    assert selection.primary is None
    assert selection.band == []
    assert sorted(selection.excluded) == sorted(
        [
            (original.sort_key, "superseded"),
            (late.sort_key, "future_effective"),
        ]
    )


def test_supersession_chain_leaves_only_the_head() -> None:
    first = observation("6.49")
    second = observation("6.79", seq=1, supersedes=first.sort_key)
    third = observation("6.99", seq=2, supersedes=second.sort_key)
    selection = select_rate([third, first, second], purchase_date=PURCHASE)
    assert sks(selection.band) == [third.sort_key]
    assert sorted(selection.excluded) == sorted(
        [(first.sort_key, "superseded"), (second.sort_key, "superseded")]
    )


def test_same_day_owner_beats_tracker_and_sticker_beats_instacart() -> None:
    tracker = observation("6.99", seq=9)
    owner = observation("7.29", source="owner")
    sticker = observation("7.19", source="sticker")
    instacart = observation("7.39", source="instacart")
    selection = select_rate(
        [tracker, instacart, sticker, owner], purchase_date=PURCHASE
    )
    assert selection.primary is not None
    assert selection.primary.source == "owner"
    assert selection.primary.verification == "user"
    assert [choice.source for choice in selection.band] == [
        "owner",
        "sticker",
        "instacart",
        "tracker",
    ]
    # A newer effective date still outranks every source.
    older_owner = observation(
        "7.29", source="owner", observed_on=date(2026, 9, 1)
    )
    selection = select_rate([tracker, older_owner], purchase_date=PURCHASE)
    assert selection.primary is not None
    assert selection.primary.source == "tracker"


def test_window_bounds_are_inclusive() -> None:
    edge = observation("6.99", observed_on=date(2026, 6, 6))
    outside = observation("5.99", observed_on=date(2026, 6, 5))
    future = observation("8.99", observed_on=date(2026, 9, 5))
    selection = select_rate(
        [edge, outside, future], purchase_date=PURCHASE, window_days=90
    )
    assert sks(selection.band) == [edge.sort_key]
    assert sorted(selection.excluded) == sorted(
        [
            (outside.sort_key, "out_of_window"),
            (future.sort_key, "future_effective"),
        ]
    )
    assert select_rate([outside], purchase_date=PURCHASE) == RateSelection(
        primary=None, band=[], excluded=[(outside.sort_key, "out_of_window")]
    )
    assert select_rate([], purchase_date=PURCHASE).primary is None


def test_each_and_package_prices_never_mix_with_mass() -> None:
    each = observation("12.99", unit="each", source="sticker")
    package = observation("24.99", unit="package", seq=1)
    mass = observation("6.99")
    selection = select_rate([each, package, mass], purchase_date=PURCHASE)
    assert sks(selection.band) == [mass.sort_key]
    assert sorted(selection.excluded) == sorted(
        [
            (each.sort_key, "unit_not_mass"),
            (package.sort_key, "unit_not_mass"),
        ]
    )
    selection = select_rate([each], purchase_date=PURCHASE)
    assert selection.primary is None and selection.band == []


def test_per_lb_per_oz_per_kg_convert_exactly() -> None:
    assert price_per_kg(Decimal("6.99"), "lb") == PER_KG_699
    assert price_per_kg(Decimal("6.99"), "lb") == Fraction(699000000, 45359237)
    assert price_per_kg(Decimal("15.41"), "kg") == Fraction(1541, 100)
    assert price_per_kg(Decimal("0.44"), "oz") == Fraction(
        Decimal("0.44")
    ) / Fraction(Decimal("0.028349523125"))
    # A dollar per ounce is exactly sixteen dollars per pound.
    assert price_per_kg(Decimal("1"), "oz") == price_per_kg(
        Decimal("16"), "lb"
    )
    assert isinstance(price_per_kg(Decimal("6.99"), "lb"), Fraction)
    for bad in (6.99, "6.99", Decimal("0"), Decimal("-1"), Decimal("NaN")):
        with pytest.raises(ValueError):
            price_per_kg(bad, "lb")  # type: ignore[arg-type]
    for unit in ("each", "package", "fl oz", "g"):
        with pytest.raises(ValueError):
            price_per_kg(Decimal("1"), unit)


def test_owner_rate_is_user_verified_without_a_row() -> None:
    rate = owner_rate(Decimal("6.99"), "lb", as_of=PURCHASE)
    assert rate == RateChoice(
        price_per_kg=PER_KG_699,
        source="owner",
        verification="user",
        observed_on=PURCHASE,
        effective_on=PURCHASE,
        sk=None,
    )
    assert isinstance(owner_rate(Decimal("1"), "kg").observed_on, date)
    with pytest.raises(ValueError):
        owner_rate(Decimal("6.99"), "each", as_of=PURCHASE)
    with pytest.raises(ValueError):
        owner_rate(6.99, "lb", as_of=PURCHASE)  # type: ignore[arg-type]


def test_selection_rejects_mixed_keys_and_bad_arguments() -> None:
    with pytest.raises(ValueError, match="more than one"):
        select_rate(
            [observation("6.99"), observation("6.99", key_text="1")],
            purchase_date=PURCHASE,
        )
    with pytest.raises(ValueError):
        select_rate([], purchase_date="2026-09-04")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        select_rate([], purchase_date=PURCHASE, window_days=-1)

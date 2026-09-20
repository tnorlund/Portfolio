"""Select a by-weight rate from stored price observations, exactly.

Pure: no DynamoDB. The caller lists the WHOLE ``PRICE_OBS#`` partition for a
product key (``DynamoClient.list_price_observations``) and hands every row
here, because a correction recorded after the purchase can supersede a row
that was current on the purchase date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Sequence

from receipt_dynamo.entities.price_observation import PriceObservation

from receipt_nutrition.units import UNIT_FACTORS

MASS_UNITS = ("lb", "kg", "oz")
# Same-day ties: a person's typed rate beats a shelf sticker, which beats a
# storefront listing, which beats a third-party tracker.
SOURCE_RANK = {"owner": 3, "sticker": 2, "instacart": 1, "tracker": 0}
GRAMS_PER_KG = 1000


@dataclass(frozen=True)
class RateChoice:
    """One eligible rate in exact $/kg with the provenance that produced it."""

    price_per_kg: Fraction
    source: str
    verification: str
    observed_on: date
    effective_on: date
    sk: str | None
    seq: int = 0

    @property
    def rank(self) -> tuple[date, int, int, date, str]:
        return (
            self.effective_on,
            SOURCE_RANK[self.source],
            self.seq,
            self.observed_on,
            self.sk or "",
        )


@dataclass(frozen=True)
class RateSelection:
    primary: RateChoice | None
    band: list[RateChoice] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)


def price_per_kg(price: Decimal, unit: str) -> Fraction:
    """Exact $/kg from a $/lb, $/kg or $/oz price via ``UNIT_FACTORS``."""
    if isinstance(price, bool) or not isinstance(price, Decimal):
        raise ValueError("price must be a Decimal, never a float")
    if not price.is_finite() or price <= 0:
        raise ValueError("price must be finite and positive")
    if unit not in MASS_UNITS:
        raise ValueError(f"not a mass price unit: {unit}")
    canonical, grams_per_unit = UNIT_FACTORS[unit]
    if canonical != "g":
        raise ValueError(f"unit factor is not a mass: {unit}")
    return Fraction(price) * GRAMS_PER_KG / Fraction(grams_per_unit)


def owner_rate(
    price: Decimal, unit: str, *, as_of: date | None = None
) -> RateChoice:
    """A typed ``--rate``: source owner, user-verified, no band, no row.

    ``as_of`` is the builder's purchase or as-of date; it defaults to today
    only for interactive use, so pass it explicitly from any hashed input.
    """
    stamped = date.today() if as_of is None else as_of
    if type(stamped) is not date:
        raise ValueError("as_of must be a date")
    return RateChoice(
        price_per_kg=price_per_kg(price, unit),
        source="owner",
        verification="user",
        observed_on=stamped,
        effective_on=stamped,
        sk=None,
    )


def rate_choice(observation: PriceObservation) -> RateChoice:
    assert observation.effective_on is not None
    return RateChoice(
        price_per_kg=price_per_kg(
            observation.price_per_unit, observation.unit
        ),
        source=observation.source,
        verification=observation.verification,
        observed_on=observation.observed_on,
        effective_on=observation.effective_on,
        sk=observation.sort_key,
        seq=observation.seq,
    )


def select_rate(
    observations: Sequence[PriceObservation],
    *,
    purchase_date: date,
    window_days: int = 90,
) -> RateSelection:
    """Primary and band mass rates for a purchase; everything else named.

    Rows named by another row's ``supersedes`` are dropped first, whatever
    their dates. The band is every remaining mass observation whose
    ``effective_on`` lies in ``[purchase_date - window_days, purchase_date]``
    inclusive, ordered best first; ``primary`` is its head: latest
    ``effective_on``, then owner > sticker > instacart > tracker, then higher
    seq. An empty band means no primary; the caller reports ``unknown``.
    """
    if type(purchase_date) is not date:
        raise ValueError("purchase_date must be a date")
    if type(window_days) is not int or window_days < 0:
        raise ValueError("window_days must be a non-negative integer")
    partitions = {row.partition for row in observations}
    if len(partitions) > 1:
        raise ValueError("observations span more than one product key")
    superseded = {
        row.supersedes for row in observations if row.supersedes is not None
    }
    window_start = purchase_date - timedelta(days=window_days)
    band: list[RateChoice] = []
    excluded: list[tuple[str, str]] = []
    for row in observations:
        sk = row.sort_key
        assert row.effective_on is not None
        if sk in superseded:
            excluded.append((sk, "superseded"))
        elif row.unit not in MASS_UNITS:
            excluded.append((sk, "unit_not_mass"))
        elif row.effective_on > purchase_date:
            excluded.append((sk, "future_effective"))
        elif row.effective_on < window_start:
            excluded.append((sk, "out_of_window"))
        else:
            band.append(rate_choice(row))
    band.sort(key=lambda choice: choice.rank, reverse=True)
    return RateSelection(
        primary=band[0] if band else None, band=band, excluded=excluded
    )

"""Append-only unit-price observations; corrections supersede, never edit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.nutrition_support import (
    check_nutrition_hash,
    nutrition_item,
    nutrition_key,
    nutrition_values,
)

PRICE_UNITS = ("lb", "kg", "oz", "each", "package")
PRICE_SOURCES = ("sticker", "tracker", "instacart", "owner")
PRICE_VERIFICATIONS = ("unverified", "user")
PRICE_CURRENCY = "USD"
MAX_SEQ = 999
SORT_KEY = re.compile(
    r"DATE#\d{4}-\d{2}-\d{2}#(?:sticker|tracker|instacart|owner)#\d{3}"
)


def price_observation_partition(
    merchant_slug: str, key_kind: str, key_text: str
) -> str:
    if key_kind not in ("TEXT", "ITEM"):
        raise EntityValidationError("price key kind must be TEXT or ITEM")
    return (
        f"PRICE_OBS#{nutrition_key(merchant_slug)}"
        f"#{key_kind}#{nutrition_key(key_text)}"
    )


def price_observation_sort_key(
    observed_on: date, source: str, seq: int
) -> str:
    check_observation_date(observed_on)
    if source not in PRICE_SOURCES:
        raise EntityValidationError("invalid price observation source")
    if type(seq) is not int or not 0 <= seq <= MAX_SEQ:
        raise EntityValidationError("price observation seq must be 0..999")
    return f"DATE#{observed_on.isoformat()}#{source}#{seq:03d}"


def price_observation_key(
    merchant_slug: str,
    key_kind: str,
    key_text: str,
    observed_on: date,
    source: str,
    seq: int,
) -> dict[str, Any]:
    return {
        "PK": {
            "S": price_observation_partition(merchant_slug, key_kind, key_text)
        },
        "SK": {"S": price_observation_sort_key(observed_on, source, seq)},
    }


def check_observation_date(value: object) -> None:
    # datetime subclasses date; a timestamp would silently change the key.
    if type(value) is not date:
        raise EntityValidationError("price observation dates must be dates")


def check_price(value: object) -> Decimal:
    """Prices are exact decimals; floats never enter a stored price."""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise EntityValidationError("price_per_unit must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise EntityValidationError("price_per_unit must be finite and > 0")
    if len(value.as_tuple().digits) > 40:
        raise EntityValidationError("price_per_unit has too many digits")
    return value


def price_text(value: Decimal) -> str:
    """Canonical decimal string, matching ``nutrition_json`` number form."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def read_date(value: object) -> date:
    if not isinstance(value, str):
        raise EntityValidationError("stored date must be text")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise EntityValidationError("invalid stored date") from error


@dataclass(frozen=True, eq=True)
class PriceEvidence:
    reference: str
    observed_on: date
    payload_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise EntityValidationError("price evidence needs a reference")
        if len(self.reference) > 2048:
            raise EntityValidationError("price evidence reference too long")
        check_observation_date(self.observed_on)
        if self.payload_sha256 is not None:
            check_nutrition_hash(self.payload_sha256)

    def to_map(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "observed_on": self.observed_on.isoformat(),
            "payload_sha256": self.payload_sha256,
        }


def evidence_from_map(value: object) -> PriceEvidence:
    if not isinstance(value, dict) or set(value) != {
        "reference",
        "observed_on",
        "payload_sha256",
    }:
        raise EntityValidationError("invalid stored price evidence")
    return PriceEvidence(
        reference=value["reference"],
        observed_on=read_date(value["observed_on"]),
        payload_sha256=value["payload_sha256"],
    )


@dataclass(eq=True)
class PriceObservation(DynamoDBEntity):
    """One observed unit price for a merchant's product key on one day.

    Rows are never updated. A correction is a new row whose ``supersedes``
    names the replaced row's SK and whose ``effective_on`` carries the date
    the corrected price applied to, so readers can drop the replaced row
    even when the correction was recorded after a purchase.
    """

    merchant_slug: str
    key_kind: str
    key_text: str
    observed_on: date
    seq: int
    unit: str
    price_per_unit: Decimal
    source: str
    evidence: PriceEvidence
    currency: str = PRICE_CURRENCY
    verification: str = "unverified"
    effective_on: date | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        price_observation_key(
            self.merchant_slug,
            self.key_kind,
            self.key_text,
            self.observed_on,
            self.source,
            self.seq,
        )
        if self.unit not in PRICE_UNITS:
            raise EntityValidationError("invalid price observation unit")
        self.price_per_unit = check_price(self.price_per_unit)
        if self.currency != PRICE_CURRENCY:
            raise EntityValidationError("price observations are USD only")
        if self.verification not in PRICE_VERIFICATIONS:
            raise EntityValidationError("invalid price verification")
        if self.source == "owner" and self.verification != "user":
            raise EntityValidationError("owner prices are user-verified")
        if self.effective_on is None:
            self.effective_on = self.observed_on
        check_observation_date(self.effective_on)
        if self.supersedes is not None:
            if not isinstance(self.supersedes, str) or not SORT_KEY.fullmatch(
                self.supersedes
            ):
                raise EntityValidationError("supersedes must name a row SK")
            if self.supersedes == self.sort_key:
                raise EntityValidationError("a row cannot supersede itself")
        if not isinstance(self.evidence, PriceEvidence):
            raise EntityValidationError("evidence must be PriceEvidence")

    @property
    def partition(self) -> str:
        return price_observation_partition(
            self.merchant_slug, self.key_kind, self.key_text
        )

    @property
    def sort_key(self) -> str:
        return price_observation_sort_key(
            self.observed_on, self.source, self.seq
        )

    @property
    def key(self) -> dict[str, Any]:
        return {"PK": {"S": self.partition}, "SK": {"S": self.sort_key}}

    def to_item(self) -> dict[str, Any]:
        self.__post_init__()
        assert self.effective_on is not None
        return nutrition_item(
            {
                "TYPE": "PRICE_OBSERVATION",
                "merchant_slug": self.merchant_slug,
                "key_kind": self.key_kind,
                "key_text": self.key_text,
                "observed_on": self.observed_on.isoformat(),
                "seq": self.seq,
                "unit": self.unit,
                "price_per_unit": price_text(self.price_per_unit),
                "currency": self.currency,
                "source": self.source,
                "verification": self.verification,
                "effective_on": self.effective_on.isoformat(),
                "supersedes": self.supersedes,
                "evidence": self.evidence.to_map(),
            },
            key=self.key,
        )


def item_to_price_observation(item: dict[str, Any]) -> PriceObservation:
    values = nutrition_values(item)
    try:
        pk, sk, record_type = (
            values.pop("PK"),
            values.pop("SK"),
            values.pop("TYPE"),
        )
        seq = values["seq"]
        if isinstance(seq, bool) or seq != int(seq):
            raise EntityValidationError("noninteger stored price seq")
        values["seq"] = int(seq)
        price = values["price_per_unit"]
        if not isinstance(price, str):
            raise EntityValidationError("stored price must be text")
        values["price_per_unit"] = Decimal(price)
        values["observed_on"] = read_date(values["observed_on"])
        values["effective_on"] = read_date(values["effective_on"])
        values["evidence"] = evidence_from_map(values["evidence"])
        observation = PriceObservation(**values)
        if record_type != "PRICE_OBSERVATION" or observation.key != {
            "PK": {"S": pk},
            "SK": {"S": sk},
        }:
            raise EntityValidationError("price observation key integrity")
        return observation
    except EntityValidationError:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise EntityValidationError(
            "invalid price observation record"
        ) from error

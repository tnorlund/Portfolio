"""Owner-stated receipt facts the photographed receipt cannot supply.

Some receipts carry no printed date (or a merchant line) on the part of
the receipt that was photographed. The summary is recomputed from word
labels whenever a label changes, so a hand-edited summary field is lost
on the next recompute. A ``ReceiptFactOverride`` is the durable place
for such a fact: every stated fact carries its own provenance (a
``*_reference`` saying how the owner knows it), the row carries
``source`` / ``changed_at`` and a ``revision`` for compare-and-swap
updates, and every summary recompute (the Lambda updater and the
backfill script) applies the stated facts through
:func:`apply_fact_override`.

The row is never deleted by the editing tools: retracting the last fact
leaves a row with no facts so the revision keeps increasing and a stale
``expected_revision`` can never become valid again.

Primary key (mirrors the summary row's receipt partition):
    PK: IMAGE#{image_id}
    SK: RECEIPT#{receipt_id:05d}#FACT_OVERRIDE
    TYPE: RECEIPT_FACT_OVERRIDE (GSITYPE only; no other GSI keys)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date as _date
from datetime import datetime, timezone
from typing import Any, ClassVar

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.identifier_mixins import ReceiptIdentifierMixin
from receipt_dynamo.entities.receipt_summary import ReceiptSummary
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid

# Summary fields an owner may state. Order is the deterministic order in
# which recomputes apply (and record) them.
OVERRIDABLE_FACT_FIELDS: tuple[str, ...] = ("date", "merchant_name")
FACT_OVERRIDE_SOURCES = frozenset({"owner"})
FACT_OVERRIDE_TYPE = "RECEIPT_FACT_OVERRIDE"
FACT_OVERRIDE_SK_SUFFIX = "FACT_OVERRIDE"
MAX_REVISION = 2**53 - 1

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fact_reference_field(name: str) -> str:
    """Name of the provenance field that accompanies fact ``name``."""
    if name not in OVERRIDABLE_FACT_FIELDS:
        raise EntityValidationError(
            f"fact must be one of {list(OVERRIDABLE_FACT_FIELDS)}"
        )
    return f"{name}_reference"


def receipt_fact_override_key(
    image_id: str, receipt_id: int
) -> dict[str, Any]:
    """Primary key of the override row for one receipt."""
    try:
        assert_valid_uuid(image_id)
    except ValueError as exc:
        raise EntityValidationError(str(exc)) from exc
    if type(receipt_id) is not int or receipt_id <= 0:
        raise EntityValidationError("receipt_id must be a positive integer")
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": f"RECEIPT#{receipt_id:05d}#{FACT_OVERRIDE_SK_SUFFIX}"},
    }


def check_fact_revision(value: Any) -> None:
    """Revisions are positive ints that DynamoDB numbers hold exactly."""
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise EntityValidationError(
            "revision must be a positive integer (1 .. 2**53 - 1)"
        )


def normalize_fact_date(value: Any) -> str:
    """Return a strict ``YYYY-MM-DD`` calendar date string."""
    if isinstance(value, datetime):
        value = value.date().isoformat()
    elif isinstance(value, _date):
        value = value.isoformat()
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise EntityValidationError("date must be an ISO date (YYYY-MM-DD)")
    try:
        _date.fromisoformat(value)
    except ValueError as exc:
        raise EntityValidationError(
            "date must be a valid calendar date (YYYY-MM-DD)"
        ) from exc
    return value


def normalize_changed_at(value: Any) -> str:
    """Return an aware UTC ISO timestamp with the ``+00:00`` suffix."""
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise EntityValidationError(
                "changed_at must be an ISO 8601 timestamp"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EntityValidationError(
            "changed_at must be a timezone-aware datetime or ISO string"
        )
    return value.astimezone(timezone.utc).isoformat()


def _clean_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntityValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(eq=True)
class ReceiptFactOverride(ReceiptIdentifierMixin, DynamoDBEntity):
    """One owner-stated fact row per receipt.

    Attributes:
        image_id: UUID of the image containing the receipt.
        receipt_id: ID of the receipt within the image.
        revision: Positive int, incremented by one on every update and
            checked by compare-and-swap writes. Never resets: retracting
            every fact keeps the row.
        date: ISO calendar date (``YYYY-MM-DD``) or None.
        date_reference: Why the owner knows ``date``; required exactly
            when ``date`` is stated.
        merchant_name: Merchant name or None.
        merchant_name_reference: Why the owner knows ``merchant_name``;
            required exactly when ``merchant_name`` is stated.
        source: Who stated the facts; only ``owner`` today.
        changed_at: Aware UTC ISO timestamp (``+00:00`` convention).
    """

    REQUIRED_KEYS: ClassVar[set[str]] = {
        "PK",
        "SK",
        "TYPE",
        "revision",
        "source",
        "changed_at",
    }

    image_id: str
    receipt_id: int
    revision: int = 1
    date: str | None = None
    date_reference: str | None = None
    merchant_name: str | None = None
    merchant_name_reference: str | None = None
    source: str = "owner"
    changed_at: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalise every field."""
        self._validate_receipt_identifiers()
        check_fact_revision(self.revision)
        if self.source not in FACT_OVERRIDE_SOURCES:
            raise EntityValidationError(
                f"source must be one of {sorted(FACT_OVERRIDE_SOURCES)}"
            )
        if self.date is not None:
            self.date = normalize_fact_date(self.date)
        if self.merchant_name is not None:
            self.merchant_name = _clean_text(
                self.merchant_name, "merchant_name"
            )
        for name in OVERRIDABLE_FACT_FIELDS:
            reference_name = fact_reference_field(name)
            reference = getattr(self, reference_name)
            if getattr(self, name) is None:
                if reference is not None:
                    raise EntityValidationError(
                        f"{reference_name} must be None when {name} is not "
                        "stated"
                    )
            else:
                setattr(
                    self,
                    reference_name,
                    _clean_text(reference, reference_name),
                )
        self.changed_at = normalize_changed_at(self.changed_at)

    @property
    def facts(self) -> dict[str, str]:
        """The stated (non-None) facts, in application order."""
        return {
            name: value
            for name in OVERRIDABLE_FACT_FIELDS
            if (value := getattr(self, name)) is not None
        }

    @property
    def references(self) -> dict[str, str]:
        """Provenance of each stated fact, keyed by fact name."""
        return {
            name: getattr(self, fact_reference_field(name))
            for name in self.facts
        }

    def with_fact(
        self,
        name: str,
        value: str | None,
        reference: str | None,
        *,
        revision: int,
        changed_at: str | None = None,
    ) -> "ReceiptFactOverride":
        """Return a copy stating (or retracting, when ``value`` is None)
        one fact at ``revision``; the other facts keep their provenance."""
        changes: dict[str, Any] = {
            "revision": revision,
            "changed_at": changed_at,
            name: value,
            fact_reference_field(name): (
                reference if value is not None else None
            ),
        }
        return replace(self, **changes)

    @property
    def key(self) -> dict[str, Any]:
        """Primary key for this override."""
        return receipt_fact_override_key(self.image_id, self.receipt_id)

    def to_item(self) -> dict[str, Any]:
        """Convert to DynamoDB item format (no GSI keys beyond TYPE)."""
        item: dict[str, Any] = {
            **self.key,
            "TYPE": {"S": FACT_OVERRIDE_TYPE},
            "revision": {"N": str(self.revision)},
            "source": {"S": self.source},
            "changed_at": {"S": self.changed_at},
        }
        for name in OVERRIDABLE_FACT_FIELDS:
            for attr in (name, fact_reference_field(name)):
                value = getattr(self, attr)
                item[attr] = (
                    {"S": value} if value is not None else {"NULL": True}
                )
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptFactOverride":
        """Create from a DynamoDB item."""
        missing = cls.validate_keys(item, cls.REQUIRED_KEYS)
        if missing:
            raise EntityValidationError(
                f"Missing required keys: {sorted(missing)}"
            )
        if item["TYPE"].get("S") != FACT_OVERRIDE_TYPE:
            raise EntityValidationError(f"TYPE must be {FACT_OVERRIDE_TYPE}")
        pk_parts = item["PK"].get("S", "").split("#")
        sk_parts = item["SK"].get("S", "").split("#")
        if len(pk_parts) != 2 or pk_parts[0] != "IMAGE":
            raise EntityValidationError("PK must match IMAGE#<image_id>")
        if (
            len(sk_parts) != 3
            or sk_parts[0] != "RECEIPT"
            or sk_parts[2] != FACT_OVERRIDE_SK_SUFFIX
        ):
            raise EntityValidationError(
                f"SK must match RECEIPT#<receipt_id>#{FACT_OVERRIDE_SK_SUFFIX}"
            )
        try:
            receipt_id = int(sk_parts[1])
            revision = int(item["revision"]["N"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EntityValidationError(
                "receipt_id and revision must be integers"
            ) from exc

        def optional_string(name: str) -> str | None:
            attr = item.get(name)
            if isinstance(attr, dict) and "S" in attr:
                return str(attr["S"])
            return None

        return cls(
            image_id=pk_parts[1],
            receipt_id=receipt_id,
            revision=revision,
            date=optional_string("date"),
            date_reference=optional_string("date_reference"),
            merchant_name=optional_string("merchant_name"),
            merchant_name_reference=optional_string("merchant_name_reference"),
            source=item["source"].get("S", ""),
            changed_at=item["changed_at"].get("S"),
        )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            "ReceiptFactOverride("
            f"image_id={_repr_str(self.image_id[:8] + '...')}, "
            f"receipt_id={self.receipt_id}, "
            f"revision={self.revision}, "
            f"date={_repr_str(self.date)}, "
            f"merchant_name={_repr_str(self.merchant_name)}"
            ")"
        )

    def __hash__(self) -> int:
        """Hash on the receipt identity (one override per receipt)."""
        return hash((self.image_id, self.receipt_id))


def item_to_receipt_fact_override(item: dict[str, Any]) -> ReceiptFactOverride:
    """Convert a DynamoDB item to a ReceiptFactOverride."""
    return ReceiptFactOverride.from_item(item)


def apply_fact_override(
    summary: ReceiptSummary, override: ReceiptFactOverride | None
) -> tuple[ReceiptSummary, list[str]]:
    """Let the owner's stated facts win over the extracted ones.

    Every stated fact of ``override`` replaces the corresponding summary
    field, in OVERRIDABLE_FACT_FIELDS order, so the result is the same
    on every recompute. Returns the (possibly new) summary and the names
    of the fields that were overridden. Every writer that recomputes a
    summary from labels must call this.
    """
    if override is None:
        return summary, []
    changes: dict[str, Any] = {}
    for name, value in override.facts.items():
        if name == "date":
            # The override stores a calendar date; summaries carry the
            # naive midnight datetime parse_date produces for labels.
            changes[name] = datetime.fromisoformat(value)
        else:
            changes[name] = value
    if not changes:
        return summary, []
    return replace(summary, **changes), list(changes)

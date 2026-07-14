"""Receipt-level merchant typeface fingerprint provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from receipt_dynamo.entities.util import assert_valid_uuid


@dataclass(eq=True)
class ReceiptTypefaceFingerprint:
    """Persisted output of the deterministic upload glyph matcher."""

    image_id: str
    receipt_id: int
    merchant_candidates: list[str]
    typeface: str | None
    confidence: float
    letter_count: int
    atlas_scores: dict[str, float]
    model_source: str
    created_at: datetime | str
    places_merchant_name: str | None = None
    places_agreement: str = "UNAVAILABLE"

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "merchant_candidates",
        "confidence",
        "letter_count",
        "atlas_scores",
        "model_source",
        "created_at",
        "places_agreement",
    }

    def __post_init__(self) -> None:
        assert_valid_uuid(self.image_id)
        if (
            isinstance(self.receipt_id, bool)
            or not isinstance(self.receipt_id, int)
            or self.receipt_id <= 0
        ):
            raise ValueError("receipt_id must be a positive integer")
        if not isinstance(self.merchant_candidates, list) or any(
            not isinstance(value, str) or not value
            for value in self.merchant_candidates
        ):
            raise ValueError("merchant_candidates must be non-empty strings")
        self.merchant_candidates = sorted(set(self.merchant_candidates))
        if self.typeface is not None and (
            not isinstance(self.typeface, str) or not self.typeface
        ):
            raise ValueError("typeface must be a non-empty string or None")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ValueError("confidence must be numeric")
        self.confidence = float(self.confidence)
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be finite and in [0, 1]")
        if (
            isinstance(self.letter_count, bool)
            or not isinstance(self.letter_count, int)
            or self.letter_count < 0
        ):
            raise ValueError("letter_count must be non-negative")
        if not isinstance(self.atlas_scores, dict) or any(
            not isinstance(name, str)
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not isfinite(float(score))
            or not 0 <= float(score) <= 1
            for name, score in self.atlas_scores.items()
        ):
            raise ValueError("atlas_scores must map names to scores in [0, 1]")
        self.atlas_scores = {
            name: float(score) for name, score in self.atlas_scores.items()
        }
        if not isinstance(self.model_source, str) or not self.model_source:
            raise ValueError("model_source must be a non-empty string")
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        elif not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime or ISO string")
        if self.places_merchant_name is not None and not isinstance(
            self.places_merchant_name, str
        ):
            raise ValueError("places_merchant_name must be a string or None")
        self.places_agreement = str(self.places_agreement).upper()
        if self.places_agreement not in {
            "MATCH",
            "DISAGREEMENT",
            "UNAVAILABLE",
        }:
            raise ValueError("places_agreement is invalid")

    @property
    def key(self) -> dict[str, Any]:
        """Return the receipt-scoped primary key."""

        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#TYPEFACE_FINGERPRINT"},
        }

    def to_item(self) -> dict[str, Any]:
        """Serialize the fingerprint to low-level DynamoDB JSON."""

        self.__post_init__()
        assert isinstance(self.created_at, datetime)
        item: dict[str, Any] = {
            **self.key,
            "TYPE": {"S": "RECEIPT_TYPEFACE_FINGERPRINT"},
            "merchant_candidates": {
                "L": [{"S": name} for name in self.merchant_candidates]
            },
            "confidence": {"N": str(self.confidence)},
            "letter_count": {"N": str(self.letter_count)},
            "atlas_scores": {
                "M": {
                    name: {"N": str(score)}
                    for name, score in self.atlas_scores.items()
                }
            },
            "model_source": {"S": self.model_source},
            "created_at": {"S": self.created_at.isoformat()},
            "places_agreement": {"S": self.places_agreement},
        }
        if self.typeface is not None:
            item["typeface"] = {"S": self.typeface}
        if self.places_merchant_name is not None:
            item["places_merchant_name"] = {"S": self.places_merchant_name}
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptTypefaceFingerprint":
        """Deserialize a low-level DynamoDB item."""

        missing = cls.REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"Item is missing required keys: {missing}")
        try:
            image_id = item["PK"]["S"].removeprefix("IMAGE#")
            sk = item["SK"]["S"].split("#")
            if (
                len(sk) != 3
                or sk[0] != "RECEIPT"
                or sk[2] != "TYPEFACE_FINGERPRINT"
            ):
                raise ValueError("invalid fingerprint sort key")
            return cls(
                image_id=image_id,
                receipt_id=int(sk[1]),
                merchant_candidates=[
                    value["S"] for value in item["merchant_candidates"]["L"]
                ],
                typeface=item.get("typeface", {}).get("S"),
                confidence=float(item["confidence"]["N"]),
                letter_count=int(item["letter_count"]["N"]),
                atlas_scores={
                    name: float(value["N"])
                    for name, value in item["atlas_scores"]["M"].items()
                },
                model_source=item["model_source"]["S"],
                created_at=item["created_at"]["S"],
                places_merchant_name=item.get("places_merchant_name", {}).get(
                    "S"
                ),
                places_agreement=item["places_agreement"]["S"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid ReceiptTypefaceFingerprint: {exc}"
            ) from exc


def item_to_receipt_typeface_fingerprint(
    item: dict[str, Any],
) -> ReceiptTypefaceFingerprint:
    """Convert a DynamoDB item to its fingerprint entity."""

    return ReceiptTypefaceFingerprint.from_item(item)


__all__ = [
    "ReceiptTypefaceFingerprint",
    "item_to_receipt_typeface_fingerprint",
]

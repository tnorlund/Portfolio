"""Receipt-level merchant typeface fingerprint provenance."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    typeface_candidates: list[str]
    typeface: str | None
    confidence: float
    confidence_basis: str
    calibration_id: str
    letter_count: int
    matched_letter_count: int
    distinct_character_count: int
    atlas_scores: dict[str, float]
    abstention_reason: str | None
    model_source: str
    created_at: datetime | str
    places_merchant_name: str | None = None
    places_agreement: str = "UNAVAILABLE"

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "merchant_candidates",
        "typeface_candidates",
        "confidence",
        "confidence_basis",
        "calibration_id",
        "letter_count",
        "matched_letter_count",
        "distinct_character_count",
        "atlas_scores",
        "model_source",
        "created_at",
        "places_agreement",
    }

    # Keep persisted cross-field invariants in one construction-time pass.
    def __post_init__(self) -> None:  # pylint: disable=too-many-statements
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
        if not isinstance(self.typeface_candidates, list) or any(
            not isinstance(value, str) or not value
            for value in self.typeface_candidates
        ):
            raise ValueError("typeface_candidates must be non-empty strings")
        self.typeface_candidates = sorted(set(self.typeface_candidates))
        if self.typeface is not None and (
            not isinstance(self.typeface, str) or not self.typeface
        ):
            raise ValueError("typeface must be a non-empty string or None")
        if (
            self.typeface is not None
            and self.typeface not in self.typeface_candidates
        ):
            raise ValueError("typeface must be present in typeface_candidates")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ValueError("confidence must be numeric")
        self.confidence = float(self.confidence)
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be finite and in [0, 1]")
        if (
            not isinstance(self.confidence_basis, str)
            or not self.confidence_basis
        ):
            raise ValueError("confidence_basis must be a non-empty string")
        if not isinstance(self.calibration_id, str) or not self.calibration_id:
            raise ValueError("calibration_id must be a non-empty string")
        if (
            isinstance(self.letter_count, bool)
            or not isinstance(self.letter_count, int)
            or self.letter_count < 0
        ):
            raise ValueError("letter_count must be non-negative")
        for name, value in (
            ("matched_letter_count", self.matched_letter_count),
            ("distinct_character_count", self.distinct_character_count),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{name} must be non-negative")
        if self.matched_letter_count > self.letter_count:
            raise ValueError("matched_letter_count cannot exceed letter_count")
        if self.distinct_character_count > self.matched_letter_count:
            raise ValueError(
                "distinct_character_count cannot exceed matched_letter_count"
            )
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
        if self.abstention_reason is not None and (
            not isinstance(self.abstention_reason, str)
            or not self.abstention_reason
        ):
            raise ValueError("abstention_reason must be a string or None")
        if self.typeface is None and self.abstention_reason is None:
            raise ValueError(
                "an abstention reason is required without a typeface"
            )
        if self.typeface is not None and self.abstention_reason is not None:
            raise ValueError(
                "an identified typeface cannot have an abstention reason"
            )
        if self.typeface is None and self.confidence != 0:
            raise ValueError("abstentions must have zero confidence")
        if not isinstance(self.model_source, str) or not self.model_source:
            raise ValueError("model_source must be a non-empty string")
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        elif not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime or ISO string")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must include a timezone")
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

        validated = replace(self)
        assert isinstance(validated.created_at, datetime)
        item: dict[str, Any] = {
            **validated.key,
            "TYPE": {"S": "RECEIPT_TYPEFACE_FINGERPRINT"},
            "merchant_candidates": {
                "L": [{"S": name} for name in validated.merchant_candidates]
            },
            "typeface_candidates": {
                "L": [{"S": name} for name in validated.typeface_candidates]
            },
            "confidence": {"N": str(validated.confidence)},
            "confidence_basis": {"S": validated.confidence_basis},
            "calibration_id": {"S": validated.calibration_id},
            "letter_count": {"N": str(validated.letter_count)},
            "matched_letter_count": {"N": str(validated.matched_letter_count)},
            "distinct_character_count": {
                "N": str(validated.distinct_character_count)
            },
            "atlas_scores": {
                "M": {
                    name: {"N": str(score)}
                    for name, score in sorted(validated.atlas_scores.items())
                }
            },
            "model_source": {"S": validated.model_source},
            "created_at": {"S": validated.created_at.isoformat()},
            "places_agreement": {"S": validated.places_agreement},
        }
        if validated.typeface is not None:
            item["typeface"] = {"S": validated.typeface}
        if validated.abstention_reason is not None:
            item["abstention_reason"] = {"S": validated.abstention_reason}
        if validated.places_merchant_name is not None:
            item["places_merchant_name"] = {
                "S": validated.places_merchant_name
            }
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptTypefaceFingerprint":
        """Deserialize a low-level DynamoDB item."""

        missing = cls.REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"Item is missing required keys: {missing}")
        try:
            if item["TYPE"].get("S") != "RECEIPT_TYPEFACE_FINGERPRINT":
                raise ValueError("invalid fingerprint TYPE")
            image_id = item["PK"]["S"].removeprefix("IMAGE#")
            if not item["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("invalid fingerprint partition key")
            sk = item["SK"]["S"].split("#")
            if (
                len(sk) != 3
                or sk[0] != "RECEIPT"
                or len(sk[1]) != 5
                or not sk[1].isdigit()
                or sk[2] != "TYPEFACE_FINGERPRINT"
            ):
                raise ValueError("invalid fingerprint sort key")
            return cls(
                image_id=image_id,
                receipt_id=int(sk[1]),
                merchant_candidates=[
                    value["S"] for value in item["merchant_candidates"]["L"]
                ],
                typeface_candidates=[
                    value["S"] for value in item["typeface_candidates"]["L"]
                ],
                typeface=item.get("typeface", {}).get("S"),
                confidence=float(item["confidence"]["N"]),
                confidence_basis=item["confidence_basis"]["S"],
                calibration_id=item["calibration_id"]["S"],
                letter_count=int(item["letter_count"]["N"]),
                matched_letter_count=int(item["matched_letter_count"]["N"]),
                distinct_character_count=int(
                    item["distinct_character_count"]["N"]
                ),
                atlas_scores={
                    name: float(value["N"])
                    for name, value in item["atlas_scores"]["M"].items()
                },
                abstention_reason=item.get("abstention_reason", {}).get("S"),
                model_source=item["model_source"]["S"],
                created_at=item["created_at"]["S"],
                places_merchant_name=item.get("places_merchant_name", {}).get(
                    "S"
                ),
                places_agreement=item["places_agreement"]["S"],
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
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

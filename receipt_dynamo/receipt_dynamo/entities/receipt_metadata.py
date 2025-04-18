from datetime import datetime
from typing import Any, Generator, Tuple

from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_point,
    assert_valid_uuid,
)


class ReceiptMetadata:
    """
    Represents validated metadata for a receipt, specifically merchant-related information
    derived from Google Places API and optionally validated by GPT.

    This entity is used to:
    - Anchor a receipt to a verified merchant (name, address, phone)
    - Support merchant-specific labeling strategies
    - Enable clustering and quality control across receipts

    Each ReceiptMetadata record is stored in DynamoDB using the image_id and receipt_id,
    and indexed by merchant name and match confidence via GSIs.

    Attributes:
        image_id (str): UUID of the image the receipt belongs to.
        receipt_id (int): Identifier of the receipt within the image.
        place_id (str): Google Places API ID of the matched business.
        merchant_name (str): Canonical name of the business (e.g., "Starbucks").
        merchant_category (str): Optional business type/category (e.g., "Coffee Shop").
        address (str): Normalized address returned from Google.
        phone_number (str): Formatted phone number.
        match_confidence (float): Confidence (0-1) from GPT or heuristics on the match quality.
        matched_fields (list[str]): List of fields that matched (e.g., ["name", "phone"]).
        validated_by (str): Source of validation (e.g., "GPT+GooglePlaces").
        timestamp (datetime): ISO timestamp when this record was created.
        reasoning (str): GPT or system-generated justification for the match.
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        place_id: str,
        merchant_name: str,
        merchant_category: str,
        address: str,
        phone_number: str,
        match_confidence: float,
        matched_fields: list[str],
        validated_by: str,
        timestamp: datetime,
        reasoning: str,
    ):
        if not isinstance(receipt_id, int):
            raise ValueError("receipt id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt id must be positive")
        self.receipt_id = receipt_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(place_id, str):
            raise ValueError("place id must be a string")
        self.place_id = place_id

        if not isinstance(merchant_name, str):
            raise ValueError("merchant name must be a string")
        self.merchant_name = merchant_name

        if not isinstance(merchant_category, str):
            raise ValueError("merchant category must be a string")
        self.merchant_category = merchant_category

        if not isinstance(address, str):
            raise ValueError("address must be a string")
        self.address = address

        if not isinstance(phone_number, str):
            raise ValueError("phone number must be a string")
        self.phone_number = phone_number

        if not isinstance(match_confidence, float):
            raise ValueError("match confidence must be a float")
        if match_confidence < 0 or match_confidence > 1:
            raise ValueError("match confidence must be between 0 and 1")
        self.match_confidence = match_confidence

        if not isinstance(matched_fields, list):
            raise ValueError("matched fields must be a list")
        for field in matched_fields:
            if not isinstance(field, str):
                raise ValueError("matched fields must be a list of strings")
        # Check that they are unique
        if len(matched_fields) != len(set(matched_fields)):
            raise ValueError("matched fields must be unique")
        self.matched_fields = matched_fields

        if not isinstance(validated_by, str):
            raise ValueError("validated by must be a string")
        self.validated_by = validated_by

        if not isinstance(timestamp, datetime):
            raise ValueError("timestamp must be a datetime")
        self.timestamp = timestamp

        if not isinstance(reasoning, str):
            raise ValueError("reasoning must be a string")
        self.reasoning = reasoning

    def key(self) -> dict:
        """Returns the primary key used to store this record in DynamoDB."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#METADATA"},
        }

    def gsi1_key(self) -> dict:
        """
        Returns the key for GSI1: used to index all receipts associated with a given merchant.
        Normalizes merchant_name by uppercasing and replacing spaces with underscores.
        """
        normalized_merchant_name = self.merchant_name.upper().replace(" ", "_")
        return {
            "GSI1PK": {"S": f"MERCHANT#{normalized_merchant_name}"},
            "GSI1SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#METADATA"
            },
        }

    def gsi2_key(self) -> dict:
        """
        Returns the key for GSI2: used to sort ReceiptMetadata entries by confidence score.
        Supports filtering low/high-confidence merchant matches across receipts.
        """
        formatted_match_confidence = f"{self.match_confidence:.2f}"
        return {
            "GSI2PK": {"S": f"MERCHANT_VALIDATION"},
            "GSI2SK": {"S": f"CONFIDENCE#{formatted_match_confidence}"},
        }

    def to_item(self) -> dict:
        """
        Serializes the ReceiptMetadata object into a DynamoDB-compatible item.
        Includes primary key and both GSI keys, as well as all merchant-related metadata.
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_METADATA"},
            "place_id": {"S": self.place_id},
            "merchant_name": {"S": self.merchant_name},
            "merchant_category": {"S": self.merchant_category},
            "address": {"S": self.address},
            "phone_number": {"S": self.phone_number},
            "match_confidence": {"N": str(self.match_confidence)},
            "matched_fields": {"SS": self.matched_fields},
            "validated_by": {"S": self.validated_by},
            "timestamp": {"S": self.timestamp.isoformat()},
            "reasoning": {"S": self.reasoning},
        }


def itemToReceiptMetadata(item: dict) -> ReceiptMetadata:
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "place_id",
        "merchant_name",
        "merchant_category",
        "address",
        "phone_number",
        "match_confidence",
        "matched_fields",
        "validated_by",
        "timestamp",
        "reasoning",
    }

    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = int(item["SK"]["S"].split("#")[1])
        place_id = item["place_id"]["S"]
        merchant_name = item["merchant_name"]["S"]
        merchant_category = item["merchant_category"]["S"]
        address = item["address"]["S"]
        phone_number = item["phone_number"]["S"]
        match_confidence = float(item["match_confidence"]["N"])
        matched_fields = item["matched_fields"]["SS"]
        validated_by = item["validated_by"]["S"]
        timestamp = datetime.fromisoformat(item["timestamp"]["S"])
        reasoning = item["reasoning"]["S"]
        return ReceiptMetadata(
            image_id=image_id,
            receipt_id=receipt_id,
            place_id=place_id,
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            address=address,
            phone_number=phone_number,
            match_confidence=match_confidence,
            matched_fields=matched_fields,
            validated_by=validated_by,
            timestamp=timestamp,
            reasoning=reasoning,
        )
    except Exception as e:
        raise ValueError(f"Error parsing receipt metadata: {e}")

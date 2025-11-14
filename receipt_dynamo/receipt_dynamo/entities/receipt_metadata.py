import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, List, Tuple

from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
    validate_positive_int,
)

# Validation thresholds
MIN_PHONE_DIGITS = (
    7  # Minimum digits for valid phone number (tolerates missing area code)
)
MIN_NAME_LENGTH = 2  # Minimum length for meaningful merchant name
MIN_ADDRESS_TOKENS = 3  # Minimum meaningful tokens for valid address


@dataclass(eq=True, unsafe_hash=False)
class ReceiptMetadata(SerializationMixin):
    """
    Represents validated metadata for a receipt, specifically merchant-related
    information derived from Google Places API and optionally validated by GPT.

    This entity is used to:
    - Anchor a receipt to a verified merchant (name, address, phone)
    - Support merchant-specific labeling strategies
    - Enable clustering and quality control across receipts

    Each ReceiptMetadata record is stored in DynamoDB using the image_id and
    receipt_id, and indexed by merchant name via GSIs.

    Attributes:
        image_id (str): UUID of the image the receipt belongs to.
        receipt_id (int): Identifier of the receipt within the image.
        place_id (str): Google Places API ID of the matched business.
        merchant_name (str): Canonical name of the business
            (e.g., "Starbucks").
        merchant_category (str): Optional business type/category
            (e.g., "Coffee Shop").
        address (str): Normalized address returned from Google.
        phone_number (str): Formatted phone number.
        matched_fields (list[str]): List of fields that matched
            (e.g., ["name", "phone"]).
        validated_by (str): Source of validation (e.g., "GPT+GooglePlaces").
        timestamp (datetime): ISO timestamp when this record was created.
        reasoning (str): GPT or system-generated justification for the match.
        canonical_place_id (str): Canonical place ID from the most
            representative business in the cluster.
        canonical_merchant_name (str): Canonical merchant name from the most
            representative business in the cluster.
        canonical_address (str): Normalized canonical address from the most
            representative business in the cluster.
        canonical_phone_number (str): Canonical phone number from the most
            representative business in the cluster.
    """

    image_id: str
    receipt_id: int
    place_id: str
    merchant_name: str
    matched_fields: List[str]
    timestamp: datetime
    merchant_category: str = ""
    address: str = ""
    phone_number: str = ""
    validated_by: str = ""
    reasoning: str = ""
    canonical_place_id: str = ""
    canonical_merchant_name: str = ""
    canonical_address: str = ""
    canonical_phone_number: str = ""
    validation_status: str = ""

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        validate_positive_int("receipt_id", self.receipt_id)

        assert_valid_uuid(self.image_id)

        if not isinstance(self.place_id, str):
            raise ValueError("place id must be a string")

        if not isinstance(self.merchant_name, str):
            raise ValueError("merchant name must be a string")

        if not isinstance(self.merchant_category, str):
            raise ValueError("merchant category must be a string")

        if not isinstance(self.address, str):
            raise ValueError("address must be a string")

        if not isinstance(self.phone_number, str):
            raise ValueError("phone number must be a string")

        if not isinstance(self.matched_fields, list):
            raise ValueError("matched fields must be a list")
        for field in self.matched_fields:
            if not isinstance(field, str):
                raise ValueError("matched fields must be a list of strings")
        # Check that they are unique
        if len(self.matched_fields) != len(set(self.matched_fields)):
            raise ValueError("matched fields must be unique")

        self.validated_by = normalize_enum(self.validated_by, ValidationMethod)

        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp must be a datetime")

        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")

        # Initialize canonical fields
        if not isinstance(self.canonical_place_id, str):
            raise ValueError("canonical place id must be a string")

        if not isinstance(self.canonical_merchant_name, str):
            raise ValueError("canonical merchant name must be a string")

        if not isinstance(self.canonical_address, str):
            raise ValueError("canonical address must be a string")

        if not isinstance(self.canonical_phone_number, str):
            raise ValueError("canonical phone number must be a string")

        # Validate field quality before determining validation status
        high_quality_fields = self._get_high_quality_matched_fields()
        num_fields = len(high_quality_fields)

        # Use configurable thresholds from environment variables
        min_fields_for_match = int(os.environ.get("MIN_FIELDS_FOR_MATCH", 2))
        min_fields_for_unsure = int(os.environ.get("MIN_FIELDS_FOR_UNSURE", 1))

        if num_fields >= min_fields_for_match:
            self.validation_status = MerchantValidationStatus.MATCHED.value
        elif num_fields >= min_fields_for_unsure:
            self.validation_status = MerchantValidationStatus.UNSURE.value
        else:
            self.validation_status = MerchantValidationStatus.NO_MATCH.value

    def _get_high_quality_matched_fields(self) -> List[str]:
        """
        Validates the quality of matched fields and returns only
        high-quality matches.

        This method filters out potentially false positive field matches by
        checking:
        - Name fields are not empty and have meaningful content
        - Phone fields have sufficient digits
        - Address fields have sufficient components

        Returns:
            list[str]: List of high-quality matched field names
        """
        high_quality_fields = []

        for field in self.matched_fields:
            if field == "name":
                # Name must be non-empty and more than just
                # whitespace/punctuation
                if (
                    self.merchant_name
                    and len(self.merchant_name.strip()) > MIN_NAME_LENGTH
                ):
                    high_quality_fields.append(field)
            elif field == "phone":
                # Phone must have at least 7 digits (tolerate missing area
                # code)
                phone_digits = "".join(
                    c for c in self.phone_number if c.isdigit()
                )
                if len(phone_digits) >= MIN_PHONE_DIGITS:
                    high_quality_fields.append(field)
            elif field == "address":
                # Address must have at least 2 meaningful components
                tokens = self.address.split()
                meaningful_tokens: float = 0.0
                has_number = False

                for i, token in enumerate(tokens):
                    token_clean = token.rstrip(".,;:")

                    # Count as meaningful if:
                    # 1. It's a number (house/street number)
                    if token_clean.replace("-", "").replace("/", "").isdigit():
                        meaningful_tokens += 1
                        has_number = True
                    # 2. It contains digits (1st, 2nd, etc)
                    elif any(c.isdigit() for c in token_clean):
                        meaningful_tokens += 1
                    # 3. It's a word with 3+ letters
                    elif (
                        len(token_clean) >= MIN_ADDRESS_TOKENS
                        and token_clean.isalpha()
                    ):
                        meaningful_tokens += 1
                    # 4. It's a short token (likely abbreviation) but not the
                    #    only token
                    elif len(tokens) > 1 and token_clean.isalpha():
                        meaningful_tokens += 0.5  # Count as half

                # Consider valid if there are at least two meaningful tokens,
                # a single descriptive token, or a street number with other
                # components (e.g., "123 Main").
                if (
                    meaningful_tokens >= 2
                    or (
                        len(tokens) == 1
                        and meaningful_tokens >= 1
                        and not has_number
                    )
                    or (has_number and len(tokens) > 1)
                ):
                    high_quality_fields.append(field)
            else:
                # Unknown fields are kept as-is (future-proofing)
                high_quality_fields.append(field)

        return high_quality_fields

    @property
    def key(self) -> Dict[str, Any]:
        """Returns the primary key used to store this record in DynamoDB."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#METADATA"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """
        Returns the key for GSI1: used to index all receipts associated with a
        given merchant.

        Uses ``canonical_merchant_name`` if available (preferred), otherwise
        falls back to ``merchant_name``. The merchant name is normalized by
        uppercasing and replacing spaces with underscores.

        This enables efficient querying of all receipts for a canonical
        merchant, regardless of the original merchant name variations.
        """
        # Prioritize canonical_merchant_name if it exists, otherwise use
        # merchant_name
        merchant_name_to_use = (
            self.canonical_merchant_name
            if self.canonical_merchant_name
            else self.merchant_name
        )
        normalized_merchant_name = merchant_name_to_use.upper().replace(
            " ", "_"
        )

        return {
            "GSI1PK": {"S": f"MERCHANT#{normalized_merchant_name}"},
            "GSI1SK": {
                "S": (
                    f"IMAGE#{self.image_id}#RECEIPT"
                    f"#{self.receipt_id:05d}#METADATA"
                )
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """
        Returns the key for GSI2: used to query records by ``place_id``. This
        index supports the incremental consolidation process by enabling
        efficient lookup of records with the same ``place_id``.

        Only includes non-empty ``place_id`` values to avoid cluttering the
        index.
        """
        if not self.place_id:
            return {}

        return {
            "GSI2PK": {"S": f"PLACE#{self.place_id}"},
            "GSI2SK": {
                "S": (
                    f"IMAGE#{self.image_id}#RECEIPT"
                    f"#{self.receipt_id:05d}#METADATA"
                )
            },
        }

    def gsi3_key(self) -> Dict[str, Any]:
        """
        Returns the key for GSI3: used to sort ``ReceiptMetadata`` entries by
        validation status. Supports filtering low/high-confidence merchant
        matches across receipts.
        """
        return {
            "GSI3PK": {"S": f"MERCHANT_VALIDATION"},
            "GSI3SK": {"S": f"STATUS#{self.validation_status}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Serializes the ``ReceiptMetadata`` object into a DynamoDB-compatible
        item. Includes primary key and GSI keys, as well as all
        merchant-related metadata.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "RECEIPT_METADATA"},
            "place_id": {"S": self.place_id},
            # Required fields (always present)
            "merchant_name": {"S": self.merchant_name},
            "validation_status": {"S": self.validation_status},
            "timestamp": {"S": self.timestamp.isoformat()},
        }

        # Add GSI2 keys if place_id exists
        gsi2_keys = self.gsi2_key()
        if gsi2_keys:
            item.update(gsi2_keys)

        # Optional string fields: only include if non-empty, else mark as NULL
        for attr in (
            "merchant_category",
            "address",
            "phone_number",
            "validated_by",
            "reasoning",
            "canonical_place_id",
            "canonical_merchant_name",
            "canonical_address",
            "canonical_phone_number",
        ):
            value = getattr(self, attr)
            if isinstance(value, str):
                if value:
                    item[attr] = {"S": value}
                else:
                    item[attr] = {"NULL": True}

        # matched_fields: only include non-empty list
        if self.matched_fields:
            item["matched_fields"] = {"SS": self.matched_fields}

        return item

    def __repr__(self) -> str:
        """
        Returns a string representation of the ReceiptMetadata object.

        Returns:
            str: A string representation of the ReceiptMetadata object.
        """
        return (
            f"ReceiptMetadata("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"place_id={_repr_str(self.place_id)}, "
            f"merchant_name={_repr_str(self.merchant_name)}, "
            f"merchant_category={_repr_str(self.merchant_category)}, "
            f"address={_repr_str(self.address)}, "
            f"phone_number={_repr_str(self.phone_number)}, "
            f"matched_fields={self.matched_fields}, "
            f"validated_by={_repr_str(self.validated_by)}, "
            f"timestamp={_repr_str(self.timestamp)}, "
            f"reasoning={_repr_str(self.reasoning)}, "
            f"validation_status={_repr_str(self.validation_status)}, "
            f"canonical_place_id={_repr_str(self.canonical_place_id)}, "
            f"canonical_merchant_name="
            f"{_repr_str(self.canonical_merchant_name)}, "
            f"canonical_address={_repr_str(self.canonical_address)}, "
            f"canonical_phone_number="
            f"{_repr_str(self.canonical_phone_number)}"
            f")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """
        Returns an iterator over the ReceiptMetadata object's attributes.

        Yields:
            Tuple[str, Any]:
                A tuple containing the attribute name and its value.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "place_id", self.place_id
        yield "merchant_name", self.merchant_name
        yield "merchant_category", self.merchant_category
        yield "address", self.address
        yield "phone_number", self.phone_number
        yield "matched_fields", self.matched_fields
        yield "validated_by", self.validated_by
        yield "timestamp", self.timestamp.isoformat()
        yield "reasoning", self.reasoning
        yield "validation_status", self.validation_status
        yield "canonical_place_id", self.canonical_place_id
        yield "canonical_merchant_name", self.canonical_merchant_name
        yield "canonical_address", self.canonical_address
        yield "canonical_phone_number", self.canonical_phone_number

    def __hash__(self) -> int:
        """
        Returns a hash value for the ReceiptMetadata object.

        Returns:
            int: The hash value for the ReceiptMetadata object.
        """
        return hash(
            (
                self.image_id,
                self.receipt_id,
                self.place_id,
                self.merchant_name,
                self.merchant_category,
                self.address,
                self.phone_number,
                self.matched_fields,
                self.validated_by,
                self.timestamp,
                self.reasoning,
                self.validation_status,
                self.canonical_place_id,
                self.canonical_merchant_name,
                self.canonical_address,
                self.canonical_phone_number,
            )
        )


def item_to_receipt_metadata(item: Dict[str, Any]) -> ReceiptMetadata:
    """Create ReceiptMetadata from DynamoDB item using EntityFactory."""
    from receipt_dynamo.entities.entity_factory import (
        EntityFactory,
        create_image_receipt_pk_parser,
        create_image_receipt_sk_parser,
    )

    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "place_id",
        "merchant_name",
        "timestamp",
    }

    # Type-safe extractors
    custom_extractors = {
        "place_id": EntityFactory.extract_string_field("place_id", ""),  # Default to empty string if missing/NULL
        "merchant_name": EntityFactory.extract_string_field("merchant_name", ""),  # Default to empty string if missing/NULL
        "matched_fields": EntityFactory.extract_string_list_field(
            "matched_fields"
        ),
        "timestamp": EntityFactory.extract_datetime_field("timestamp"),
        "merchant_category": EntityFactory.extract_string_field(
            "merchant_category", ""
        ),
        "address": EntityFactory.extract_string_field("address", ""),
        "phone_number": EntityFactory.extract_string_field("phone_number", ""),
        "validated_by": EntityFactory.extract_string_field("validated_by", ""),
        "reasoning": EntityFactory.extract_string_field("reasoning", ""),
        "canonical_place_id": EntityFactory.extract_string_field(
            "canonical_place_id", ""
        ),
        "canonical_merchant_name": EntityFactory.extract_string_field(
            "canonical_merchant_name", ""
        ),
        "canonical_address": EntityFactory.extract_string_field(
            "canonical_address", ""
        ),
        "canonical_phone_number": EntityFactory.extract_string_field(
            "canonical_phone_number", ""
        ),
    }

    return EntityFactory.create_entity(
        entity_class=ReceiptMetadata,
        item=item,
        required_keys=required_keys,
        key_parsers={
            "PK": create_image_receipt_pk_parser(),
            "SK": create_image_receipt_sk_parser(),
        },
        custom_extractors=custom_extractors,
    )

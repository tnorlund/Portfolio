"""ReceiptCoreLabelDefinition entity for storing CORE_LABEL definitions in DynamoDB."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import _repr_str


@dataclass(eq=True, unsafe_hash=False)
class ReceiptCoreLabelDefinition(DynamoDBEntity):
    """
    Stores CORE_LABEL definitions in DynamoDB for reference.

    This provides a single source of truth for label definitions that can be
    queried and updated without code changes.

    Attributes:
        label_type (str): The label type (e.g., "GRAND_TOTAL").
        description (str): Description of what this label represents.
        category (str): Category grouping (e.g., "totals_taxes", "transaction_info").
        created_at (str): ISO formatted timestamp when the definition was created.
        updated_at (str): ISO formatted timestamp when the definition was last updated.
    """

    label_type: str
    description: str
    category: str
    created_at: datetime | str
    updated_at: datetime | str

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        if not isinstance(self.label_type, str):
            raise ValueError("label_type must be a string")
        if not self.label_type:
            raise ValueError("label_type cannot be empty")
        self.label_type = self.label_type.upper()

        if not isinstance(self.description, str):
            raise ValueError("description must be a string")
        if not self.description:
            raise ValueError("description cannot be empty")

        if not isinstance(self.category, str):
            raise ValueError("category must be a string")
        if not self.category:
            raise ValueError("category cannot be empty")

        # Convert datetime to string for storage
        if isinstance(self.created_at, datetime):
            self.created_at = self.created_at.isoformat()
        elif isinstance(self.created_at, str):
            try:
                datetime.fromisoformat(self.created_at)
            except ValueError as e:
                raise ValueError(
                    "created_at string must be in ISO format"
                ) from e
        else:
            raise ValueError(
                "created_at must be a datetime object or a string"
            )

        if isinstance(self.updated_at, datetime):
            self.updated_at = self.updated_at.isoformat()
        elif isinstance(self.updated_at, str):
            try:
                datetime.fromisoformat(self.updated_at)
            except ValueError as e:
                raise ValueError(
                    "updated_at string must be in ISO format"
                ) from e
        else:
            raise ValueError(
                "updated_at must be a datetime object or a string"
            )

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the CORE_LABEL definition.

        Returns:
            dict: The primary key for the definition.
        """
        return {
            "PK": {"S": "CORE_LABEL_DEFINITION"},
            "SK": {"S": f"LABEL#{self.label_type}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generate the GSI1 key for querying by category.

        Returns:
            dict: GSI1 key for querying by category.
        """
        return {
            "GSI1PK": {"S": f"CATEGORY#{self.category}"},
            "GSI1SK": {"S": f"LABEL#{self.label_type}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptCoreLabelDefinition object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the object as a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_CORE_LABEL_DEFINITION"},
            "label_type": {"S": self.label_type},
            "description": {"S": self.description},
            "category": {"S": self.category},
            "created_at": {"S": self.created_at},
            "updated_at": {"S": self.updated_at},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the object."""
        return (
            "ReceiptCoreLabelDefinition("
            f"label_type={_repr_str(self.label_type)}, "
            f"description={_repr_str(self.description[:50])}..., "
            f"category={_repr_str(self.category)}, "
            f"created_at={_repr_str(self.created_at)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the object's attributes."""
        yield "label_type", self.label_type
        yield "description", self.description
        yield "category", self.category
        yield "created_at", self.created_at
        yield "updated_at", self.updated_at


def item_to_receipt_core_label_definition(
    item: Dict[str, Any],
) -> ReceiptCoreLabelDefinition:
    """Converts a DynamoDB item to a ReceiptCoreLabelDefinition object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptCoreLabelDefinition: The object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {"PK", "SK", "label_type", "description", "category"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Invalid item format - missing keys: {missing_keys}")

    try:
        label_type = item["label_type"]["S"]
        description = item["description"]["S"]
        category = item["category"]["S"]
        created_at = item.get("created_at", {}).get("S", "")
        updated_at = item.get("updated_at", {}).get("S", "")

        return ReceiptCoreLabelDefinition(
            label_type=label_type,
            description=description,
            category=category,
            created_at=created_at,
            updated_at=updated_at,
        )
    except Exception as e:
        raise ValueError(
            f"Error converting item to ReceiptCoreLabelDefinition: {e}"
        ) from e


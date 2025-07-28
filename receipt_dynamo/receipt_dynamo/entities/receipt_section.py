from datetime import datetime
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.constants import SectionType
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
)


class ReceiptSection:
    """
    Represents a classified section of a receipt stored in DynamoDB.

    This entity tracks sections identified by machine learning models,
    including header, body, footer, and other semantically meaningful
    segments of a receipt.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the section
            belongs.
        section_type (str): The type of section (e.g., "HEADER", "FOOTER",
            "LINE_ITEMS", etc.)
        line_ids (list[int]): The line IDs in this section.
        confidence (float): The model's confidence in this section
            classification.
        embedding_status (EmbeddingStatus): The status of the embedding for
            this section.
        created_at (datetime): Timestamp when this section was created.
        model_source (str): The model or pipeline that identified this section.
    """

    def __init__(
        self,
        receipt_id: int,
        image_id: str,
        section_type: str | SectionType,
        line_ids: list[int],
        created_at: datetime | str,
    ):
        """Initialize a ReceiptSection instance."""
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id: int = receipt_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        # Normalize and validate section_type (allow enum or string)
        if isinstance(section_type, SectionType):
            section_type_value = section_type.value
        elif isinstance(section_type, str):
            section_type_value = section_type
        else:
            raise ValueError(
                "section_type must be a string or SectionType enum"
            )
        valid_section_types = [t.value for t in SectionType]
        if section_type_value not in valid_section_types:
            raise ValueError(
                f"section_type must be one of: "
                f"{', '.join(valid_section_types)}\nGot: {section_type_value}"
            )
        self.section_type = section_type_value

        if not isinstance(line_ids, list):
            raise ValueError("line_ids must be a list")
        if not line_ids:
            raise ValueError("line_ids must not be empty")
        if not all(isinstance(line_id, int) for line_id in line_ids):
            raise ValueError("line_ids must contain only integers")
        self.line_ids: list[int] = line_ids

        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif isinstance(created_at, datetime):
            created_at = created_at
        else:
            raise ValueError("created_at must be a datetime or ISO string")
        self.created_at = created_at

    @property
    def key(self) -> Dict[str, Any]:
        """Generate the primary key for the receipt section."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"SECTION#{self.section_type}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Convert the ReceiptSection to a DynamoDB item."""
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_SECTION"},
            "section_type": {"S": self.section_type},
            "line_ids": {
                "L": [{"N": str(line_id)} for line_id in self.line_ids]
            },
            "created_at": {"S": self.created_at.isoformat()},
        }

    def __eq__(self, other: object) -> bool:
        """Determine whether two ReceiptSection objects are equal."""
        if not isinstance(other, ReceiptSection):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
            and self.section_type == other.section_type
            and self.line_ids == other.line_ids
            and self.created_at == other.created_at
        )

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptSection object."""
        return (
            f"ReceiptSection("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"section_type='{self.section_type}', "
            f"line_ids={self.line_ids}, "
            f"created_at={_repr_str(self.created_at.isoformat())}"
            f")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Iterate over the attributes of the ReceiptSection."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id

        yield "section_type", self.section_type
        yield "line_ids", self.line_ids
        yield "created_at", self.created_at.isoformat()

    def __hash__(self) -> int:
        """Return a hash of the ReceiptSection."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.section_type,
                self.line_ids,
                self.created_at.isoformat(),
            )
        )


def item_to_receipt_section(item: Dict[str, Any]) -> ReceiptSection:
    """
    Convert a DynamoDB item to a ReceiptSection object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptSection: The converted ReceiptSection object.

    Raises:
        ValueError: If the item is not a valid ReceiptSection.
    """
    required_keys = {
        "PK",
        "SK",
        "section_type",
        "line_ids",
        "created_at",
    }

    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")

    try:
        image_id = item["PK"]["S"].split("#")[1]
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[1])

        # Extract other attributes
        section_type = item["section_type"]["S"]
        line_ids = [int(li["N"]) for li in item["line_ids"]["L"]]
        created_at = datetime.fromisoformat(item["created_at"]["S"])

        return ReceiptSection(
            receipt_id=receipt_id,
            image_id=image_id,
            section_type=section_type,
            line_ids=line_ids,
            created_at=created_at,
        )
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"Error converting item to ReceiptSection: {e}") from e

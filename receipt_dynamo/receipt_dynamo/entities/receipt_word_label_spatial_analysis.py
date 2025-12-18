from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class SpatialRelationship:
    """
    Represents a spatial relationship between two receipt word labels.

    Attributes:
        to_label (str): The target label type (e.g., "TAX", "SUBTOTAL").
        to_line_id (int): Line identifier of the target word.
        to_word_id (int): Word identifier of the target word.
        distance (float): Euclidean distance between the word centroids.
        angle (float): Angle in radians from this word to the target word.
    """

    to_label: str
    to_line_id: int
    to_word_id: int
    distance: float
    angle: float

    def __post_init__(self) -> None:
        """Validate initialization arguments."""
        if not isinstance(self.to_label, str) or not self.to_label:
            raise ValueError("to_label must be a non-empty string")
        self.to_label = self.to_label.upper()  # Store labels in uppercase

        validate_positive_int("to_line_id", self.to_line_id)
        validate_positive_int("to_word_id", self.to_word_id)

        if not isinstance(self.distance, (int, float)) or self.distance < 0:
            raise ValueError("distance must be a non-negative number")

        if not isinstance(self.angle, (int, float)):
            raise ValueError("angle must be a number")


@dataclass(eq=True, unsafe_hash=False)
class ReceiptWordLabelSpatialAnalysis:
    """
    Represents spatial analysis results for a receipt word label stored in DynamoDB.

    This entity stores Metric 2 (Inter-Label Spatial Relationships) from the
    comprehensive spatial analysis system. For each valid receipt word label,
    it captures the spatial relationships to all other valid labels on the same receipt.

    The spatial relationships include distance and angle measurements that can be used
    for validating new labels by checking if they follow expected spatial patterns
    relative to existing validated labels.

    GSI Design:
    - GSI1: Cross-receipt label analysis (SPATIAL_ANALYSIS#<label>)
    - GSI2: Receipt-based validation (IMAGE#<image_id>#RECEIPT#<receipt_id>#SPATIAL)

    Attributes:
        image_id (str): UUID identifying the associated image.
        receipt_id (int): Number identifying the receipt.
        line_id (int): Number identifying the line containing the word.
        word_id (int): Number identifying the word.
        from_label (str): The label assigned to this word.
        from_position (Dict[str, float]): Position of this word (x, y coordinates).
        spatial_relationships (List[SpatialRelationship]): All relationships to other valid labels.
        timestamp_added (str): ISO formatted timestamp when analysis was computed.
        analysis_version (str): Version of the spatial analysis algorithm used.
    """

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    from_label: str
    from_position: Dict[str, float]
    spatial_relationships: List[SpatialRelationship]
    timestamp_added: datetime | str
    analysis_version: str = "1.0"

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)

        validate_positive_int("receipt_id", self.receipt_id)
        validate_positive_int("line_id", self.line_id)
        validate_positive_int("word_id", self.word_id)

        if not isinstance(self.from_label, str) or not self.from_label:
            raise ValueError("from_label must be a non-empty string")
        self.from_label = self.from_label.upper()  # Store labels in uppercase

        if not isinstance(self.from_position, dict):
            raise ValueError("from_position must be a dictionary")
        required_position_keys = {"x", "y"}
        if not required_position_keys.issubset(self.from_position.keys()):
            raise ValueError("from_position must contain 'x' and 'y' keys")

        if not isinstance(self.spatial_relationships, list):
            raise ValueError("spatial_relationships must be a list")

        # Validate each spatial relationship
        for i, rel in enumerate(self.spatial_relationships):
            if not isinstance(rel, SpatialRelationship):
                raise ValueError(
                    f"spatial_relationships[{i}] must be a SpatialRelationship"
                )

        # Convert datetime to string for storage
        if isinstance(self.timestamp_added, datetime):
            self.timestamp_added = self.timestamp_added.isoformat()
        elif isinstance(self.timestamp_added, str):
            # Validate it's a valid ISO format by trying to parse it
            try:
                datetime.fromisoformat(self.timestamp_added)
            except ValueError as e:
                raise ValueError("timestamp_added string must be in ISO format") from e
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")

        if not isinstance(self.analysis_version, str) or not self.analysis_version:
            raise ValueError("analysis_version must be a non-empty string")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the receipt word label spatial analysis.

        Returns:
            dict: The primary key for the spatial analysis.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}#SPATIAL_ANALYSIS"
                )
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generate the GSI1 key for finding spatial analyses by label type.

        GSI1PK: SPATIAL_ANALYSIS#<label>
        GSI1SK: IMAGE#<image_id>#RECEIPT#<receipt_id>#TIMESTAMP#<timestamp>
        """
        return {
            "GSI1PK": {"S": f"SPATIAL_ANALYSIS#{self.from_label}"},
            "GSI1SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#TIMESTAMP#{self.timestamp_added}"
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """Generate the GSI2 key for finding all spatial analyses for a receipt.

        GSI2PK: IMAGE#<image_id>#RECEIPT#<receipt_id>#SPATIAL
        GSI2SK: LABEL#<label>#LINE#<line_id>#WORD#<word_id>
        """
        return {
            "GSI2PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#SPATIAL"
            },
            "GSI2SK": {
                "S": f"LABEL#{self.from_label}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptWordLabelSpatialAnalysis object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the object as a DynamoDB item.
        """
        # Convert spatial relationships to DynamoDB format
        relationships_list = []
        for rel in self.spatial_relationships:
            relationships_list.append(
                {
                    "M": {
                        "to_label": {"S": rel.to_label},
                        "to_line_id": {"N": str(rel.to_line_id)},
                        "to_word_id": {"N": str(rel.to_word_id)},
                        "distance": {"N": str(rel.distance)},
                        "angle": {"N": str(rel.angle)},
                    }
                }
            )

        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_WORD_LABEL_SPATIAL_ANALYSIS"},
            "from_label": {"S": self.from_label},
            "from_position": {
                "M": {
                    "x": {"N": str(self.from_position["x"])},
                    "y": {"N": str(self.from_position["y"])},
                }
            },
            "spatial_relationships": {"L": relationships_list},
            "relationships_count": {"N": str(len(self.spatial_relationships))},
            "timestamp_added": {"S": self.timestamp_added},
            "analysis_version": {"S": self.analysis_version},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the object."""
        return (
            "ReceiptWordLabelSpatialAnalysis("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"from_label={_repr_str(self.from_label)}, "
            f"relationships_count={len(self.spatial_relationships)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}, "
            f"analysis_version={_repr_str(self.analysis_version)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the object's attributes."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "from_label", self.from_label
        yield "from_position", self.from_position
        yield "spatial_relationships", self.spatial_relationships
        yield "timestamp_added", self.timestamp_added
        yield "analysis_version", self.analysis_version

    def __eq__(self, other) -> bool:
        """Determines whether two objects are equal."""
        if not isinstance(other, ReceiptWordLabelSpatialAnalysis):
            return False
        return (
            self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.from_label == other.from_label
            and self.from_position == other.from_position
            and self.spatial_relationships == other.spatial_relationships
            and self.timestamp_added == other.timestamp_added
            and self.analysis_version == other.analysis_version
        )

    def __hash__(self) -> int:
        """Returns the hash value of the object."""
        # Convert spatial_relationships to tuple for hashing
        relationships_tuple = tuple(
            (
                rel.to_label,
                rel.to_line_id,
                rel.to_word_id,
                rel.distance,
                rel.angle,
            )
            for rel in self.spatial_relationships
        )
        position_tuple = (self.from_position["x"], self.from_position["y"])

        return hash(
            (
                self.image_id,
                self.receipt_id,
                self.line_id,
                self.word_id,
                self.from_label,
                position_tuple,
                relationships_tuple,
                self.timestamp_added,
                self.analysis_version,
            )
        )


def item_to_receipt_word_label_spatial_analysis(
    item: Dict[str, Any],
) -> ReceiptWordLabelSpatialAnalysis:
    """Converts a DynamoDB item to a ReceiptWordLabelSpatialAnalysis object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWordLabelSpatialAnalysis: The spatial analysis object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "from_label",
        "from_position",
        "spatial_relationships",
        "timestamp_added",
        "analysis_version",
    }

    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Invalid item format. Missing keys: {missing_keys}")

    # Parse the SK to extract identifiers
    sk = item["SK"]["S"]
    parts = sk.split("#")
    if (
        len(parts) != 7
        or parts[0] != "RECEIPT"
        or parts[2] != "LINE"
        or parts[4] != "WORD"
        or parts[6] != "SPATIAL_ANALYSIS"
    ):
        raise ValueError(f"Invalid SK format: {sk}")

    receipt_id = int(parts[1])
    line_id = int(parts[3])
    word_id = int(parts[5])

    # Parse the PK to extract image_id
    pk = item["PK"]["S"]
    if not pk.startswith("IMAGE#"):
        raise ValueError(f"Invalid PK format: {pk}")
    image_id = pk[6:]  # Remove "IMAGE#" prefix

    # Parse from_position
    from_position = {
        "x": float(item["from_position"]["M"]["x"]["N"]),
        "y": float(item["from_position"]["M"]["y"]["N"]),
    }

    # Parse spatial relationships
    spatial_relationships = []
    for rel_item in item["spatial_relationships"]["L"]:
        rel_data = rel_item["M"]
        spatial_relationships.append(
            SpatialRelationship(
                to_label=rel_data["to_label"]["S"],
                to_line_id=int(rel_data["to_line_id"]["N"]),
                to_word_id=int(rel_data["to_word_id"]["N"]),
                distance=float(rel_data["distance"]["N"]),
                angle=float(rel_data["angle"]["N"]),
            )
        )

    return ReceiptWordLabelSpatialAnalysis(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        from_label=item["from_label"]["S"],
        from_position=from_position,
        spatial_relationships=spatial_relationships,
        timestamp_added=item["timestamp_added"]["S"],
        analysis_version=item["analysis_version"]["S"],
    )

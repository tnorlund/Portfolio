import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLabelAnalysis:
    """Represents a Receipt Label Analysis item in DynamoDB.

    This class handles storage and retrieval of receipt label analysis data.
    It contains information about labeled words in a receipt, including what
    type of field each word represents (e.g., business_name, address, total,
    etc.).

    Instead of using confidence scores, this class relies on detailed textual
    reasoning to explain labeling decisions.

    Attributes:
        image_id (str): UUID identifying the associated image.
        receipt_id (int): Number identifying the receipt.
        labels (List[Dict]): List of label dictionaries containing label
            information.
        timestamp_added (datetime): When this analysis was created.
        version (str): Version of the analysis (for tracking changes over
            time).
        overall_reasoning (str): Explanation of the overall labeling decisions.
        metadata (Dict): Additional metadata including processing metrics and
            history.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "labels",
        "timestamp_added",
    }

    image_id: str
    receipt_id: int
    labels: List[Dict[str, Any]]
    timestamp_added: datetime | str
    version: str = "1.0"
    overall_reasoning: str = ""
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Initializes and validates the ReceiptLabelAnalysis object.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(self.image_id, str):
            raise ValueError("image_id must be a string")
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if not isinstance(self.labels, list):
            raise ValueError("labels must be a list")

        if not isinstance(self.version, str):
            raise ValueError("version must be a string")
        if not isinstance(self.overall_reasoning, str):
            raise ValueError("overall_reasoning must be a string")

        if isinstance(self.timestamp_added, datetime):
            self.timestamp_added = self.timestamp_added.isoformat()
        elif isinstance(self.timestamp_added, str):
            pass  # Already a string, no conversion needed
        else:
            raise ValueError(
                "timestamp_added must be a datetime object or a string"
            )

        # Initialize default metadata if not provided
        if self.metadata is None:
            self.metadata = {
                "processing_metrics": {
                    "processing_time_ms": 0,
                    "api_calls": 0,
                },
                "processing_history": [
                    {
                        "event_type": "creation",
                        "timestamp": self.timestamp_added,
                        "description": "Initial creation of label analysis",
                        "model_version": "unknown",
                    }
                ],
                "source_information": {
                    "model_name": "unknown",
                    "model_version": "unknown",
                    "algorithm": "unknown",
                    "configuration": {},
                },
            }

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
        """Returns the primary key for DynamoDB.

        Returns:
            dict: A dictionary containing the primary key attributes.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#LABELS"},
        }

    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
        """Returns the GSI1 key for DynamoDB.

        Returns:
            dict: A dictionary containing the GSI1 key attributes.
        """
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"LABELS#{self.timestamp_added}"},
        }

    def gsi2_key(self) -> Dict[str, Dict[str, str]]:
        """Returns the GSI2 key for DynamoDB.

        Returns:
            dict: A dictionary containing the GSI2 key attributes.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptLabelAnalysis object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLabelAnalysis object as
                a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_LABEL_ANALYSIS"},
            "labels": {
                "L": [
                    {
                        "M": {
                            "label_type": {"S": label.get("label_type", "")},
                            "line_id": {"N": str(label.get("line_id", 0))},
                            "word_id": {"N": str(label.get("word_id", 0))},
                            "text": {"S": label.get("text", "")},
                            "reasoning": {"S": label.get("reasoning", "")},
                            "bounding_box": {
                                "M": self._convert_bounding_box(
                                    label.get("bounding_box", {})
                                )
                            },
                        }
                    }
                    for label in self.labels
                ]
            },
            "timestamp_added": {"S": self.timestamp_added},
            "version": {"S": self.version},
            "overall_reasoning": {"S": self.overall_reasoning},
            "metadata": {"S": json.dumps(self.metadata)},
        }

    def _convert_bounding_box(
        self, bounding_box: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Converts a bounding box dictionary to DynamoDB format.

        Args:
            bounding_box (Dict): Dictionary containing top_left,
                top_right, bottom_left and bottom_right points.

        Returns:
            Dict: DynamoDB formatted bounding box.
        """
        if not bounding_box:
            return {}

        result: Dict[str, Any] = {}

        for key in ["top_left", "top_right", "bottom_left", "bottom_right"]:
            if key in bounding_box:
                point = bounding_box[key]
                if isinstance(point, dict) and "x" in point and "y" in point:
                    result[key] = {
                        "M": {
                            "x": {"N": str(point["x"])},
                            "y": {"N": str(point["y"])},
                        }
                    }

        return result

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptLabelAnalysis object.

        Returns:
            str: A string representation of the ReceiptLabelAnalysis object.
        """
        return (
            f"ReceiptLabelAnalysis(image_id={self.image_id}, "
            f"receipt_id={self.receipt_id}, "
            f"labels_count={len(self.labels)}, "
            f"timestamp_added={self.timestamp_added}, "
            f"version={self.version})"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Return an iterator over the object's attributes.

        Yields:
            Tuple[str, Any]: A tuple containing an attribute name and its
                value.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "labels", self.labels
        yield "timestamp_added", self.timestamp_added
        yield "version", self.version
        yield "overall_reasoning", self.overall_reasoning
        yield "metadata", self.metadata

    def __hash__(self) -> int:
        """Returns a hash of the ReceiptLabelAnalysis object.

        Returns:
            int: A hash of the ReceiptLabelAnalysis object.
        """
        return hash(
            (
                self.image_id,
                self.receipt_id,
                str(self.labels),  # Convert to string for hashing
                self.timestamp_added,
                self.version,
                self.overall_reasoning,
                str(self.metadata),  # Convert to string for hashing
            )
        )

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptLabelAnalysis":
        """Converts a DynamoDB item to a ReceiptLabelAnalysis object.

        Args:
            item: A DynamoDB item representing a ReceiptLabelAnalysis.

        Returns:
            ReceiptLabelAnalysis: A ReceiptLabelAnalysis object.

        Raises:
            ValueError: If the item is not a valid ReceiptLabelAnalysis item.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - item.keys()
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        # Extract image_id and receipt_id from PK and SK
        image_id = item["PK"]["S"].replace("IMAGE#", "")
        sk_parts = item["SK"]["S"].split("#")

        if (
            len(sk_parts) < 4
            or sk_parts[0] != "RECEIPT"
            or sk_parts[2] != "ANALYSIS"
            or sk_parts[3] != "LABELS"
        ):
            raise ValueError("Invalid SK format for ReceiptLabelAnalysis")

        receipt_id = int(sk_parts[1])

        # Extract labels
        labels = _parse_labels_list(item)

        # Extract timestamp_added
        timestamp_added = (
            datetime.fromisoformat(item["timestamp_added"]["S"])
            if "timestamp_added" in item and "S" in item["timestamp_added"]
            else datetime.now()
        )

        # Extract version
        version = (
            item["version"]["S"]
            if "version" in item and "S" in item["version"]
            else "1.0"
        )

        # Extract overall_reasoning
        overall_reasoning = (
            item["overall_reasoning"]["S"]
            if "overall_reasoning" in item and "S" in item["overall_reasoning"]
            else ""
        )

        # Extract metadata
        metadata = None
        if "metadata" in item and "S" in item["metadata"]:
            try:
                metadata = json.loads(item["metadata"]["S"])
            except json.JSONDecodeError:
                metadata = None

        return cls(
            image_id=image_id,
            receipt_id=receipt_id,
            labels=labels,
            timestamp_added=timestamp_added,
            version=version,
            overall_reasoning=overall_reasoning,
            metadata=metadata,
        )


def _parse_labels_list(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse labels list from DynamoDB item format."""
    labels = []
    if "labels" not in item or "L" not in item["labels"]:
        return labels

    for label_item in item["labels"]["L"]:
        if "M" in label_item:
            label_dict = _parse_single_label(label_item["M"])
            if label_dict:
                labels.append(label_dict)
    return labels


def _parse_single_label(label_map: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a single label from DynamoDB map format."""
    label_dict: Dict[str, Any] = {}

    # Extract string fields
    if "label_type" in label_map and "S" in label_map["label_type"]:
        label_dict["label_type"] = label_map["label_type"]["S"]
    if "text" in label_map and "S" in label_map["text"]:
        label_dict["text"] = label_map["text"]["S"]
    if "reasoning" in label_map and "S" in label_map["reasoning"]:
        label_dict["reasoning"] = label_map["reasoning"]["S"]

    # Extract numeric fields
    if "line_id" in label_map and "N" in label_map["line_id"]:
        label_dict["line_id"] = int(label_map["line_id"]["N"])
    if "word_id" in label_map and "N" in label_map["word_id"]:
        label_dict["word_id"] = int(label_map["word_id"]["N"])

    # Extract bounding_box if present
    if "bounding_box" in label_map and "M" in label_map["bounding_box"]:
        bbox = _parse_bounding_box(label_map["bounding_box"]["M"])
        if bbox:
            label_dict["bounding_box"] = bbox

    return label_dict


def _parse_bounding_box(bbox_map: Dict[str, Any]) -> Dict[str, Any]:
    """Parse bounding box corners from DynamoDB map format."""
    bbox: Dict[str, Any] = {}
    corners = ["top_left", "top_right", "bottom_left", "bottom_right"]

    for corner in corners:
        if corner in bbox_map and "M" in bbox_map[corner]:
            point = _parse_corner_point(bbox_map[corner]["M"])
            if point:
                bbox[corner] = point
    return bbox


def _parse_corner_point(
    point_map: Dict[str, Any],
) -> Optional[Dict[str, float]]:
    """Parse a corner point (x, y) from DynamoDB map format."""
    point: Dict[str, float] = {}
    if "x" in point_map and "N" in point_map["x"]:
        point["x"] = float(point_map["x"]["N"])
    if "y" in point_map and "N" in point_map["y"]:
        point["y"] = float(point_map["y"]["N"])
    return point if point else None


def item_to_receipt_label_analysis(
    item: Dict[str, Any],
) -> ReceiptLabelAnalysis:
    """Converts a DynamoDB item to a ReceiptLabelAnalysis object.

    Args:
        item (dict): A DynamoDB item representing a ReceiptLabelAnalysis.

    Returns:
        ReceiptLabelAnalysis: A ReceiptLabelAnalysis object.

    Raises:
        ValueError: If the item is not a valid ReceiptLabelAnalysis item.
    """
    return ReceiptLabelAnalysis.from_item(item)

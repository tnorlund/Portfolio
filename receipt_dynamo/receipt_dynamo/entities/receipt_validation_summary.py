# receipt_dynamo/receipt_dynamo/entities/receipt_validation_summary.py
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_non_empty_string,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptValidationSummary(SerializationMixin):
    """
    DynamoDB entity representing the overall validation summary for a receipt.
    This is the parent item for all validation data for a receipt.
    """

    receipt_id: int
    image_id: str
    overall_status: str
    overall_reasoning: str
    field_summary: dict[str, dict[str, Any]]
    validation_timestamp: str | None = None
    version: str = "1.0.0"
    metadata: dict[str, Any] | None = None
    timestamp_added: str | datetime | None = None
    timestamp_updated: str | datetime | None = None

    def __post_init__(self):
        """
        Initialize a ReceiptValidationSummary.

        Raises:
            ValueError: If any parameter is invalid
        """
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        validate_non_empty_string("overall_status", self.overall_status)
        validate_non_empty_string("overall_reasoning", self.overall_reasoning)

        # Store field_summary as an instance attribute
        if not isinstance(self.field_summary, dict):
            raise ValueError("field_summary must be a dictionary")
        self.field_summary = deepcopy(self.field_summary)
        self.validation_timestamp = validate_iso_timestamp(
            self.validation_timestamp, "validation_timestamp"
        )
        validate_non_empty_string("version", self.version)

        # Initialize metadata with default structure if not provided
        if self.metadata is None:
            self.metadata = {
                "processing_metrics": {},
                "source_info": {},
                "processing_history": [],
            }
        else:
            if not isinstance(self.metadata, dict):
                raise ValueError("metadata must be a dictionary")

            # Ensure metadata has the expected structure
            self.metadata = deepcopy(self.metadata)
            if "processing_metrics" not in self.metadata:
                self.metadata["processing_metrics"] = {}
            if "source_info" not in self.metadata:
                self.metadata["source_info"] = {}
            if "processing_history" not in self.metadata:
                self.metadata["processing_history"] = []

        if not isinstance(self.metadata["processing_metrics"], dict):
            raise ValueError(
                "metadata processing_metrics must be a dictionary"
            )
        if not isinstance(self.metadata["source_info"], dict):
            raise ValueError("metadata source_info must be a dictionary")
        if not isinstance(self.metadata["processing_history"], list):
            raise ValueError("metadata processing_history must be a list")

        if self.timestamp_added is not None:
            self.timestamp_added = validate_iso_timestamp(
                self.timestamp_added, "timestamp_added"
            )

        if self.timestamp_updated is not None:
            self.timestamp_updated = validate_iso_timestamp(
                self.timestamp_updated, "timestamp_updated"
            )

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """Return the DynamoDB key for this item."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#VALIDATION"},
        }

    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"VALIDATION#{self.validation_timestamp}"},
        }

    def gsi2_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI2 key for this item."""
        return {
            "GSI2PK": {
                "S": f"VALIDATION_SUMMARY_STATUS#{self.overall_status}"
            },
            "GSI2SK": {"S": f"TIMESTAMP#{self.validation_timestamp}"},
        }

    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"VALIDATION_STATUS#{self.overall_status}"},
            "GSI3SK": {"S": f"TIMESTAMP#{self.validation_timestamp}"},
        }

    def to_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB item."""
        item = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "RECEIPT_VALIDATION_SUMMARY"},
            "overall_status": {"S": self.overall_status},
            "overall_reasoning": {"S": self.overall_reasoning},
            "validation_timestamp": {"S": self.validation_timestamp},
            "version": {"S": self.version},
            "field_summary": self._python_to_dynamo(self.field_summary),
            "metadata": self._python_to_dynamo(self.metadata),
        }

        # Add timestamps if they exist
        if self.timestamp_added:
            item["timestamp_added"] = {"S": self.timestamp_added}
        else:
            item["timestamp_added"] = {"NULL": True}

        if self.timestamp_updated:
            item["timestamp_updated"] = {"S": self.timestamp_updated}
        else:
            item["timestamp_updated"] = {"NULL": True}

        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptValidationSummary":
        """Create a ReceiptValidationSummary from a DynamoDB item."""
        cls.validate_required_keys(
            item,
            {
                "PK",
                "SK",
                "overall_status",
                "overall_reasoning",
                "validation_timestamp",
                "field_summary",
            },
        )

        # Extract image_id and receipt_id from keys
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = int(item["SK"]["S"].split("#")[1])

        # Extract other fields with proper type conversion
        overall_status = item.get("overall_status", {}).get("S", "")
        overall_reasoning = item.get("overall_reasoning", {}).get("S", "")

        # Convert field_summary from DynamoDB format
        field_summary = cls._dynamo_to_python(item["field_summary"])

        validation_timestamp = item.get("validation_timestamp", {}).get("S")
        version = item.get("version", {}).get("S", "1.0.0")

        # Convert metadata from DynamoDB format
        metadata = cls._dynamo_to_python(item.get("metadata", {"M": {}}))

        # Parse timestamps if present
        timestamp_added = item.get("timestamp_added", {}).get("S")
        timestamp_updated = item.get("timestamp_updated", {}).get("S")

        # Create and return the object
        return cls(
            receipt_id=receipt_id,
            image_id=image_id,
            overall_status=overall_status,
            overall_reasoning=overall_reasoning,
            field_summary=field_summary,
            validation_timestamp=validation_timestamp,
            version=version,
            metadata=metadata,
            timestamp_added=timestamp_added,
            timestamp_updated=timestamp_updated,
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptValidationSummary(receipt_id={self.receipt_id}, "
            f"image_id={self.image_id}, "
            f"overall_status={self.overall_status})"
        )

    def add_processing_metric(self, metric_name: str, value: Any) -> None:
        """Adds a processing metric to the metadata.

        Args:
            metric_name (str): The name of the metric.
            value (Any): The value of the metric.
        """
        metadata = self.metadata
        if metadata is None:  # pragma: no cover - normalized in __post_init__
            raise RuntimeError("metadata was not initialized")
        if "processing_metrics" not in metadata:
            metadata["processing_metrics"] = {}

        metadata["processing_metrics"][metric_name] = value

    def add_history_event(
        self, event_type: str, details: dict[str, Any] | None = None
    ) -> None:
        """Adds a history event to the metadata.

        Args:
            event_type (str): The type of event.
            details (Dict, optional): Additional details about the event.
                Defaults to None.
        """
        metadata = self.metadata
        if metadata is None:  # pragma: no cover - normalized in __post_init__
            raise RuntimeError("metadata was not initialized")
        if "processing_history" not in metadata:
            metadata["processing_history"] = []

        event = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if details:
            event.update(details)

        metadata["processing_history"].append(event)


def item_to_receipt_validation_summary(
    item: dict[str, Any],
) -> ReceiptValidationSummary:
    """
    Converts a DynamoDB item to a ReceiptValidationSummary object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptValidationSummary: The ReceiptValidationSummary object
            represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid or required keys are
            missing.
    """
    return ReceiptValidationSummary.from_item(item)

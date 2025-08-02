# receipt_dynamo/receipt_dynamo/entities/receipt_validation_summary.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.entities.util import assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class ReceiptValidationSummary:
    """
    DynamoDB entity representing the overall validation summary for a receipt.
    This is the parent item for all validation data for a receipt.
    """

    receipt_id: int
    image_id: str
    overall_status: str
    overall_reasoning: str
    field_summary: Dict[str, Dict[str, Any]]
    validation_timestamp: Optional[str] = None
    version: str = "1.0.0"
    metadata: Optional[Dict[str, Any]] = None
    timestamp_added: Optional[datetime] = None
    timestamp_updated: Optional[datetime] = None

    def __post_init__(self):
        """
        Initialize a ReceiptValidationSummary.

        Raises:
            ValueError: If any parameter is invalid
        """
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        assert_valid_uuid(self.image_id)

        if not isinstance(self.overall_status, str):
            raise ValueError("overall_status must be a string")

        if not isinstance(self.overall_reasoning, str):
            raise ValueError("overall_reasoning must be a string")

        # Store field_summary as an instance attribute
        if not isinstance(self.field_summary, dict):
            raise ValueError("field_summary must be a dictionary")

        if self.validation_timestamp is not None and not isinstance(self.validation_timestamp, str):
            raise ValueError("validation_timestamp must be a string")

        if not isinstance(self.version, str):
            raise ValueError("version must be a string")

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
            self.metadata = self.metadata.copy()
            if "processing_metrics" not in self.metadata:
                self.metadata["processing_metrics"] = {}
            if "source_info" not in self.metadata:
                self.metadata["source_info"] = {}
            if "processing_history" not in self.metadata:
                self.metadata["processing_history"] = []

        if isinstance(self.timestamp_added, datetime):
            self.timestamp_added = self.timestamp_added.isoformat()
        elif isinstance(self.timestamp_added, str):
            pass  # Already a string
        elif self.timestamp_added is not None:
            raise ValueError("timestamp_added must be a datetime, string, or None")

        if self.timestamp_updated is None:
            pass  # Leave as None
        elif isinstance(self.timestamp_updated, datetime):
            self.timestamp_updated = self.timestamp_updated.isoformat()
        elif isinstance(self.timestamp_updated, str):
            pass  # Already a string
        else:
            raise ValueError(
                "timestamp_updated must be a datetime, string, or None"
            )

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
        """Return the DynamoDB key for this item."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#VALIDATION"},
        }

    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"VALIDATION#{self.validation_timestamp}"},
        }

    def gsi2_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI2 key for this item."""
        return {
            "GSI2PK": {
                "S": f"VALIDATION_SUMMARY_STATUS#{self.overall_status}"
            },
            "GSI2SK": {"S": f"TIMESTAMP#{self.validation_timestamp}"},
        }

    def gsi3_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"VALIDATION_STATUS#{self.overall_status}"},
            "GSI3SK": {"S": f"TIMESTAMP#{self.validation_timestamp}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Convert to a DynamoDB item."""

        # Helper function to convert a dict to DynamoDB M (map) format
        def dict_to_dynamo(d):
            if not d:
                return {"M": {}}

            result = {"M": {}}
            for k, v in d.items():
                if isinstance(v, dict):
                    result["M"][k] = dict_to_dynamo(v)
                elif isinstance(v, str):
                    result["M"][k] = {"S": v}
                elif isinstance(v, (int, float)):
                    result["M"][k] = {"N": str(v)}
                elif isinstance(v, bool):
                    # Use BOOL type for boolean values
                    result["M"][k] = {"BOOL": v}
                elif v is None:
                    result["M"][k] = {"NULL": True}
                elif isinstance(v, list):
                    result["M"][k] = {
                        "L": [
                            (
                                dict_to_dynamo(item)
                                if isinstance(item, dict)
                                else (
                                    {"BOOL": item}
                                    if isinstance(item, bool)
                                    else (
                                        {"N": str(item)}
                                        if isinstance(item, (int, float))
                                        else {"S": str(item)}
                                    )
                                )
                            )
                            for item in v
                        ]
                    }
                else:
                    result["M"][k] = {"S": str(v)}
            return result

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
            "field_summary": dict_to_dynamo(self.field_summary),
            "metadata": dict_to_dynamo(self.metadata),
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
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptValidationSummary":
        """Create a ReceiptValidationSummary from a DynamoDB item."""

        # Helper function to convert DynamoDB format back to Python dicts
        def dynamo_to_python(dynamo_item):
            if "M" in dynamo_item:
                result: Dict[str, Any] = {}
                for k, v in dynamo_item["M"].items():
                    if "S" in v:
                        result[k] = v["S"]
                    elif "N" in v:
                        # Check for string booleans first
                        if v["N"] == "True":
                            result[k] = True
                        elif v["N"] == "False":
                            result[k] = False
                        else:
                            # Try to convert to int first, then float
                            try:
                                result[k] = int(v["N"])
                            except ValueError:
                                result[k] = float(v["N"])
                    elif "BOOL" in v:
                        result[k] = v["BOOL"]
                    elif "NULL" in v:
                        result[k] = None
                    elif "M" in v:
                        result[k] = dynamo_to_python(v)
                    elif "L" in v:
                        result[k] = [
                            (
                                dynamo_to_python(item)
                                if "M" in item
                                else item.get("S", item.get("N", None))
                            )
                            for item in v["L"]
                        ]
                return result
            return {}

        # Extract image_id and receipt_id from keys
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = int(item["SK"]["S"].split("#")[1])

        # Extract other fields with proper type conversion
        overall_status = item.get("overall_status", {}).get("S", "")
        overall_reasoning = item.get("overall_reasoning", {}).get("S", "")

        # Convert field_summary from DynamoDB format
        field_summary = dynamo_to_python(item.get("field_summary", {"M": {}}))

        validation_timestamp = item.get("validation_timestamp", {}).get("S")
        version = item.get("version", {}).get("S", "1.0.0")

        # Convert metadata from DynamoDB format
        metadata = dynamo_to_python(item.get("metadata", {"M": {}}))

        # Parse timestamps if present
        timestamp_added = None
        if "timestamp_added" in item and "S" in item["timestamp_added"]:
            try:
                timestamp_added = datetime.fromisoformat(
                    item["timestamp_added"]["S"]
                )
            except (ValueError, TypeError):
                pass

        timestamp_updated = None
        if "timestamp_updated" in item and "S" in item["timestamp_updated"]:
            try:
                timestamp_updated = datetime.fromisoformat(
                    item["timestamp_updated"]["S"]
                )
            except (ValueError, TypeError):
                pass

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
        if "processing_metrics" not in self.metadata:
            self.metadata["processing_metrics"] = {}

        self.metadata["processing_metrics"][metric_name] = value

    def add_history_event(
        self, event_type: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Adds a history event to the metadata.

        Args:
            event_type (str): The type of event.
            details (Dict, optional): Additional details about the event.
                Defaults to None.
        """
        if "processing_history" not in self.metadata:
            self.metadata["processing_history"] = []

        event = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if details:
            event.update(details)

        self.metadata["processing_history"].append(event)


def item_to_receipt_validation_summary(
    item: Dict[str, Any],
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
    required_keys = {
        "PK",
        "SK",
        "overall_status",
        "overall_reasoning",
        "validation_timestamp",
        "field_summary",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")

    try:
        # Helper function to convert DynamoDB format back to Python dicts
        def dynamo_to_python(dynamo_item):
            if "M" in dynamo_item:
                result: Dict[str, Any] = {}
                for k, v in dynamo_item["M"].items():
                    if "S" in v:
                        result[k] = v["S"]
                    elif "N" in v:
                        # Check for string booleans first
                        if v["N"] == "True":
                            result[k] = True
                        elif v["N"] == "False":
                            result[k] = False
                        else:
                            # Try to convert to int first, then float
                            try:
                                result[k] = int(v["N"])
                            except ValueError:
                                result[k] = float(v["N"])
                    elif "BOOL" in v:
                        result[k] = v["BOOL"]
                    elif "NULL" in v:
                        result[k] = None
                    elif "M" in v:
                        result[k] = dynamo_to_python(v)
                    elif "L" in v:
                        result[k] = [
                            (
                                dynamo_to_python(item)
                                if "M" in item
                                else item.get(
                                    "S", item.get("N", item.get("BOOL", None))
                                )
                            )
                            for item in v["L"]
                        ]
                return result
            return {}

        # Extract basic values
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = int(item["SK"]["S"].split("#")[1])
        overall_status = item["overall_status"]["S"]
        overall_reasoning = item["overall_reasoning"]["S"]
        validation_timestamp = item["validation_timestamp"]["S"]
        version = item.get("version", {}).get("S", "1.0.0")

        # Convert complex structures
        field_summary = dynamo_to_python(item["field_summary"])
        metadata = dynamo_to_python(item.get("metadata", {"M": {}}))

        # Handle timestamps
        timestamp_added = item.get("timestamp_added", {}).get("S")
        timestamp_updated = item.get("timestamp_updated", {}).get("S")

        return ReceiptValidationSummary(
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
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(
            "Error converting item to ReceiptValidationSummary"
        ) from e

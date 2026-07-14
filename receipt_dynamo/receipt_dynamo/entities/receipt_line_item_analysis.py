"""Receipt line item analysis entity definitions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Generator

from receipt_dynamo.entities.dynamodb_utils import (
    dict_to_dynamodb_map,
    freeze_for_hash,
    parse_dynamodb_map,
)
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLineItemAnalysis:  # pylint: disable=too-many-instance-attributes
    """
    Represents a receipt line item analysis stored in a DynamoDB table.

    This class encapsulates the results of line item analysis for a receipt.
    It includes individual line items identified, financial totals
    (subtotal, tax, total), and detailed reasoning explaining how the
    analysis was performed.

    Attributes:
        image_id (str): UUID identifying the image the receipt is from.
        receipt_id (str): UUID identifying the receipt.
        timestamp_added (datetime or str):
            The timestamp when the analysis was added.
        items (list[Dict]): List of line items identified in the receipt.
        reasoning (str):
            Detailed reasoning explaining how the analysis was performed.
        subtotal (Decimal, optional): The subtotal amount from the receipt.
        tax (Decimal, optional): The tax amount from the receipt.
        total (Decimal, optional): The total amount from the receipt.
        fees (Decimal, optional): Any fees on the receipt.
        discounts (Decimal, optional): Any discounts on the receipt.
        tips (Decimal, optional): Any tips on the receipt.
        total_found (int): The number of line items found.
        discrepancies (list[str]): Any discrepancies found during analysis.
        version (str): Version of the analysis.
        metadata (Dict): Additional metadata about the analysis.
        word_labels (Dict):
            Mapping of (line_id, word_id) tuples to label information.
        timestamp_updated (str | None):
            The timestamp when the analysis was last updated.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "items",
        "reasoning",
        "timestamp_added",
        "version",
        "total_found",
    }

    image_id: str
    receipt_id: int
    timestamp_added: datetime | str
    items: list[dict[str, Any]]
    reasoning: str
    version: str
    subtotal: Decimal | str | None = None
    tax: Decimal | str | None = None
    total: Decimal | str | None = None
    fees: Decimal | str | None = None
    discounts: Decimal | str | None = None
    tips: Decimal | str | None = None
    total_found: int | None = None
    discrepancies: list[str] | None = None
    metadata: dict[str, Any] | None = None
    word_labels: dict[tuple[int, int | None], dict[str, Any]] | None = None
    timestamp_updated: str | None = None

    def __post_init__(self):
        """Initializes and validates the ReceiptLineItemAnalysis object.

        Raises:
            ValueError:
                If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(self.image_id)

        validate_positive_int("receipt_id", self.receipt_id)

        # Validate and convert timestamps
        self.timestamp_added = self._validate_timestamp(
            self.timestamp_added, "timestamp_added"
        )
        if self.timestamp_updated is not None:
            self.timestamp_updated = self._validate_timestamp(
                self.timestamp_updated, "timestamp_updated"
            )

        # Validate required fields
        if not isinstance(self.items, list):
            raise ValueError("items must be a list")
        self.items = [self._validate_item(item) for item in self.items]
        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")
        if not isinstance(self.version, str):
            raise ValueError("version must be a string")

        # Process financial amounts (can be Decimal or string)
        self.subtotal = self._process_decimal(self.subtotal, "subtotal")
        self.tax = self._process_decimal(self.tax, "tax")
        self.total = self._process_decimal(self.total, "total")
        self.fees = self._process_decimal(self.fees, "fees")
        self.discounts = self._process_decimal(self.discounts, "discounts")
        self.tips = self._process_decimal(self.tips, "tips")

        # Process total_found
        if self.total_found is not None:
            if (
                isinstance(self.total_found, bool)
                or not isinstance(self.total_found, int)
                or self.total_found < 0
            ):
                raise ValueError("total_found must be a non-negative integer")
        else:
            self.total_found = len(self.items)

        # Initialize optional collection fields with defaults
        self._init_optional_fields()

    def _validate_timestamp(
        self, value: datetime | str, field_name: str
    ) -> str:
        """Validate and convert a timestamp to ISO format string."""
        return validate_iso_timestamp(value, field_name, default_now=False)

    def _init_optional_fields(  # pylint: disable=too-many-branches
        self,
    ) -> None:
        """Initialize optional collection fields with defaults."""
        if self.discrepancies is not None:
            if not isinstance(self.discrepancies, list):
                raise ValueError("discrepancies must be a list")
            if any(
                not isinstance(discrepancy, str)
                for discrepancy in self.discrepancies
            ):
                raise ValueError("discrepancies must be a list of strings")
            self.discrepancies = list(self.discrepancies)
        else:
            self.discrepancies = []

        if self.metadata is not None:
            if not isinstance(self.metadata, dict):
                raise ValueError("metadata must be a dictionary")
        else:
            self.metadata = {
                "processing_metrics": {},
                "processing_history": [],
                "source_information": {},
            }

        if self.word_labels is not None:
            if not isinstance(self.word_labels, dict):
                raise ValueError("word_labels must be a dictionary")
            normalized_word_labels = {}
            for key, label_info in self.word_labels.items():
                if not isinstance(key, tuple) or len(key) != 2:
                    raise ValueError(
                        "word_labels keys must be (line_id, word_id) tuples"
                    )
                line_id, word_id = key
                validate_positive_int("line_id", line_id)
                if word_id is not None:
                    validate_positive_int("word_id", word_id)
                if not isinstance(label_info, dict):
                    raise ValueError("word label values must be dictionaries")
                normalized_word_labels[(line_id, word_id)] = dict(label_info)
            self.word_labels = normalized_word_labels
        else:
            self.word_labels = {}

    def _validate_item(  # pylint: disable=too-many-branches
        self, item: Any
    ) -> dict[str, Any]:
        """Validate and detach one line-item analysis payload."""
        if not isinstance(item, dict):
            raise ValueError("each item must be a dictionary")
        allowed_keys = {
            "description",
            "reasoning",
            "line_ids",
            "quantity",
            "price",
            "metadata",
        }
        if not set(item).issubset(allowed_keys):
            raise ValueError("line item contains unsupported fields")
        normalized = dict(item)
        for field_name in ("description", "reasoning"):
            if field_name in normalized and not isinstance(
                normalized[field_name], str
            ):
                raise ValueError(f"item {field_name} must be a string")
        if "line_ids" in normalized:
            line_ids = normalized["line_ids"]
            if not isinstance(line_ids, list):
                raise ValueError("line_ids must be a list")
            for line_id in line_ids:
                validate_positive_int("line_id", line_id)
            normalized["line_ids"] = list(line_ids)
        if "quantity" in normalized:
            quantity = normalized["quantity"]
            if not isinstance(quantity, dict) or not set(quantity).issubset(
                {"amount", "unit"}
            ):
                raise ValueError("quantity must be a dictionary")
            quantity = dict(quantity)
            if "amount" in quantity:
                quantity["amount"] = self._process_decimal(
                    quantity["amount"], "quantity amount"
                )
            if "unit" in quantity and not isinstance(quantity["unit"], str):
                raise ValueError("quantity unit must be a string")
            normalized["quantity"] = quantity
        if "price" in normalized:
            price = normalized["price"]
            if not isinstance(price, dict) or not set(price).issubset(
                {"unit_price", "extended_price"}
            ):
                raise ValueError("price must be a dictionary")
            price = dict(price)
            for field_name in ("unit_price", "extended_price"):
                if field_name in price:
                    price[field_name] = self._process_decimal(
                        price[field_name], field_name
                    )
            normalized["price"] = price
        if "metadata" in normalized and not isinstance(
            normalized["metadata"], dict
        ):
            raise ValueError("item metadata must be a dictionary")
        return normalized

    def _process_decimal(self, value, field_name):
        """Process a value that could be a Decimal, string, or None."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, str):
            try:
                result = Decimal(value)
            except (InvalidOperation, ValueError) as e:
                raise ValueError(
                    f"{field_name} string must be convertible to Decimal"
                ) from e
        else:
            raise ValueError(
                f"{field_name} must be a Decimal, string, or None"
            )
        if not result.is_finite():
            raise ValueError(f"{field_name} must be finite")
        return result

    @property
    def key(self) -> dict[str, Any]:
        """Generates the primary key for the receipt line item analysis.

        Returns:
            dict: The primary key for the receipt line item analysis.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#LINE_ITEMS"},
        }

    def gsi1_key(self) -> dict[str, Any]:
        """Generates the GSI1 key for the receipt line item analysis.

        Returns:
            dict: The GSI1 key for the receipt line item analysis.
        """
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"LINE_ITEMS#{self.timestamp_added}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        """Generates the GSI2 key for the receipt line item analysis.

        Returns:
            dict: The GSI2 key for the receipt line item analysis.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Converts the ReceiptLineItemAnalysis object to a DynamoDB item.

        Returns:
            dict:
                A dictionary representing the ReceiptLineItemAnalysis object as
                a DynamoDB item.
        """
        self.__post_init__()
        item: dict[str, Any] = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_LINE_ITEM_ANALYSIS"},
            "version": {"S": self.version},
            "timestamp_added": {"S": self.timestamp_added},
            "reasoning": {"S": self.reasoning},
            "total_found": {"N": str(self.total_found)},
        }

        # Add timestamp_updated if present
        if self.timestamp_updated is not None:
            item["timestamp_updated"] = {"S": self.timestamp_updated}

        # Convert items list to DynamoDB format
        items_list: list[dict[str, Any]] = [
            self._convert_item_to_dynamo(i) for i in self.items
        ]
        item["items"] = {"L": items_list}

        # Convert discrepancies list to DynamoDB format
        discrepancies_list: list[dict[str, str]] = [
            {"S": discrepancy} for discrepancy in (self.discrepancies or [])
        ]
        item["discrepancies"] = {"L": discrepancies_list}

        # Add word_labels if present
        item["word_labels"] = self._serialize_word_labels()

        # Add financial fields
        financial_fields = (
            "subtotal",
            "tax",
            "total",
            "fees",
            "discounts",
            "tips",
        )
        for field_name in financial_fields:
            item[field_name] = self._serialize_optional_decimal(
                getattr(self, field_name)
            )

        # Add metadata if present
        item["metadata"] = (
            {"M": self._convert_dict_to_dynamo(self.metadata)}
            if self.metadata
            else {"NULL": True}
        )

        return item

    def _serialize_word_labels(self) -> dict[str, Any]:
        """Serialize word_labels to DynamoDB format."""
        if not self.word_labels:
            return {"NULL": True}
        word_labels_dynamo: dict[str, Any] = {}
        for (line_id, word_id), label_info in self.word_labels.items():
            key = f"{line_id}:{'' if word_id is None else word_id}"
            word_labels_dynamo[key] = {
                "M": self._convert_dict_to_dynamo(label_info)
            }
        return {"M": word_labels_dynamo}

    @staticmethod
    def _serialize_optional_decimal(value: Any) -> dict[str, Any]:
        """Serialize an optional Decimal to DynamoDB format."""
        if value is not None:
            return {"S": str(value)}
        return {"NULL": True}

    def _convert_item_to_dynamo(self, item: dict[str, Any]) -> dict[str, Any]:
        """Converts a line item dictionary to DynamoDB format.

        Args:
            item (Dict): The line item dictionary.

        Returns:
            Dict: The line item in DynamoDB format.
        """
        result: dict[str, Any] = {"M": {}}

        # Add required fields
        if "description" in item:
            result["M"]["description"] = {"S": item["description"]}

        if "reasoning" in item:
            result["M"]["reasoning"] = {"S": item["reasoning"]}

        # Convert line_ids if present
        if "line_ids" in item and item["line_ids"]:
            result["M"]["line_ids"] = {
                "L": [{"N": str(line_id)} for line_id in item["line_ids"]]
            }

        # Convert quantity if present
        if "quantity" in item and item["quantity"]:
            quantity = item["quantity"]
            quantity_map: dict[str, Any] = {"M": {}}

            if "amount" in quantity:
                quantity_map["M"]["amount"] = {"S": str(quantity["amount"])}

            if "unit" in quantity:
                quantity_map["M"]["unit"] = {"S": quantity["unit"]}

            result["M"]["quantity"] = quantity_map

        # Convert price if present
        if "price" in item and item["price"]:
            price = item["price"]
            price_map: dict[str, Any] = {"M": {}}

            if "unit_price" in price and price["unit_price"] is not None:
                price_map["M"]["unit_price"] = {"S": str(price["unit_price"])}

            if (
                "extended_price" in price
                and price["extended_price"] is not None
            ):
                price_map["M"]["extended_price"] = {
                    "S": str(price["extended_price"])
                }

            result["M"]["price"] = price_map

        # Add metadata if present
        if "metadata" in item and item["metadata"]:
            result["M"]["metadata"] = {
                "M": self._convert_dict_to_dynamo(item["metadata"])
            }

        return result

    def _convert_dict_to_dynamo(self, d: dict) -> dict:
        """Recursively converts a dictionary to DynamoDB format.

        Args:
            d (Dict): The dictionary to convert.

        Returns:
            Dict: The dictionary in DynamoDB format.
        """
        return dict_to_dynamodb_map(d)

    def add_processing_metric(self, metric_name: str, value: Any) -> None:
        """Adds a processing metric to the metadata.

        Args:
            metric_name (str): The name of the metric.
            value (Any): The value of the metric.
        """
        metadata = self.metadata if self.metadata is not None else {}
        self.metadata = metadata
        if "processing_metrics" not in metadata:
            metadata["processing_metrics"] = {}

        metadata["processing_metrics"][metric_name] = value

    def add_history_event(
        self, event_type: str, details: dict[str, Any] | None = None
    ) -> None:
        """Adds a history event to the metadata.

        Args:
            event_type (str): The type of event.
            details (Dict, optional):
                Additional details about the event. Defaults to None.
        """
        metadata = self.metadata if self.metadata is not None else {}
        self.metadata = metadata
        if "processing_history" not in metadata:
            metadata["processing_history"] = []

        event = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
        }

        if details:
            event.update(details)

        metadata["processing_history"].append(event)

    def generate_reasoning(self) -> str:
        """Generate a comprehensive reasoning explanation for the line item
        analysis.

        Returns:
            str:
                A detailed explanation of how items were identified and
                calculations performed.
        """
        reasoning_parts = [
            f"Analyzed {self.total_found} line items from the receipt."
        ]

        # Add financial summary
        financial_parts = []
        if self.subtotal:
            financial_parts.append(f"Subtotal: ${self.subtotal}")
        if self.tax:
            financial_parts.append(f"Tax: ${self.tax}")
        if self.fees:
            financial_parts.append(f"Fees: ${self.fees}")
        if self.discounts:
            financial_parts.append(f"Discounts: ${self.discounts}")
        if self.tips:
            financial_parts.append(f"Tips: ${self.tips}")
        if self.total:
            financial_parts.append(f"Total: ${self.total}")

        if financial_parts:
            reasoning_parts.append(
                "Financial summary: " + ", ".join(financial_parts)
            )

        # Add discrepancies if any
        if self.discrepancies:
            reasoning_parts.append(
                "Discrepancies found: " + "; ".join(self.discrepancies)
            )

        # Add item reasoning summary
        item_reasons = [
            item.get("reasoning", "")
            for item in self.items
            if item.get("reasoning")
        ]
        if item_reasons:
            # Just include a summary count to avoid extremely long reasoning
            # strings
            reasoning_parts.append(
                "Detailed reasoning provided for "
                f"{len(item_reasons)} individual line items."
            )

        return " ".join(reasoning_parts)

    def get_item_by_description(
        self, description: str
    ) -> dict[str, Any] | None:
        """
        Find a line item by its description.

        Args:
            description (str): The description to search for.

        Returns:
            Dict | None: The matching line item, or None if not found.
        """
        for item in self.items:
            if item.get("description", "").lower() == description.lower():
                return item
        return None

    def __repr__(self) -> str:
        """Return a string representation of the
        :class:`ReceiptLineItemAnalysis` object.

        Returns:
            str: A string representation of the object.
        """
        return (
            "ReceiptLineItemAnalysis("
            f"image_id='{self.image_id}', "
            f"receipt_id={self.receipt_id}, "
            f"timestamp_added='{self.timestamp_added}', "
            f"items=[{len(self.items)} items], "
            f"subtotal={self.subtotal}, "
            f"tax={self.tax}, "
            f"total={self.total}, "
            f"total_found={self.total_found}, "
            f"version='{self.version}', "
            f"reasoning='{self.reasoning[:30]}...'"
            ")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Return an iterator over the object's attributes.

        Returns:
            Generator[tuple[str, Any], None, None]:
                An iterator over attribute name/value pairs.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "timestamp_added", self.timestamp_added
        yield "timestamp_updated", self.timestamp_updated
        yield "items", self.items
        yield "reasoning", self.reasoning
        yield "subtotal", self.subtotal
        yield "tax", self.tax
        yield "total", self.total
        yield "fees", self.fees
        yield "discounts", self.discounts
        yield "tips", self.tips
        yield "total_found", self.total_found
        yield "discrepancies", self.discrepancies
        yield "version", self.version
        yield "metadata", self.metadata
        yield "word_labels", self.word_labels

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptLineItemAnalysis object.

        Returns:
            int: The hash value of the ReceiptLineItemAnalysis object.
        """
        return hash(
            (
                self.image_id,
                self.receipt_id,
                self.timestamp_added,
                self.timestamp_updated,
                freeze_for_hash(self.items),
                self.reasoning,
                self.subtotal,
                self.tax,
                self.total,
                self.fees,
                self.discounts,
                self.tips,
                self.total_found,
                freeze_for_hash(self.discrepancies),
                self.version,
                freeze_for_hash(self.metadata),
                freeze_for_hash(self.word_labels),
            )
        )

    @classmethod
    def from_item(  # pylint: disable=too-many-locals
        cls, item: dict[str, Any]
    ) -> "ReceiptLineItemAnalysis":
        """Converts a DynamoDB item to a ReceiptLineItemAnalysis object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptLineItemAnalysis:
                The ReceiptLineItemAnalysis object
                represented by the DynamoDB item.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - item.keys()
            additional_keys = item.keys() - cls.REQUIRED_KEYS
            raise ValueError(
                "Invalid item format\nmissing keys: "
                f"{missing_keys}\nadditional keys: {additional_keys}"
            )
        try:
            if item["TYPE"] != {"S": "RECEIPT_LINE_ITEM_ANALYSIS"}:
                raise ValueError("TYPE must be RECEIPT_LINE_ITEM_ANALYSIS")

            # Extract primary identifiers
            pk_parts = item["PK"]["S"].split("#")
            if len(pk_parts) != 2 or pk_parts[0] != "IMAGE":
                raise ValueError(
                    "Invalid PK format for ReceiptLineItemAnalysis"
                )
            image_id = pk_parts[1]
            receipt_parts = item["SK"]["S"].split("#")
            if (
                len(receipt_parts) != 4
                or receipt_parts[0] != "RECEIPT"
                or receipt_parts[2:] != ["ANALYSIS", "LINE_ITEMS"]
            ):
                raise ValueError(
                    "Invalid SK format for ReceiptLineItemAnalysis"
                )
            receipt_id = int(receipt_parts[1])

            # Extract required fields
            timestamp_added = item["timestamp_added"]["S"]
            timestamp_updated = item.get("timestamp_updated", {}).get("S")
            reasoning = item["reasoning"]["S"]
            version = item["version"]["S"]
            total_found = int(item["total_found"]["N"])

            # Convert items from DynamoDB format
            items = [
                _convert_dynamo_to_item(item_dict)
                for item_dict in item["items"]["L"]
            ]

            # Extract optional financial fields
            subtotal = item.get("subtotal", {}).get("S")
            tax = item.get("tax", {}).get("S")
            total = item.get("total", {}).get("S")
            fees = item.get("fees", {}).get("S")
            discounts = item.get("discounts", {}).get("S")
            tips = item.get("tips", {}).get("S")

            # Extract discrepancies
            discrepancies = [
                discrepancy["S"]
                for discrepancy in item.get("discrepancies", {}).get("L", [])
            ]

            # Extract metadata
            metadata = (
                _convert_dynamo_to_dict(item.get("metadata", {}).get("M", {}))
                if "metadata" in item and item["metadata"].get("M")
                else None
            )

            # Extract word_labels
            word_labels: (
                dict[tuple[int, int | None], dict[str, Any]] | None
            ) = None
            if "word_labels" in item and item["word_labels"].get("M"):
                word_labels = {}
                word_labels_dynamo = item["word_labels"]["M"]
                for key, value in word_labels_dynamo.items():
                    line_part, word_part = key.split(":", 1)
                    line_id = int(line_part)
                    word_id = int(word_part) if word_part else None
                    if word_labels is not None:
                        word_labels[(line_id, word_id)] = (
                            _convert_dynamo_to_dict(value["M"])
                        )

            result = cls(
                image_id=image_id,
                receipt_id=receipt_id,
                timestamp_added=timestamp_added,
                timestamp_updated=timestamp_updated,
                items=items,
                reasoning=reasoning,
                version=version,
                total_found=total_found,
                subtotal=subtotal,
                tax=tax,
                total=total,
                fees=fees,
                discounts=discounts,
                tips=tips,
                discrepancies=discrepancies,
                metadata=metadata,
                word_labels=word_labels,
            )
            for key_name, expected_value in {
                **result.key,
                **result.gsi1_key(),
                **result.gsi2_key(),
            }.items():
                if key_name in item and item[key_name] != expected_value:
                    raise ValueError(f"{key_name} does not match entity keys")
            return result
        except (KeyError, TypeError, ValueError, IndexError) as e:
            raise ValueError(
                f"Error converting item to ReceiptLineItemAnalysis: {e}"
            ) from e


def item_to_receipt_line_item_analysis(
    item: dict[str, Any],
) -> ReceiptLineItemAnalysis:
    """Converts a DynamoDB item to a ReceiptLineItemAnalysis object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptLineItemAnalysis:
            The ReceiptLineItemAnalysis object
            represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptLineItemAnalysis.from_item(item)


def _convert_dynamo_to_item(dynamo_item: dict) -> dict:
    """Converts a DynamoDB format item to a Python dictionary.

    Args:
        dynamo_item (Dict): The DynamoDB format item.

    Returns:
        Dict: The item as a Python dictionary.
    """
    item: dict[str, Any] = {}
    item_map = dynamo_item.get("M", {})

    # Extract basic fields
    if "description" in item_map:
        item["description"] = item_map["description"]["S"]

    if "reasoning" in item_map:
        item["reasoning"] = item_map["reasoning"]["S"]

    # Extract line_ids
    if "line_ids" in item_map:
        item["line_ids"] = [
            int(line_id["N"]) for line_id in item_map["line_ids"]["L"]
        ]

    # Extract quantity
    if "quantity" in item_map:
        quantity_map = item_map["quantity"]["M"]
        item["quantity"] = {}

        if "amount" in quantity_map:
            item["quantity"]["amount"] = Decimal(quantity_map["amount"]["S"])

        if "unit" in quantity_map:
            item["quantity"]["unit"] = quantity_map["unit"]["S"]

    # Extract price
    if "price" in item_map:
        price_map = item_map["price"]["M"]
        item["price"] = {}

        if "unit_price" in price_map:
            item["price"]["unit_price"] = Decimal(price_map["unit_price"]["S"])

        if "extended_price" in price_map:
            item["price"]["extended_price"] = Decimal(
                price_map["extended_price"]["S"]
            )

    # Extract metadata
    if "metadata" in item_map and "M" in item_map["metadata"]:
        item["metadata"] = _convert_dynamo_to_dict(item_map["metadata"]["M"])

    return item


def _convert_dynamo_to_dict(dynamo_dict: dict) -> dict:
    """Recursively convert a DynamoDB format dictionary to a Python
    dictionary.

    Args:
        dynamo_dict (Dict): The DynamoDB format dictionary.

    Returns:
        Dict: The dictionary as a Python dictionary.
    """
    return parse_dynamodb_map(dynamo_dict)

"""Receipt line item analysis entity definitions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from receipt_dynamo.entities.util import assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLineItemAnalysis:
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
        items (List[Dict]): List of line items identified in the receipt.
        reasoning (str):
            Detailed reasoning explaining how the analysis was performed.
        subtotal (Decimal, optional): The subtotal amount from the receipt.
        tax (Decimal, optional): The tax amount from the receipt.
        total (Decimal, optional): The total amount from the receipt.
        fees (Decimal, optional): Any fees on the receipt.
        discounts (Decimal, optional): Any discounts on the receipt.
        tips (Decimal, optional): Any tips on the receipt.
        total_found (int): The number of line items found.
        discrepancies (List[str]): Any discrepancies found during analysis.
        version (str): Version of the analysis.
        metadata (Dict): Additional metadata about the analysis.
        word_labels (Dict):
            Mapping of (line_id, word_id) tuples to label information.
        timestamp_updated (Optional[str]):
            The timestamp when the analysis was last updated.
    """

    image_id: str
    receipt_id: int
    timestamp_added: Union[datetime, str]
    items: List[Dict[str, Any]]
    reasoning: str
    version: str
    subtotal: Optional[Union[Decimal, str]] = None
    tax: Optional[Union[Decimal, str]] = None
    total: Optional[Union[Decimal, str]] = None
    fees: Optional[Union[Decimal, str]] = None
    discounts: Optional[Union[Decimal, str]] = None
    tips: Optional[Union[Decimal, str]] = None
    total_found: Optional[int] = None
    discrepancies: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    word_labels: Optional[Dict[Tuple[int, int], Any]] = None
    timestamp_updated: Optional[str] = None

    def __post_init__(self):
        """Initializes and validates the ReceiptLineItemAnalysis object.

        Raises:
            ValueError:
                If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(self.image_id)

        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        if isinstance(self.timestamp_added, datetime):
            self.timestamp_added = self.timestamp_added.isoformat()
        elif isinstance(self.timestamp_added, str):
            pass  # Already a string
        else:
            raise ValueError(
                "timestamp_added must be a datetime object or a string"
            )

        # Store timestamp_updated if provided
        if self.timestamp_updated is not None:
            if isinstance(self.timestamp_updated, datetime):
                self.timestamp_updated = self.timestamp_updated.isoformat()
            elif isinstance(self.timestamp_updated, str):
                pass  # Already a string
            else:
                raise ValueError(
                    "timestamp_updated must be a datetime object or a string"
                )

        # Validate and process items
        if not isinstance(self.items, list):
            raise ValueError("items must be a list")

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
            if not isinstance(self.total_found, int) or self.total_found < 0:
                raise ValueError("total_found must be a non-negative integer")
        else:
            self.total_found = len(self.items)

        # Process discrepancies
        if self.discrepancies is not None:
            if not isinstance(self.discrepancies, list):
                raise ValueError("discrepancies must be a list")
        else:
            self.discrepancies = []

        # Process metadata
        if self.metadata is not None:
            if not isinstance(self.metadata, dict):
                raise ValueError("metadata must be a dictionary")
        else:
            self.metadata = {
                "processing_metrics": {},
                "processing_history": [],
                "source_information": {},
            }

        # Process word_labels
        if self.word_labels is not None:
            if not isinstance(self.word_labels, dict):
                raise ValueError("word_labels must be a dictionary")
        else:
            self.word_labels = {}

    def _process_decimal(self, value, field_name):
        """Process a value that could be a Decimal, string, or None."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str):
            try:
                return Decimal(value)
            except Exception as e:
                raise ValueError(
                    f"{field_name} string must be convertible to Decimal"
                ) from e
        raise ValueError(f"{field_name} must be a Decimal, string, or None")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the receipt line item analysis.

        Returns:
            dict: The primary key for the receipt line item analysis.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#LINE_ITEMS"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the receipt line item analysis.

        Returns:
            dict: The GSI1 key for the receipt line item analysis.
        """
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"LINE_ITEMS#{self.timestamp_added}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
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

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptLineItemAnalysis object to a DynamoDB item.

        Returns:
            dict:
                A dictionary representing the ReceiptLineItemAnalysis object as
                a DynamoDB item.
        """
        item: Dict[str, Any] = {
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
        items_list: List[Dict[str, Any]] = [
            self._convert_item_to_dynamo(i) for i in self.items
        ]
        item["items"] = {"L": items_list}

        # Convert discrepancies list to DynamoDB format
        discrepancies_list: List[Dict[str, str]] = [
            {"S": discrepancy} for discrepancy in self.discrepancies
        ]
        item["discrepancies"] = {"L": discrepancies_list}

        # Add word_labels if present
        if self.word_labels:
            word_labels_dynamo: Dict[str, Any] = {}
            for (line_id, word_id), label_info in self.word_labels.items():
                key = f"{line_id}:{word_id}"
                word_labels_dynamo[key] = {
                    "M": self._convert_dict_to_dynamo(label_info)
                }
            word_labels_value: Dict[str, Any] = {"M": word_labels_dynamo}
            item["word_labels"] = word_labels_value
        else:
            item["word_labels"] = {"NULL": True}

        # Add financial fields if present
        if self.subtotal is not None:
            subtotal_value: Dict[str, str] = {"S": str(self.subtotal)}
            item["subtotal"] = subtotal_value
        else:
            item["subtotal"] = {"NULL": True}

        if self.tax is not None:
            tax_value: Dict[str, str] = {"S": str(self.tax)}
            item["tax"] = tax_value
        else:
            item["tax"] = {"NULL": True}

        if self.total is not None:
            total_value: Dict[str, str] = {"S": str(self.total)}
            item["total"] = total_value
        else:
            item["total"] = {"NULL": True}

        if self.fees is not None:
            fees_value: Dict[str, str] = {"S": str(self.fees)}
            item["fees"] = fees_value
        else:
            item["fees"] = {"NULL": True}

        if self.discounts is not None:
            discounts_value: Dict[str, str] = {"S": str(self.discounts)}
            item["discounts"] = discounts_value
        else:
            item["discounts"] = {"NULL": True}

        if self.tips is not None:
            tips_value: Dict[str, str] = {"S": str(self.tips)}
            item["tips"] = tips_value
        else:
            item["tips"] = {"NULL": True}

        # Add metadata if present
        if self.metadata:
            metadata_value: Dict[str, Any] = {
                "M": self._convert_dict_to_dynamo(self.metadata)
            }
            item["metadata"] = metadata_value
        else:
            item["metadata"] = {"NULL": True}

        return item

    def _convert_item_to_dynamo(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Converts a line item dictionary to DynamoDB format.

        Args:
            item (Dict): The line item dictionary.

        Returns:
            Dict: The line item in DynamoDB format.
        """
        result: Dict[str, Any] = {"M": {}}

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
            quantity_map: Dict[str, Any] = {"M": {}}

            if "amount" in quantity:
                quantity_map["M"]["amount"] = {"S": str(quantity["amount"])}

            if "unit" in quantity:
                quantity_map["M"]["unit"] = {"S": quantity["unit"]}

            result["M"]["quantity"] = quantity_map

        # Convert price if present
        if "price" in item and item["price"]:
            price = item["price"]
            price_map: Dict[str, Any] = {"M": {}}

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

    def _convert_dict_to_dynamo(self, d: Dict) -> Dict:
        """Recursively converts a dictionary to DynamoDB format.

        Args:
            d (Dict): The dictionary to convert.

        Returns:
            Dict: The dictionary in DynamoDB format.
        """
        result: Dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = {"M": self._convert_dict_to_dynamo(v)}
            elif isinstance(v, list):
                result[k] = {
                    "L": [
                        (
                            {"M": self._convert_dict_to_dynamo(item)}
                            if isinstance(item, dict)
                            else (
                                {"N": str(item)}
                                if isinstance(item, (int, float, Decimal))
                                else {"S": str(item)}
                            )
                        )
                        for item in v
                    ]
                }
            elif isinstance(v, (int, float, Decimal)):
                result[k] = {"N": str(v)}
            elif v is None:
                result[k] = {"NULL": True}
            else:
                result[k] = {"S": str(v)}
        return result

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
            details (Dict, optional):
                Additional details about the event. Defaults to None.
        """
        if "processing_history" not in self.metadata:
            self.metadata["processing_history"] = []

        event = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
        }

        if details:
            event.update(details)

        self.metadata["processing_history"].append(event)

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
    ) -> Optional[Dict[str, Any]]:
        """
        Find a line item by its description.

        Args:
            description (str): The description to search for.

        Returns:
            Optional[Dict]: The matching line item, or None if not found.
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

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Return an iterator over the object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]:
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
                # Can't hash a list directly, so we'll use a tuple of counts
                len(self.items),
                self.reasoning,
                self.subtotal,
                self.tax,
                self.total,
                self.fees,
                self.discounts,
                self.tips,
                self.total_found,
                tuple(self.discrepancies) if self.discrepancies else None,
                self.version,
                # Can't hash a dict, so we'll just include its presence
                bool(self.metadata),
                bool(self.word_labels),
            )
        )


def item_to_receipt_line_item_analysis(
    item: Dict[str, Any],
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
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "items",
        "reasoning",
        "timestamp_added",
        "version",
        "total_found",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            "Invalid item format\nmissing keys: "
            f"{missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        # Extract primary identifiers
        image_id = item["PK"]["S"].split("#")[1]
        receipt_parts = item["SK"]["S"].split("#")
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
        word_labels: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None
        if "word_labels" in item and item["word_labels"].get("M"):
            word_labels = {}
            word_labels_dynamo = item["word_labels"]["M"]
            for key, value in word_labels_dynamo.items():
                line_id, word_id = map(int, key.split(":"))
                if word_labels is not None:  # Type guard for mypy
                    word_labels[(line_id, word_id)] = _convert_dynamo_to_dict(
                        value["M"]
                    )

        return ReceiptLineItemAnalysis(
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
    except KeyError as e:
        raise ValueError(
            f"Error converting item to ReceiptLineItemAnalysis: {e}"
        ) from e


def _convert_dynamo_to_item(dynamo_item: Dict) -> Dict:
    """Converts a DynamoDB format item to a Python dictionary.

    Args:
        dynamo_item (Dict): The DynamoDB format item.

    Returns:
        Dict: The item as a Python dictionary.
    """
    item: Dict[str, Any] = {}
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


def _convert_dynamo_to_dict(dynamo_dict: Dict) -> Dict:
    """Recursively convert a DynamoDB format dictionary to a Python
    dictionary.

    Args:
        dynamo_dict (Dict): The DynamoDB format dictionary.

    Returns:
        Dict: The dictionary as a Python dictionary.
    """
    result: Dict[str, Any] = {}
    for k, v in dynamo_dict.items():
        if "M" in v:
            result[k] = _convert_dynamo_to_dict(v["M"])
        elif "L" in v:
            result[k] = [
                (
                    _convert_dynamo_to_dict(item["M"])
                    if "M" in item
                    else (int(item["N"]) if "N" in item else item["S"])
                )
                for item in v["L"]
            ]
        elif "N" in v:
            result[k] = int(v["N"]) if v["N"].isdigit() else Decimal(v["N"])
        elif "S" in v:
            result[k] = v["S"]
        # Handle NULL, BOOL, etc. if needed

    return result

from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from decimal import Decimal

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


class ReceiptLineItemAnalysis:
    """
    Represents a receipt line item analysis stored in a DynamoDB table.

    This class encapsulates the results of line item analysis for a receipt, 
    including individual line items identified, financial totals (subtotal, tax, total),
    and detailed reasoning explaining how the analysis was performed.

    Attributes:
        image_id (str): UUID identifying the image the receipt is from.
        receipt_id (str): UUID identifying the receipt.
        timestamp_added (datetime or str): The timestamp when the analysis was added.
        items (List[Dict]): List of line items identified in the receipt.
        reasoning (str): Detailed reasoning explaining how the analysis was performed.
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
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: str,
        timestamp_added: Union[datetime, str],
        items: List[Dict],
        reasoning: str,
        version: str,
        subtotal: Optional[Union[Decimal, str]] = None,
        tax: Optional[Union[Decimal, str]] = None,
        total: Optional[Union[Decimal, str]] = None,
        fees: Optional[Union[Decimal, str]] = None,
        discounts: Optional[Union[Decimal, str]] = None,
        tips: Optional[Union[Decimal, str]] = None,
        total_found: Optional[int] = None,
        discrepancies: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ):
        """Initializes a new ReceiptLineItemAnalysis object for DynamoDB.

        Args:
            image_id (str): UUID identifying the image the receipt is from.
            receipt_id (int): Identifier for the receipt.
            timestamp_added (Union[datetime, str]): The timestamp when the analysis was added.
            items (List[Dict]): List of line items identified in the receipt.
            reasoning (str): Detailed reasoning explaining how the analysis was performed.
            version (str): Version of the analysis.
            subtotal (Optional[Union[Decimal, str]], optional): The subtotal amount. Defaults to None.
            tax (Optional[Union[Decimal, str]], optional): The tax amount. Defaults to None.
            total (Optional[Union[Decimal, str]], optional): The total amount. Defaults to None.
            fees (Optional[Union[Decimal, str]], optional): Any fees on the receipt. Defaults to None.
            discounts (Optional[Union[Decimal, str]], optional): Any discounts on the receipt. Defaults to None.
            tips (Optional[Union[Decimal, str]], optional): Any tips on the receipt. Defaults to None.
            total_found (Optional[int], optional): The number of line items found. Defaults to None.
            discrepancies (Optional[List[str]], optional): Any discrepancies found during analysis. Defaults to None.
            metadata (Optional[Dict], optional): Additional metadata about the analysis. Defaults to None.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id

        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError(
                "timestamp_added must be a datetime object or a string"
            )

        # Validate and process items
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        self.items = items

        if not isinstance(reasoning, str):
            raise ValueError("reasoning must be a string")
        self.reasoning = reasoning

        if not isinstance(version, str):
            raise ValueError("version must be a string")
        self.version = version

        # Process financial amounts (can be Decimal or string)
        self.subtotal = self._process_decimal(subtotal, "subtotal")
        self.tax = self._process_decimal(tax, "tax")
        self.total = self._process_decimal(total, "total")
        self.fees = self._process_decimal(fees, "fees")
        self.discounts = self._process_decimal(discounts, "discounts")
        self.tips = self._process_decimal(tips, "tips")

        # Process total_found
        if total_found is not None:
            if not isinstance(total_found, int) or total_found < 0:
                raise ValueError("total_found must be a non-negative integer")
            self.total_found = total_found
        else:
            self.total_found = len(items)

        # Process discrepancies
        if discrepancies is not None:
            if not isinstance(discrepancies, list):
                raise ValueError("discrepancies must be a list")
            self.discrepancies = discrepancies
        else:
            self.discrepancies = []

        # Process metadata
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a dictionary")
            self.metadata = metadata
        else:
            self.metadata = {}

    def _process_decimal(self, value, field_name):
        """Process a value that could be a Decimal, string, or None."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str):
            try:
                return Decimal(value)
            except:
                raise ValueError(f"{field_name} string must be convertible to Decimal")
        raise ValueError(f"{field_name} must be a Decimal, string, or None")

    def key(self) -> dict:
        """Generates the primary key for the receipt line item analysis.

        Returns:
            dict: The primary key for the receipt line item analysis.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id}#ANALYSIS#LINE_ITEMS"},
        }

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the receipt line item analysis.

        Returns:
            dict: The GSI1 key for the receipt line item analysis.
        """
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"LINE_ITEMS#{self.timestamp_added}"},
        }

    def gsi2_key(self) -> dict:
        """Generates the GSI2 key for the receipt line item analysis.

        Returns:
            dict: The GSI2 key for the receipt line item analysis.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {"S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id}"},
        }

    def to_item(self) -> dict:
        """Converts the ReceiptLineItemAnalysis object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLineItemAnalysis object as a DynamoDB item.
        """
        item = {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_LINE_ITEM_ANALYSIS"},
            "version": {"S": self.version},
            "timestamp_added": {"S": self.timestamp_added},
            "reasoning": {"S": self.reasoning},
            "total_found": {"N": str(self.total_found)},
        }

        # Convert items list to DynamoDB format
        item["items"] = {"L": [self._convert_item_to_dynamo(i) for i in self.items]}

        # Convert discrepancies list to DynamoDB format
        item["discrepancies"] = {
            "L": [{"S": discrepancy} for discrepancy in self.discrepancies]
        }

        # Add financial fields if present
        if self.subtotal is not None:
            item["subtotal"] = {"S": str(self.subtotal)}
        else:
            item["subtotal"] = {"NULL": True}

        if self.tax is not None:
            item["tax"] = {"S": str(self.tax)}
        else:
            item["tax"] = {"NULL": True}

        if self.total is not None:
            item["total"] = {"S": str(self.total)}
        else:
            item["total"] = {"NULL": True}

        if self.fees is not None:
            item["fees"] = {"S": str(self.fees)}
        else:
            item["fees"] = {"NULL": True}

        if self.discounts is not None:
            item["discounts"] = {"S": str(self.discounts)}
        else:
            item["discounts"] = {"NULL": True}

        if self.tips is not None:
            item["tips"] = {"S": str(self.tips)}
        else:
            item["tips"] = {"NULL": True}

        # Add metadata if present
        if self.metadata:
            item["metadata"] = {"M": self._convert_dict_to_dynamo(self.metadata)}
        else:
            item["metadata"] = {"NULL": True}

        return item

    def _convert_item_to_dynamo(self, item: Dict) -> Dict:
        """Converts a line item dictionary to DynamoDB format.

        Args:
            item (Dict): The line item dictionary.

        Returns:
            Dict: The line item in DynamoDB format.
        """
        result = {"M": {}}
        
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
            quantity_map = {"M": {}}
            
            if "amount" in quantity:
                quantity_map["M"]["amount"] = {"S": str(quantity["amount"])}
            
            if "unit" in quantity:
                quantity_map["M"]["unit"] = {"S": quantity["unit"]}
            
            result["M"]["quantity"] = quantity_map
        
        # Convert price if present
        if "price" in item and item["price"]:
            price = item["price"]
            price_map = {"M": {}}
            
            if "unit_price" in price and price["unit_price"] is not None:
                price_map["M"]["unit_price"] = {"S": str(price["unit_price"])}
            
            if "extended_price" in price and price["extended_price"] is not None:
                price_map["M"]["extended_price"] = {"S": str(price["extended_price"])}
            
            result["M"]["price"] = price_map
        
        # Add metadata if present
        if "metadata" in item and item["metadata"]:
            result["M"]["metadata"] = {"M": self._convert_dict_to_dynamo(item["metadata"])}
        
        return result

    def _convert_dict_to_dynamo(self, d: Dict) -> Dict:
        """Recursively converts a dictionary to DynamoDB format.

        Args:
            d (Dict): The dictionary to convert.

        Returns:
            Dict: The dictionary in DynamoDB format.
        """
        result = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = {"M": self._convert_dict_to_dynamo(v)}
            elif isinstance(v, list):
                result[k] = {
                    "L": [
                        {"M": self._convert_dict_to_dynamo(item)} if isinstance(item, dict)
                        else {"S": str(item)}
                        for item in v
                    ]
                }
            elif isinstance(v, (int, float)):
                result[k] = {"N": str(v)}
            elif v is None:
                result[k] = {"NULL": True}
            else:
                result[k] = {"S": str(v)}
        return result

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptLineItemAnalysis object.

        Returns:
            str: A string representation of the ReceiptLineItemAnalysis object.
        """
        return (
            "ReceiptLineItemAnalysis("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={_repr_str(self.receipt_id)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}, "
            f"items=[{len(self.items)} items], "
            f"subtotal={self.subtotal}, "
            f"tax={self.tax}, "
            f"total={self.total}, "
            f"total_found={self.total_found}, "
            f"version={_repr_str(self.version)}, "
            f"reasoning='{self.reasoning[:30]}...'"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the ReceiptLineItemAnalysis object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the ReceiptLineItemAnalysis object's attribute name/value pairs.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "timestamp_added", self.timestamp_added
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

    def __eq__(self, other) -> bool:
        """Determines whether two ReceiptLineItemAnalysis objects are equal.

        Args:
            other (ReceiptLineItemAnalysis): The other ReceiptLineItemAnalysis object to compare.

        Returns:
            bool: True if the ReceiptLineItemAnalysis objects are equal, False otherwise.

        Note:
            If other is not an instance of ReceiptLineItemAnalysis, False is returned.
        """
        if not isinstance(other, ReceiptLineItemAnalysis):
            return False
        return (
            self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.timestamp_added == other.timestamp_added
            and self.items == other.items
            and self.reasoning == other.reasoning
            and self.subtotal == other.subtotal
            and self.tax == other.tax
            and self.total == other.total
            and self.fees == other.fees
            and self.discounts == other.discounts
            and self.tips == other.tips
            and self.total_found == other.total_found
            and self.discrepancies == other.discrepancies
            and self.version == other.version
            and self.metadata == other.metadata
        )

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
            )
        )


def itemToReceiptLineItemAnalysis(item: dict) -> ReceiptLineItemAnalysis:
    """Converts a DynamoDB item to a ReceiptLineItemAnalysis object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptLineItemAnalysis: The ReceiptLineItemAnalysis object represented by the DynamoDB item.

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
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        # Extract primary identifiers
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = item["SK"]["S"].split("#")[1]
        
        # Extract required fields
        timestamp_added = item["timestamp_added"]["S"]
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
        metadata = _convert_dynamo_to_dict(item.get("metadata", {}).get("M", {})) if "metadata" in item and item["metadata"].get("M") else None
        
        return ReceiptLineItemAnalysis(
            image_id=image_id,
            receipt_id=receipt_id,
            timestamp_added=timestamp_added,
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
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to ReceiptLineItemAnalysis: {e}")


def _convert_dynamo_to_item(dynamo_item: Dict) -> Dict:
    """Converts a DynamoDB format item to a Python dictionary.

    Args:
        dynamo_item (Dict): The DynamoDB format item.

    Returns:
        Dict: The item as a Python dictionary.
    """
    item = {}
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
            item["price"]["extended_price"] = Decimal(price_map["extended_price"]["S"])
    
    # Extract metadata
    if "metadata" in item_map and "M" in item_map["metadata"]:
        item["metadata"] = _convert_dynamo_to_dict(item_map["metadata"]["M"])
    
    return item


def _convert_dynamo_to_dict(dynamo_dict: Dict) -> Dict:
    """Recursively converts a DynamoDB format dictionary to a Python dictionary.

    Args:
        dynamo_dict (Dict): The DynamoDB format dictionary.

    Returns:
        Dict: The dictionary as a Python dictionary.
    """
    result = {}
    for k, v in dynamo_dict.items():
        if "M" in v:
            result[k] = _convert_dynamo_to_dict(v["M"])
        elif "L" in v:
            result[k] = [
                _convert_dynamo_to_dict(item["M"]) if "M" in item 
                else (int(item["N"]) if "N" in item else item["S"])
                for item in v["L"]
            ]
        elif "N" in v:
            result[k] = int(v["N"]) if v["N"].isdigit() else Decimal(v["N"])
        elif "S" in v:
            result[k] = v["S"]
        # Handle NULL, BOOL, etc. if needed
    
    return result

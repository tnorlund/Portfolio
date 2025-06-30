from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.entities.receipt_line_item_analysis import (
    ReceiptLineItemAnalysis,
)

from .metadata import MetadataMixin


@dataclass
class Price:
    """Price information for a line item."""

    unit_price: Optional[Decimal] = None
    extended_price: Optional[Decimal] = None


@dataclass
class Quantity:
    """Quantity information for a line item."""

    amount: Decimal
    unit: str = "each"


@dataclass
class ItemModifier:
    """Modifier information for a line item."""

    type: str  # e.g., "discount", "special_price", "void"
    description: str
    amount: Optional[Decimal] = None
    percentage: Optional[Decimal] = None


@dataclass
class LineItem:
    """A single line item from a receipt."""

    description: str
    quantity: Optional[Quantity] = None
    price: Optional[Price] = None
    reasoning: str = ""
    line_ids: List[int] = None
    metadata: Dict = None

    def __post_init__(self):
        if self.line_ids is None:
            self.line_ids = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class LineItemAnalysis(MetadataMixin):
    """
    Analysis results for line items in a receipt, with detailed reasoning.

    This class stores the complete line item analysis including all items found,
    financial totals (subtotal, tax, total, fees, discounts, tips),
    discrepancies detected during analysis, and detailed reasoning explaining
    how items were identified and calculations were performed.

    The reasoning field provides a human-readable explanation of the analysis process
    instead of relying on numerical confidence scores.
    """

    items: List[LineItem]
    total_found: int = 0
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None
    fees: Optional[Decimal] = None
    discounts: Optional[Decimal] = None
    tips: Optional[Decimal] = None
    discrepancies: List[str] = None
    reasoning: str = ""
    word_labels: Dict[Tuple[int, int], Dict[str, Any]] = (
        None  # Maps (line_id, word_id) to label info
    )
    metadata: Dict = field(default_factory=dict)
    timestamp_added: Optional[str] = None
    timestamp_updated: Optional[str] = None
    image_id: Optional[str] = None
    receipt_id: Optional[int] = None
    version: str = "1.0.0"

    def __post_init__(self):
        if self.discrepancies is None:
            self.discrepancies = []
        if self.total_found == 0:
            self.total_found = len(self.items)
        # Calculate subtotal if not provided
        if self.subtotal is None and self.items:
            self.subtotal = sum(
                (
                    item.price.extended_price
                    for item in self.items
                    if item.price and item.price.extended_price is not None
                ),
                Decimal("0"),
            )
        # Initialize optional financial fields if not provided
        if self.tax is None:
            self.tax = Decimal("0")
        if self.fees is None:
            self.fees = Decimal("0")
        if self.discounts is None:
            self.discounts = Decimal("0")
        if self.tips is None:
            self.tips = Decimal("0")
        # Calculate total if not provided
        if self.total is None and self.subtotal is not None:
            self.total = (
                self.subtotal + self.tax + self.fees - self.discounts + self.tips
            )

        # Initialize word_labels if None
        if self.word_labels is None:
            self.word_labels = {}

        # Initialize metadata if None
        if self.metadata is None:
            self.metadata = {}

        # If no reasoning is provided, generate a basic one
        if not self.reasoning:
            self.reasoning = self.generate_reasoning()

        # Initialize metadata
        self.initialize_metadata()

        # Add line item specific metrics
        self.add_processing_metric("item_count", self.total_found)
        self.add_processing_metric(
            "financial_totals",
            {
                "subtotal": str(self.subtotal) if self.subtotal else "0",
                "tax": str(self.tax) if self.tax else "0",
                "fees": str(self.fees) if self.fees else "0",
                "discounts": str(self.discounts) if self.discounts else "0",
                "tips": str(self.tips) if self.tips else "0",
                "total": str(self.total) if self.total else "0",
            },
        )

        # Add labeled word metrics if any
        if self.word_labels:
            self.add_processing_metric("labeled_words", len(self.word_labels))
            label_counts = {}
            for label_info in self.word_labels.values():
                label_type = label_info.get("label")
                if label_type:
                    label_counts[label_type] = label_counts.get(label_type, 0) + 1
            self.add_processing_metric("label_distribution", label_counts)

        # If there are discrepancies, add to history
        if self.discrepancies:
            self.add_history_event(
                "discrepancies_detected",
                {
                    "count": len(self.discrepancies),
                    "details": self.discrepancies[
                        :3
                    ],  # Just include first few for brevity
                },
            )

    def generate_reasoning(self) -> str:
        """
        Generate a comprehensive reasoning explanation for the line item analysis.

        Returns:
            str: A detailed explanation of how items were identified and calculations performed.
        """
        reasoning_parts = [f"Analyzed {self.total_found} line items from the receipt."]

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
            reasoning_parts.append("Financial summary: " + ", ".join(financial_parts))

        # Add discrepancies if any
        if self.discrepancies:
            reasoning_parts.append(
                "Discrepancies found: " + "; ".join(self.discrepancies)
            )

        # Add item reasoning summary
        item_reasons = [item.reasoning for item in self.items if item.reasoning]
        if item_reasons:
            # Just include a summary count to avoid extremely long reasoning strings
            reasoning_parts.append(
                f"Detailed reasoning provided for {len(item_reasons)} individual line items."
            )

        return " ".join(reasoning_parts)

    def get_item_by_description(self, description: str) -> Optional[LineItem]:
        """
        Find a line item by its description.

        Args:
            description (str): The description to search for.

        Returns:
            Optional[LineItem]: The matching line item, or None if not found.
        """
        for item in self.items:
            if item.description.lower() == description.lower():
                return item
        return None

    def to_dynamo(self) -> Optional[ReceiptLineItemAnalysis]:
        """
        Convert this LineItemAnalysis to a DynamoDB ReceiptLineItemAnalysis entity.

        Returns:
            ReceiptLineItemAnalysis: A DynamoDB entity object

        Raises:
            ValueError: If receipt_id or image_id are not set
        """
        # Check if required fields are set
        if self.receipt_id is None:
            raise ValueError("receipt_id must be set before calling to_dynamo()")

        if self.image_id is None:
            raise ValueError("image_id must be set before calling to_dynamo()")

        # Convert items to format expected by ReceiptLineItemAnalysis
        dynamo_items = []
        for item in self.items:
            item_dict = {
                "description": item.description,
                "reasoning": item.reasoning,
                "line_ids": item.line_ids,
                "metadata": item.metadata,
            }

            # Add price if available
            if item.price:
                item_dict["price"] = {
                    "unit_price": item.price.unit_price,
                    "extended_price": item.price.extended_price,
                }

            # Add quantity if available
            if item.quantity:
                item_dict["quantity"] = {
                    "amount": item.quantity.amount,
                    "unit": item.quantity.unit,
                }

            dynamo_items.append(item_dict)

        # Parse timestamps if they are strings
        timestamp_added = None
        if self.timestamp_added:
            if isinstance(self.timestamp_added, str):
                try:
                    timestamp_added = datetime.fromisoformat(self.timestamp_added)
                except ValueError:
                    timestamp_added = datetime.now()
            else:
                timestamp_added = self.timestamp_added

        timestamp_updated = None
        if self.timestamp_updated:
            if isinstance(self.timestamp_updated, str):
                try:
                    timestamp_updated = datetime.fromisoformat(self.timestamp_updated)
                except ValueError:
                    timestamp_updated = datetime.now()
            else:
                timestamp_updated = self.timestamp_updated

        # Extract processing metrics and source info from metadata if present
        processing_metrics = self.metadata.get("processing_metrics", {})
        processing_history = self.metadata.get("processing_history", [])
        source_information = self.metadata.get("source_information", {})

        # Create metadata without the extracted fields to avoid duplication
        metadata = {
            k: v
            for k, v in self.metadata.items()
            if k
            not in [
                "processing_metrics",
                "processing_history",
                "source_information",
            ]
        }

        # Create the DynamoDB ReceiptLineItemAnalysis
        return ReceiptLineItemAnalysis(
            image_id=self.image_id,
            receipt_id=self.receipt_id,
            timestamp_added=timestamp_added or datetime.now(),
            timestamp_updated=timestamp_updated,
            items=dynamo_items,
            reasoning=self.reasoning,
            version=self.version,
            subtotal=self.subtotal,
            tax=self.tax,
            total=self.total,
            fees=self.fees,
            discounts=self.discounts,
            tips=self.tips,
            total_found=self.total_found,
            discrepancies=self.discrepancies,
            metadata={
                "processing_metrics": processing_metrics,
                "processing_history": processing_history,
                "source_information": source_information,
                **metadata,
            },
            word_labels=self.word_labels,
        )

    @classmethod
    def from_dynamo(cls, analysis: ReceiptLineItemAnalysis) -> "LineItemAnalysis":
        """
        Create a LineItemAnalysis instance from a DynamoDB ReceiptLineItemAnalysis entity.

        Args:
            analysis: A ReceiptLineItemAnalysis object from the DynamoDB entities

        Returns:
            LineItemAnalysis: A new instance populated with the data from the DynamoDB entity
        """
        # Process items from the DynamoDB entity
        items = []
        for item_dict in analysis.items:
            # Create a LineItem for each item
            line_item = LineItem(
                description=item_dict.get("description", ""),
                reasoning=item_dict.get("reasoning", ""),
                line_ids=item_dict.get("line_ids", []),
                metadata=item_dict.get("metadata", {}),
            )

            # Add price if available
            price_data = item_dict.get("price")
            if price_data:
                line_item.price = Price(
                    unit_price=price_data.get("unit_price"),
                    extended_price=price_data.get("extended_price"),
                )

            # Add quantity if available
            quantity_data = item_dict.get("quantity")
            if quantity_data:
                line_item.quantity = Quantity(
                    amount=quantity_data.get("amount"),
                    unit=quantity_data.get("unit", "each"),
                )

            items.append(line_item)

        # Create instance but bypass normal initialization to preserve timestamps
        instance = cls.__new__(cls)

        # Set attributes manually
        instance.items = items
        instance.total_found = analysis.total_found
        instance.subtotal = analysis.subtotal
        instance.tax = analysis.tax
        instance.total = analysis.total
        instance.fees = analysis.fees
        instance.discounts = analysis.discounts
        instance.tips = analysis.tips
        instance.discrepancies = analysis.discrepancies
        instance.reasoning = analysis.reasoning
        instance.word_labels = analysis.word_labels
        instance.image_id = analysis.image_id
        instance.receipt_id = analysis.receipt_id
        instance.version = analysis.version
        instance.timestamp_added = (
            analysis.timestamp_added
            if isinstance(analysis.timestamp_added, str)
            else (
                analysis.timestamp_added.isoformat()
                if analysis.timestamp_added
                else None
            )
        )
        instance.timestamp_updated = (
            analysis.timestamp_updated
            if isinstance(analysis.timestamp_updated, str)
            else (
                analysis.timestamp_updated.isoformat()
                if analysis.timestamp_updated
                else None
            )
        )

        # Create metadata from the DynamoDB entity's fields
        instance.metadata = {
            **analysis.metadata,
            "processing_metrics": analysis.metadata.get("processing_metrics", {}),
            "processing_history": analysis.metadata.get("processing_history", []),
            "source_information": analysis.metadata.get("source_information", {}),
        }

        # Minimal initialization to avoid overriding timestamps
        instance._initialize_from_dynamo()

        return instance

    def _initialize_from_dynamo(self):
        """
        Perform minimal initialization when creating from DynamoDB data.
        This avoids overriding timestamps that came from the database.
        """
        # Ensure basic metadata structure exists
        if "processing_metrics" not in self.metadata:
            self.metadata["processing_metrics"] = {}

        if "source_information" not in self.metadata:
            self.metadata["source_information"] = {}

        if "processing_history" not in self.metadata:
            self.metadata["processing_history"] = []

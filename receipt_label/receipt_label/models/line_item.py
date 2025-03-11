from dataclasses import dataclass, field
from typing import List, Optional, Dict
from decimal import Decimal
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
    metadata: Dict = field(default_factory=dict)
    timestamp_added: Optional[str] = None
    timestamp_updated: Optional[str] = None

    def __post_init__(self):
        if self.discrepancies is None:
            self.discrepancies = []
        if self.total_found == 0:
            self.total_found = len(self.items)
        # Calculate subtotal if not provided
        if self.subtotal is None and self.items:
            self.subtotal = sum(
                (item.price.extended_price for item in self.items 
                if item.price and item.price.extended_price is not None),
                Decimal('0')
            )
        # Initialize optional financial fields if not provided
        if self.tax is None:
            self.tax = Decimal('0')
        if self.fees is None:
            self.fees = Decimal('0')
        if self.discounts is None:
            self.discounts = Decimal('0')
        if self.tips is None:
            self.tips = Decimal('0')
        # Calculate total if not provided
        if self.total is None and self.subtotal is not None:
            self.total = self.subtotal + self.tax + self.fees - self.discounts + self.tips
        
        # If no reasoning is provided, generate a basic one
        if not self.reasoning:
            self.reasoning = self.generate_reasoning()
            
        # Initialize metadata
        self.initialize_metadata()
        
        # Add line item specific metrics
        self.add_processing_metric("item_count", self.total_found)
        self.add_processing_metric("financial_totals", {
            "subtotal": str(self.subtotal) if self.subtotal else "0",
            "tax": str(self.tax) if self.tax else "0",
            "fees": str(self.fees) if self.fees else "0",
            "discounts": str(self.discounts) if self.discounts else "0",
            "tips": str(self.tips) if self.tips else "0",
            "total": str(self.total) if self.total else "0"
        })
        
        # If there are discrepancies, add to history
        if self.discrepancies:
            self.add_history_event("discrepancies_detected", {
                "count": len(self.discrepancies),
                "details": self.discrepancies[:3]  # Just include first few for brevity
            })
    
    def generate_reasoning(self) -> str:
        """
        Generate a comprehensive reasoning explanation for the line item analysis.
        
        Returns:
            str: A detailed explanation of how items were identified and calculations performed.
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
            reasoning_parts.append("Financial summary: " + ", ".join(financial_parts))
        
        # Add discrepancies if any
        if self.discrepancies:
            reasoning_parts.append("Discrepancies found: " + "; ".join(self.discrepancies))
        
        # Add item reasoning summary
        item_reasons = [item.reasoning for item in self.items if item.reasoning]
        if item_reasons:
            # Just include a summary count to avoid extremely long reasoning strings
            reasoning_parts.append(f"Detailed reasoning provided for {len(item_reasons)} individual line items.")
        
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

    def to_dynamo(self) -> Dict:
        """
        Convert the LineItemAnalysis to a DynamoDB-compatible dictionary.
        
        Returns:
            Dict: A dictionary representation for DynamoDB
        """
        # Get base metadata fields
        result = super().to_dict()
        
        # Add class-specific fields
        result.update({
            "items": [item.__dict__ for item in self.items],
            "total_found": self.total_found,
            "subtotal": str(self.subtotal) if self.subtotal else None,
            "tax": str(self.tax) if self.tax else None,
            "total": str(self.total) if self.total else None,
            "fees": str(self.fees) if self.fees else None,
            "discounts": str(self.discounts) if self.discounts else None,
            "tips": str(self.tips) if self.tips else None,
            "discrepancies": self.discrepancies,
            "reasoning": self.reasoning
        })
        
        return result
    
    @classmethod
    def from_dynamo(cls, data: Dict) -> "LineItemAnalysis":
        """
        Create a LineItemAnalysis instance from DynamoDB data.
        
        Args:
            data (Dict): Data from DynamoDB
            
        Returns:
            LineItemAnalysis: A new instance populated with the DynamoDB data
        """
        # Extract metadata fields
        metadata_fields = MetadataMixin.from_dict(data)
        
        # Process decimal values
        financial_fields = {}
        for field in ["subtotal", "tax", "total", "fees", "discounts", "tips"]:
            if data.get(field):
                financial_fields[field] = Decimal(data.get(field))
        
        # Process items
        items = []
        for item_data in data.get("items", []):
            item = LineItem(
                description=item_data.get("description", ""),
                reasoning=item_data.get("reasoning", ""),
                line_ids=item_data.get("line_ids", []),
                metadata=item_data.get("metadata", {})
            )
            
            # Add price if available
            price_data = item_data.get("price")
            if price_data:
                item.price = Price(
                    unit_price=Decimal(price_data.get("unit_price")) if price_data.get("unit_price") else None,
                    extended_price=Decimal(price_data.get("extended_price")) if price_data.get("extended_price") else None
                )
                
            # Add quantity if available
            quantity_data = item_data.get("quantity")
            if quantity_data:
                item.quantity = Quantity(
                    amount=Decimal(quantity_data.get("amount")),
                    unit=quantity_data.get("unit", "each")
                )
                
            items.append(item)
            
        return cls(
            items=items,
            total_found=data.get("total_found", len(items)),
            discrepancies=data.get("discrepancies", []),
            reasoning=data.get("reasoning", ""),
            **financial_fields,
            **metadata_fields
        ) 
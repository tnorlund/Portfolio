from dataclasses import dataclass
from typing import List, Optional, Dict
from decimal import Decimal


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
class LineItemAnalysis:
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
    metadata: Dict = None

    def __post_init__(self):
        if self.discrepancies is None:
            self.discrepancies = []
        if self.metadata is None:
            self.metadata = {}
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
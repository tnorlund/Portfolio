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
    confidence: float = 0.0
    line_ids: List[int] = None
    metadata: Dict = None

    def __post_init__(self):
        if self.line_ids is None:
            self.line_ids = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class LineItemAnalysis:
    """Analysis results for line items in a receipt."""
    items: List[LineItem]
    total_found: int = 0
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None
    discrepancies: List[str] = None
    confidence: float = 0.0
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
        # Initialize tax if not provided
        if self.tax is None:
            self.tax = Decimal('0')
        # Calculate total if not provided
        if self.total is None and self.subtotal is not None:
            self.total = self.subtotal + self.tax 
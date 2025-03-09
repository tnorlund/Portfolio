from typing import Dict, List, Union, Literal, Any
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from .receipt import ReceiptLine

def ensure_decimal(value: Any) -> Decimal:
    """Convert a value to Decimal, handling various input types."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            # Remove any currency symbols and commas
            clean_value = value.replace('$', '').replace(',', '').strip()
            return Decimal(clean_value)
        except InvalidOperation as e:
            raise ValueError(f"Could not convert '{value}' to Decimal: {str(e)}")
    raise ValueError(f"Cannot convert type {type(value)} to Decimal")

def ensure_decimal_list(values: List[Any]) -> List[Decimal]:
    """Convert a list of values to Decimals."""
    return [ensure_decimal(v) for v in values]

@dataclass
class MultipleAmountsUncertainty:
    """Represents a line with multiple currency amounts that needs resolution."""
    line: ReceiptLine
    amounts: List[Any]
    reason: Literal["multiple_amounts"] = "multiple_amounts"

    def __post_init__(self):
        """Validate and convert amounts to Decimals."""
        if not isinstance(self.line, ReceiptLine):
            raise ValueError(f"line must be a ReceiptLine object, got {type(self.line)}")
        
        if not isinstance(self.amounts, list):
            raise ValueError(f"amounts must be a list, got {type(self.amounts)}")
        try:
            self.amounts = ensure_decimal_list(self.amounts)
        except ValueError as e:
            raise ValueError(f"Invalid amount in amounts list: {str(e)}")

    def to_dict(self) -> Dict:
        """Convert to dictionary format."""
        return {
            "line_id": self.line.line_id,
            "text": self.line.text,
            "amounts": [str(amount) for amount in self.amounts],
            "reason": self.reason
        }

@dataclass
class MissingComponentUncertainty:
    """Represents a missing required component in the receipt."""
    component: Literal["subtotal", "tax", "total"]
    reason: Literal["missing_component"] = "missing_component"

    def __post_init__(self):
        """Validate component type."""
        valid_components = {"subtotal", "tax", "total"}
        if self.component not in valid_components:
            raise ValueError(f"component must be one of {valid_components}, got {self.component}")

    def to_dict(self) -> Dict:
        """Convert to dictionary format."""
        return {
            "component": self.component,
            "reason": self.reason
        }

@dataclass
class TotalMismatchUncertainty:
    """Represents a mismatch between calculated and found totals."""
    calculated: Any
    found: Any
    reason: Literal["total_mismatch"] = "total_mismatch"

    def __post_init__(self):
        """Validate and convert amounts to Decimals."""
        try:
            self.calculated = ensure_decimal(self.calculated)
            self.found = ensure_decimal(self.found)
        except ValueError as e:
            raise ValueError(f"Invalid amount: {str(e)}")

    def to_dict(self) -> Dict:
        """Convert to dictionary format."""
        return {
            "calculated": str(self.calculated),
            "found": str(self.found),
            "reason": self.reason
        }

# Type alias for all possible uncertainty types
UncertaintyItem = Union[MultipleAmountsUncertainty, MissingComponentUncertainty, TotalMismatchUncertainty] 
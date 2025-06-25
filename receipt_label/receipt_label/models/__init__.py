"""Data models for receipt labeling."""

from .label import FieldGroup, LabelAnalysis, SectionLabels, WordLabel
from .line_item import LineItem, LineItemAnalysis, Price, Quantity
from .receipt import Receipt, ReceiptSection, ReceiptWord
from .structure import (
    ContentPattern,
)
from .structure import ReceiptSection as StructureSection
from .structure import (
    SpatialPattern,
    StructureAnalysis,
)
from .validation import (
    FieldValidation,
    ValidationAnalysis,
    ValidationResult,
    ValidationResultType,
    ValidationStatus,
)

__all__ = [
    "Receipt",
    "ReceiptWord",
    "ReceiptSection",
    "LineItem",
    "LineItemAnalysis",
    "Price",
    "Quantity",
    "WordLabel",
    "FieldGroup",
    "SectionLabels",
    "LabelAnalysis",
    "SpatialPattern",
    "ContentPattern",
    "StructureSection",
    "StructureAnalysis",
    "ValidationAnalysis",
    "ValidationResult",
    "FieldValidation",
    "ValidationStatus",
    "ValidationResultType",
]

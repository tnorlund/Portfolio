"""Data models for receipt labeling."""

from .receipt import Receipt, ReceiptWord, ReceiptSection
from .line_item import LineItem, LineItemAnalysis, Price, Quantity
from .label import WordLabel, FieldGroup, SectionLabels, LabelAnalysis
from .structure import (
    SpatialPattern,
    ContentPattern,
    ReceiptSection as StructureSection,
    StructureAnalysis,
)
from .validation import (
    ValidationAnalysis,
    ValidationResult,
    FieldValidation,
    ValidationStatus,
    ValidationResultType,
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

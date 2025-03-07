"""
Receipt Label - A package for labeling and validating receipt data.
"""

from .core.labeler import ReceiptLabeler, LabelingResult
from .core.validator import ReceiptValidator
from .models.receipt import Receipt, ReceiptWord, ReceiptSection
from .processors.gpt import GPTProcessor
from .processors.structure import StructureProcessor
from .processors.field import FieldProcessor
from .data.places_api import BatchPlacesProcessor

__version__ = "0.1.0"

__all__ = [
    "ReceiptLabeler",
    "LabelingResult",
    "ReceiptValidator",
    "Receipt",
    "ReceiptWord",
    "ReceiptSection",
    "GPTProcessor",
    "StructureProcessor",
    "FieldProcessor",
    "BatchPlacesProcessor",
]

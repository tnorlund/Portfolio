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
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set module-specific log levels
logging.getLogger('receipt_label.receipt_label.processors.gpt').setLevel(logging.WARNING)
logging.getLogger('receipt_label.receipt_label.processors.line_item').setLevel(logging.WARNING)
logging.getLogger('receipt_label.receipt_label.core.validator').setLevel(logging.WARNING)
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

# Only show important info logs from main module
logging.getLogger('__main__').setLevel(logging.INFO)

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

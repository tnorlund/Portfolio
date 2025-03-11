"""
Receipt Label - A package for labeling and validating receipt data.
"""

from .core.labeler import ReceiptLabeler, LabelingResult
from .core.validator import ReceiptValidator
from .models.receipt import Receipt, ReceiptWord, ReceiptSection, ReceiptLine
from .models.line_item import LineItem, LineItemAnalysis, Price, Quantity
from .processors.receipt_analyzer import ReceiptAnalyzer
from .data.places_api import BatchPlacesProcessor
from .processors.line_item_processor import LineItemProcessor
from .version import __version__
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set module-specific log levels
logging.getLogger('receipt_label.receipt_label.processors.receipt_analyzer').setLevel(logging.WARNING)
logging.getLogger('receipt_label.receipt_label.core.validator').setLevel(logging.WARNING)
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

# Only show important info logs from main module
logging.getLogger('__main__').setLevel(logging.INFO)

__all__ = [
    "ReceiptLabeler",
    "LabelingResult",
    "ReceiptValidator",
    "Receipt",
    "ReceiptWord",
    "ReceiptSection",
    "ReceiptLine",
    "LineItem",
    "LineItemAnalysis",
    "Price",
    "Quantity",
    "ReceiptAnalyzer",
    "BatchPlacesProcessor",
    "LineItemProcessor",
]

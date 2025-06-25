"""
Receipt Label - A package for labeling and validating receipt data.
"""

import logging

from .core.labeler import LabelingResult, ReceiptLabeler
from .data.places_api import BatchPlacesProcessor
from .models.line_item import LineItem, LineItemAnalysis, Price, Quantity
from .models.receipt import Receipt, ReceiptLine, ReceiptSection, ReceiptWord
from .version import __version__

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set module-specific log levels
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Only show important info logs from main module
logging.getLogger("__main__").setLevel(logging.INFO)

__all__ = [
    "ReceiptLabeler",
    "LabelingResult",
    "Receipt",
    "ReceiptWord",
    "ReceiptSection",
    "ReceiptLine",
    "LineItem",
    "LineItemAnalysis",
    "Price",
    "Quantity",
    "BatchPlacesProcessor",
]

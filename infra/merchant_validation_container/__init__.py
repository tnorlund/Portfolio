"""
Container-based merchant validation Lambda with EFS and NDJSON embedding trigger.

This module provides a Lambda function that:
1. Validates merchant data using direct EFS-backed ChromaDB access
2. Creates ReceiptMetadata with merchant information
3. Triggers NDJSON embedding process automatically
"""

from .infrastructure import (
    MerchantValidationContainer,
    create_merchant_validation_container,
)

__all__ = [
    "MerchantValidationContainer",
    "create_merchant_validation_container",
]


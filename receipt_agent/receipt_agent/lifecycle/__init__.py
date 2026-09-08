"""
Receipt Lifecycle Management

This module provides unified functions for creating and deleting receipts
in DynamoDB. It's designed to be used by:
- Split receipt scripts
- Combine receipt scripts
- Receipt agent workflows
"""

from receipt_agent.lifecycle.ndjson_manager import (
    export_receipt_ndjson,
)
from receipt_agent.lifecycle.receipt_manager import (
    ReceiptCreationResult,
    ReceiptDeletionResult,
    create_receipt,
    delete_receipt,
)

__all__ = [
    "create_receipt",
    "delete_receipt",
    "ReceiptCreationResult",
    "ReceiptDeletionResult",
    "export_receipt_ndjson",
]

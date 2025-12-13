"""
Receipt Lifecycle Management

This module provides unified functions for creating and deleting receipts
across DynamoDB and ChromaDB. It's designed to be used by:
- Split receipt scripts
- Combine receipt scripts
- Receipt agent workflows
"""

from receipt_agent.lifecycle.receipt_manager import (
    create_receipt,
    delete_receipt,
    ReceiptCreationResult,
    ReceiptDeletionResult,
)

from receipt_agent.lifecycle.embedding_manager import (
    create_embeddings,
)

from receipt_agent.lifecycle.compaction_manager import (
    wait_for_compaction,
    check_compaction_status,
)

from receipt_agent.lifecycle.ndjson_manager import (
    export_receipt_ndjson,
)

__all__ = [
    "create_receipt",
    "delete_receipt",
    "ReceiptCreationResult",
    "ReceiptDeletionResult",
    "create_embeddings",
    "wait_for_compaction",
    "check_compaction_status",
    "export_receipt_ndjson",
]


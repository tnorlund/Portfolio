"""LangSmith API client and bulk export manager.

This module provides:
- LangSmithClient: Async/sync API client with retry logic
- BulkExportManager: Bulk export operations
"""

from receipt_langsmith.client.api import LangSmithClient
from receipt_langsmith.client.export import (
    BulkExportManager,
    ExportJob,
)
from receipt_langsmith.client.models import (
    BulkExportDestination,
    BulkExportRequest,
    BulkExportResponse,
    ExportStatus,
    Project,
)
from receipt_langsmith.exceptions import (
    BulkExportError,
    BulkExportResponseError,
    BulkExportTimeoutError,
    LangSmithAPIError,
    LangSmithConfigurationError,
    LangSmithRequestError,
    LangSmithResponseError,
    LangSmithTransportError,
    ReceiptLangSmithError,
)

# Public re-export list is intentionally duplicated to keep a stable API.
# pylint: disable=duplicate-code
__all__ = [
    "LangSmithClient",
    "BulkExportManager",
    "ExportJob",
    "ExportStatus",
    "BulkExportDestination",
    "BulkExportRequest",
    "BulkExportResponse",
    "Project",
    "ReceiptLangSmithError",
    "LangSmithConfigurationError",
    "LangSmithRequestError",
    "LangSmithAPIError",
    "LangSmithTransportError",
    "LangSmithResponseError",
    "BulkExportError",
    "BulkExportResponseError",
    "BulkExportTimeoutError",
]
# pylint: enable=duplicate-code

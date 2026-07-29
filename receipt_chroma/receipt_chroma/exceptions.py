"""Public exception hierarchy for :mod:`receipt_chroma`."""

from __future__ import annotations


class ReceiptChromaError(Exception):
    """Base class for receipt-chroma operational failures."""


class ChromaConfigurationError(ReceiptChromaError, ValueError):
    """Required Chroma service configuration is missing or inconsistent."""


class ChromaClientStateError(ReceiptChromaError, RuntimeError):
    """An operation is incompatible with the client's current state."""


class ChromaClientClosedError(ChromaClientStateError):
    """An operation was attempted after the client was closed."""


class ChromaReadOnlyError(ChromaClientStateError):
    """A write operation was attempted with a read-only client."""


class ChromaCollectionNotFoundError(ReceiptChromaError, ValueError):
    """A requested Chroma collection does not exist."""

    def __init__(self, collection_name: str) -> None:
        self.collection_name = collection_name
        super().__init__(
            f"Collection '{collection_name}' not found and "
            "create_if_missing=False"
        )


class ChromaDeltaUploadError(ReceiptChromaError, RuntimeError):
    """A persisted Chroma delta could not be uploaded or verified."""

    def __init__(
        self,
        message: str,
        *,
        bucket: str | None = None,
        key: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.key = key
        super().__init__(message)


__all__ = [
    "ChromaClientClosedError",
    "ChromaClientStateError",
    "ChromaCollectionNotFoundError",
    "ChromaConfigurationError",
    "ChromaDeltaUploadError",
    "ChromaReadOnlyError",
    "ReceiptChromaError",
]

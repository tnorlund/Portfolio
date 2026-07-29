"""Public exception hierarchy for :mod:`receipt_langsmith`."""

from __future__ import annotations


class ReceiptLangSmithError(Exception):
    """Base class for receipt-langsmith operational failures."""


class LangSmithConfigurationError(ReceiptLangSmithError, ValueError):
    """Required LangSmith configuration is missing or invalid."""


class LangSmithRequestError(ReceiptLangSmithError, RuntimeError):
    """Base class for LangSmith HTTP request failures."""


class LangSmithAPIError(LangSmithRequestError):
    """The LangSmith API returned an unsuccessful HTTP response."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        method: str,
        path: str,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.method = method
        self.path = path
        super().__init__(
            f"LangSmith API {method.upper()} {path} failed with "
            f"status {status_code}: {message}"
        )


class LangSmithTransportError(LangSmithRequestError):
    """A request could not reach or receive a response from LangSmith."""

    def __init__(self, *, method: str, path: str) -> None:
        self.method = method
        self.path = path
        super().__init__(
            f"LangSmith API {method.upper()} {path} transport failed"
        )


class LangSmithResponseError(LangSmithRequestError):
    """LangSmith returned a successful but unusable response payload."""

    def __init__(self, *, method: str, path: str, detail: str) -> None:
        self.method = method
        self.path = path
        self.detail = detail
        super().__init__(
            f"Invalid response from LangSmith API {method.upper()} {path}: "
            f"{detail}"
        )


class BulkExportError(ReceiptLangSmithError, RuntimeError):
    """Base class for LangSmith bulk export failures."""


class BulkExportResponseError(BulkExportError):
    """A bulk export response was missing or contained invalid data."""

    def __init__(self, operation: str, detail: str) -> None:
        self.operation = operation
        self.detail = detail
        super().__init__(f"Invalid bulk export {operation} response: {detail}")


class BulkExportTimeoutError(BulkExportError, TimeoutError):
    """A bulk export did not reach a terminal state before its deadline."""

    def __init__(self, export_id: str, timeout: int) -> None:
        self.export_id = export_id
        self.timeout = timeout
        super().__init__(
            f"Export {export_id} did not complete within {timeout}s"
        )


__all__ = [
    "BulkExportError",
    "BulkExportResponseError",
    "BulkExportTimeoutError",
    "LangSmithAPIError",
    "LangSmithConfigurationError",
    "LangSmithRequestError",
    "LangSmithResponseError",
    "LangSmithTransportError",
    "ReceiptLangSmithError",
]

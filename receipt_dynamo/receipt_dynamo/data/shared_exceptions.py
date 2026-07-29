"""Custom exceptions for :mod:`receipt_dynamo` operations."""

from typing import Any


class ReceiptDynamoError(Exception):
    """Base exception for all receipt_dynamo errors."""


# Broad categories ---------------------------------------------------------
class DynamoDBError(ReceiptDynamoError):
    """Base exception for failures reported by DynamoDB."""


class DynamoRetryableException(DynamoDBError):
    """Base for transient DynamoDB failures that may succeed when retried."""


class DynamoCriticalErrorException(DynamoDBError):
    """Base for non-retryable DynamoDB failures requiring intervention."""


class EntityError(ReceiptDynamoError):
    """Base exception for entity operations."""


class EntityNotFoundError(EntityError, ValueError):
    """Raised when an entity is not found."""


class EntityAlreadyExistsError(EntityError):
    """Raised when attempting to create an entity that already exists."""


class EntityValidationError(EntityError, ValueError):
    """Raised when entity validation fails."""


class OperationError(ReceiptDynamoError):
    """Base exception for operation failures."""


class BatchOperationError(OperationError):
    """Raised when a batch operation leaves items unprocessed."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int | None = None,
        unprocessed_items: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.unprocessed_items = unprocessed_items or {}


class TransactionError(OperationError):
    """Raised when a transaction operation fails."""


class ResilienceError(ReceiptDynamoError):
    """Base exception for retry and circuit-breaker failures."""


class CircuitBreakerOpenError(ResilienceError):
    """Raised when an open circuit breaker blocks an operation."""

    def __init__(
        self, message: str, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class RetryExhaustedError(ResilienceError):
    """Raised when an operation still fails after all retry attempts."""

    def __init__(
        self,
        message: str,
        last_exception: Exception,
        *,
        attempts: int | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts
        self.operation = operation


# DynamoDB failure kinds ---------------------------------------------------
class DynamoDBThroughputError(DynamoRetryableException):
    """Raised when DynamoDB provisioned throughput is exceeded."""


class DynamoDBServerError(DynamoRetryableException):
    """Raised when DynamoDB reports an internal or unavailable service."""


class DynamoDBAccessError(DynamoCriticalErrorException):
    """Raised when credentials are invalid or DynamoDB denies access."""


class DynamoDBResourceNotFoundError(
    DynamoCriticalErrorException, OperationError
):
    """Raised when the requested DynamoDB table or index does not exist.

    ``OperationError`` remains a base class for backward compatibility with
    callers that previously handled this less-specific category.
    """


class DynamoDBValidationError(
    DynamoCriticalErrorException, EntityValidationError
):
    """Raised when DynamoDB rejects a malformed request.

    This is also an ``EntityValidationError`` so existing callers that group
    local and service-side validation failures continue to work.
    """


# Merchant-truth contract failures ----------------------------------------
class MerchantTruthError(ReceiptDynamoError):
    """Base exception for merchant-truth contract violations."""


class MerchantTruthConflictError(MerchantTruthError):
    """Raised when an optimistic or immutable write loses a race."""


class MerchantTruthIntegrityError(MerchantTruthError, ValueError):
    """Raised when a bundle does not match its sealed manifest."""


class MerchantTruthTableMismatchError(MerchantTruthError, ValueError):
    """Raised before a write targets an unexpected table."""


class MerchantTruthPromotionError(MerchantTruthError):
    """Raised when fail-closed promotion cannot preserve the truth closure."""


class GateBridgeError(MerchantTruthError, ValueError):
    """Raised when eval output cannot be adapted into a seal gate signal.

    The eval->seal bridge (contract §7.5) fails closed on structurally
    inconsistent input: an unknown ``overall`` verdict, an
    ``overall == PASS_WITH_GAPS`` carrying an empty gap list, or a non-empty
    gap list masquerading as a plain ``PASS``.
    """


class GateBlockedError(MerchantTruthError):
    """Raised when a failing eval blocks a seal (contract §7.5).

    A ``FAIL`` overall leaves the version OPEN. The gate record written for
    the failing run is the work list for closing it, so the derived
    ``gate_results`` (with the failing gaps) ride along on the exception.
    """

    def __init__(
        self,
        message: str,
        *,
        gate_results: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.gate_results = gate_results or {}

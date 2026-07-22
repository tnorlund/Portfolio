"""Custom exceptions for receipt_dynamo data layer operations."""


class ReceiptDynamoError(Exception):
    """Base exception for all receipt_dynamo errors."""


# DynamoDB specific exceptions
class DynamoDBError(ReceiptDynamoError):
    """Base exception for DynamoDB operations."""


class DynamoRetryableException(DynamoDBError):
    """
    Exception raised for retryable errors in DynamoDB operations.

    This exception should be raised when an operation fails due to a temporary
    issue
    such as a provisioned throughput exceeded error, which could succeed if
    retried later.
    """


class DynamoCriticalErrorException(DynamoDBError):
    """
    Exception raised for critical errors in DynamoDB operations.

    This exception should be raised when an operation fails due to a permanent
    issue
    such as a resource not found or permission denied error, which would not
    succeed
    if retried without addressing the underlying issue.
    """


class DynamoDBThroughputError(DynamoRetryableException):
    """Raised when DynamoDB provisioned throughput is exceeded."""


class DynamoDBServerError(DynamoRetryableException):
    """Raised when DynamoDB has an internal server error."""


class DynamoDBAccessError(DynamoCriticalErrorException):
    """Raised when access to DynamoDB is denied."""


class DynamoDBResourceNotFoundError(DynamoCriticalErrorException):
    """Raised when a DynamoDB resource is not found."""


class DynamoDBValidationError(DynamoCriticalErrorException):
    """Raised when DynamoDB request validation fails."""


# Entity specific exceptions
class EntityError(ReceiptDynamoError):
    """Base exception for entity operations."""


class EntityNotFoundError(EntityError, ValueError):
    """Raised when an entity is not found."""


class EntityAlreadyExistsError(EntityError):
    """Raised when attempting to create an entity that already exists."""


class EntityValidationError(EntityError, ValueError):
    """Raised when entity validation fails."""


# Operation specific exceptions
class OperationError(ReceiptDynamoError):
    """Base exception for operation failures."""


class BatchOperationError(OperationError):
    """Raised when a batch operation fails."""


class TransactionError(OperationError):
    """Raised when a transaction operation fails."""


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

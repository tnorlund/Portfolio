"""Custom exceptions for receipt_dynamo data layer operations."""


class ReceiptDynamoError(Exception):
    """Base exception for all receipt_dynamo errors."""

    pass


# DynamoDB specific exceptions
class DynamoDBError(ReceiptDynamoError):
    """Base exception for DynamoDB operations."""

    pass


class DynamoRetryableException(DynamoDBError):
    """
    Exception raised for retryable errors in DynamoDB operations.

    This exception should be raised when an operation fails due to a temporary
    issue
    such as a provisioned throughput exceeded error, which could succeed if
    retried later.
    """

    pass


class DynamoCriticalErrorException(DynamoDBError):
    """
    Exception raised for critical errors in DynamoDB operations.

    This exception should be raised when an operation fails due to a permanent
    issue
    such as a resource not found or permission denied error, which would not
    succeed
    if retried without addressing the underlying issue.
    """

    pass


class DynamoDBThroughputError(DynamoRetryableException):
    """Raised when DynamoDB provisioned throughput is exceeded."""

    pass


class DynamoDBServerError(DynamoRetryableException):
    """Raised when DynamoDB has an internal server error."""

    pass


class DynamoDBAccessError(DynamoCriticalErrorException):
    """Raised when access to DynamoDB is denied."""

    pass


class DynamoDBResourceNotFoundError(DynamoCriticalErrorException):
    """Raised when a DynamoDB resource is not found."""

    pass


class DynamoDBValidationError(DynamoCriticalErrorException):
    """Raised when DynamoDB request validation fails."""

    pass


# Entity specific exceptions
class EntityError(ReceiptDynamoError):
    """Base exception for entity operations."""

    pass


class EntityNotFoundError(EntityError):
    """Raised when an entity is not found."""

    pass


class EntityAlreadyExistsError(EntityError):
    """Raised when attempting to create an entity that already exists."""

    pass


class EntityValidationError(EntityError):
    """Raised when entity validation fails."""

    pass


# Operation specific exceptions
class OperationError(ReceiptDynamoError):
    """Base exception for operation failures."""

    pass


class BatchOperationError(OperationError):
    """Raised when a batch operation fails."""

    pass


class TransactionError(OperationError):
    """Raised when a transaction operation fails."""

    pass

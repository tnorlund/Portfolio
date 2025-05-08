class DynamoRetryableException(Exception):
    """
    Exception raised for retryable errors in DynamoDB operations.

    This exception should be raised when an operation fails due to a temporary issue
    such as a provisioned throughput exceeded error, which could succeed if retried later.
    """

    # TODO: REMOVE THIS


class DynamoCriticalErrorException(Exception):
    """
    Exception raised for critical errors in DynamoDB operations.

    This exception should be raised when an operation fails due to a permanent issue
    such as a resource not found or permission denied error, which would not succeed
    if retried without addressing the underlying issue. ugly
    """

    # TODO: REMOVE THIS

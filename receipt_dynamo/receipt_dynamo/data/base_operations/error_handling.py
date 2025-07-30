"""
Consolidated error handling for DynamoDB operations.

This module combines error configuration, context extraction, and error
handling into a single cohesive module.
"""

import random
import time
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# =============================================================================
# ERROR CONFIGURATION
# =============================================================================


class ErrorMessageConfig:
    """Configuration for DynamoDB error message patterns and operation
    names."""

    # Entity not found patterns for different operations
    ENTITY_NOT_FOUND_PATTERNS = {
        "get_entity": "Entity with {entity_name}={entity_id} not found",
        "update_entity": (
            "Cannot update {entity_name} with {entity_name}={entity_id}: "
            "entity not found"
        ),
        "delete_entity": (
            "Cannot delete {entity_name} with {entity_name}={entity_id}: "
            "entity not found"
        ),
        "get_receipt": "Receipt with receipt_id={receipt_id} not found",
        "get_image": "Image with image_id={image_id} not found",
        "get_job": "Job with job_id={job_id} not found",
        "get_word": (
            "Word with image_id={image_id}, line_id={line_id}, "
            "word_id={word_id} not found"
        ),
        "get_line": (
            "Line with image_id={image_id}, line_id={line_id} not found"
        ),
    }

    # Operation-specific error messages
    OPERATION_MESSAGES = {
        "add_entity": "Failed to add {entity_name}",
        "update_entity": "Failed to update {entity_name}",
        "delete_entity": "Failed to delete {entity_name}",
        "get_entity": "Failed to retrieve {entity_name}",
        "list_entities": "Failed to list {entity_name} entities",
        "batch_write": "Failed to write batch items",
        "transact_write": "Failed to execute transaction",
        "query_by_type": "Failed to query entities by type",
        "query_entities": "Failed to query entities",
    }


# =============================================================================
# ERROR CONTEXT EXTRACTION
# =============================================================================


class ErrorContextExtractor:
    """Extracts contextual information from DynamoDB ClientError exceptions."""

    @staticmethod
    def extract_context(
        error: ClientError, operation: str, **kwargs
    ) -> Dict[str, Any]:
        """Extract relevant context from a ClientError for debugging."""
        context = {
            "operation": operation,
            "error_code": error.response.get("Error", {}).get(
                "Code", "Unknown"
            ),
            "error_message": error.response.get("Error", {}).get(
                "Message", "Unknown error"
            ),
            "request_id": error.response.get("ResponseMetadata", {}).get(
                "RequestId"
            ),
            "http_status": error.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            ),
        }

        # Add operation-specific context
        if kwargs:
            context.update(kwargs)

        return context


# =============================================================================
# ERROR HANDLERS
# =============================================================================


class ErrorHandler:
    """Handles DynamoDB operation errors with proper exception mapping."""

    def __init__(self, config: Optional[ErrorMessageConfig] = None):
        self.config = config or ErrorMessageConfig()
        self.context_extractor = ErrorContextExtractor()

    def handle_client_error(
        self, error: ClientError, operation: str, **context_kwargs
    ) -> None:
        """Handle ClientError and raise appropriate domain exception."""
        error_code = error.response.get("Error", {}).get("Code", "Unknown")
        error_message = error.response.get("Error", {}).get("Message", "")

        # Extract context for debugging
        self.context_extractor.extract_context(
            error, operation, **context_kwargs
        )

        # Map AWS error codes to domain exceptions
        if error_code == "ConditionalCheckFailedException":
            if "add_" in operation:
                entity_type = operation.replace("add_", "").replace("_", " ")
                raise EntityValidationError(
                    f"Entity {entity_type} already exists"
                )
            if any(op in operation for op in ["update_", "delete_"]):
                self._raise_not_found_error(operation, context_kwargs)
                return

            raise EntityValidationError(
                f"Conditional check failed: {error_message}"
            )

        if error_code == "ValidationException":
            raise EntityValidationError(f"Validation error: {error_message}")

        if error_code in [
            "ProvisionedThroughputExceededException",
            "ThrottlingException",
        ]:
            raise DynamoDBThroughputError(
                f"Throughput exceeded for {operation}: " f"{error_message}"
            )

        if error_code in ["InternalServerError", "ServiceUnavailable"]:
            raise DynamoDBServerError(
                f"DynamoDB server error during {operation}: "
                f"{error_message}"
            )

        if error_code == "ResourceNotFoundException":
            raise OperationError(
                f"DynamoDB resource not found during {operation}: "
                f"{error_message}"
            )

        # Generic DynamoDB error
        raise DynamoDBError(
            f"DynamoDB error during {operation}: {error_code} - "
            f"{error_message}"
        )

    def _raise_not_found_error(
        self, operation: str, context: Dict[str, Any]
    ) -> None:
        """Raise EntityNotFoundError with operation-specific message."""
        if operation in self.config.ENTITY_NOT_FOUND_PATTERNS:
            message = self.config.ENTITY_NOT_FOUND_PATTERNS[operation].format(
                **context
            )
        else:
            message = f"Entity not found during {operation}"
        raise EntityNotFoundError(message)


# =============================================================================
# ERROR HANDLING DECORATOR
# =============================================================================


def handle_dynamodb_errors(operation_name: str):
    """
    Decorator to handle DynamoDB ClientError exceptions consistently.

    Args:
        operation_name: Name of the operation for error context

    Returns:
        Decorator function that wraps methods with error handling
    """

    def decorator(func):
        def wrapper(self, *args, **kwargs):
            error_handler = ErrorHandler()

            try:
                return func(self, *args, **kwargs)
            except ClientError as e:
                # Extract context from method parameters
                context = _extract_operation_context(
                    operation_name, args, kwargs
                )
                error_handler.handle_client_error(e, operation_name, **context)
                return None
            except (
                EntityNotFoundError,
                EntityValidationError,
                DynamoDBThroughputError,
                DynamoDBServerError,
                DynamoDBError,
                OperationError,
            ):
                # Re-raise domain exceptions as-is
                raise
            except Exception as e:
                # Wrap unexpected exceptions
                raise OperationError(
                    f"Unexpected error during {operation_name}: {str(e)}"
                ) from e

        return wrapper

    return decorator


def _extract_operation_context(
    operation_name: str, args: tuple, kwargs: dict
) -> Dict[str, Any]:
    """Extract relevant context from operation parameters."""
    context = {}

    # Common patterns for extracting IDs from parameters
    if "receipt_id" in kwargs:
        context["receipt_id"] = kwargs["receipt_id"]
    elif len(args) > 0 and hasattr(args[0], "receipt_id"):
        context["receipt_id"] = args[0].receipt_id

    if "image_id" in kwargs:
        context["image_id"] = kwargs["image_id"]
    elif len(args) > 0 and hasattr(args[0], "image_id"):
        context["image_id"] = args[0].image_id

    if "job_id" in kwargs:
        context["job_id"] = kwargs["job_id"]
    elif len(args) > 0 and hasattr(args[0], "job_id"):
        context["job_id"] = args[0].job_id

    # Extract entity name from operation
    if "_" in operation_name:
        entity_name = operation_name.split("_", 1)[1]
        context["entity_name"] = entity_name

    return context


# =============================================================================
# RETRY UTILITIES
# =============================================================================


def exponential_backoff_retry(
    func,
    max_retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
) -> Any:
    """
    Execute a function with exponential backoff retry logic.

    Args:
        func: Function to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        exponential_base: Base for exponential backoff calculation
        jitter: Whether to add random jitter to delays

    Returns:
        Result of the function call

    Raises:
        The last exception encountered if all retries fail
    """

    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except (DynamoDBThroughputError, DynamoDBServerError) as e:
            last_exception = e
            if attempt == max_retries:
                break

            # Calculate delay with exponential backoff
            delay = min(base_delay * (exponential_base**attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random() * 0.5  # Add 0-50% jitter

            time.sleep(delay)
        except Exception as e:
            # Don't retry for other exceptions
            raise e

    # If we get here, all retries failed
    raise last_exception

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
    EntityAlreadyExistsError,
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
        "get_entity": ("{entity_name} with {entity_name}={entity_id} not found"),
        "update_entity": (
            "Cannot update {entity_name} with {entity_name}={entity_id}: "
            "{entity_name} not found"
        ),
        "delete_entity": (
            "Cannot delete {entity_name} with {entity_name}={entity_id}: "
            "{entity_name} not found"
        ),
        "get_receipt": "receipt with receipt_id={receipt_id} not found",
        "get_image": "image with image_id={image_id} not found",
        "get_job": "job with job_id={job_id} not found",
        "get_word": (
            "word with image_id={image_id}, line_id={line_id}, "
            "word_id={word_id} not found"
        ),
        "get_line": ("line with image_id={image_id}, line_id={line_id} not found"),
        # Batch/plural operations
        "update_receipts": ("Cannot update receipts: one or more receipts not found"),
        "delete_receipts": ("Cannot delete receipts: one or more receipts not found"),
        "update_entities": (
            "Cannot update {entity_name}: one or more {entity_name} not found"
        ),
        "delete_entities": (
            "Cannot delete {entity_name}: one or more {entity_name} not found"
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

    # Required parameter messages
    REQUIRED_PARAM_MESSAGES = {
        "receipt": "receipt cannot be None",
        "receipts": "receipts cannot be None",
        "image": "image cannot be None",
        "images": "images cannot be None",
        "word": "word cannot be None",
        "words": "words cannot be None",
        "line": "line cannot be None",
        "lines": "lines cannot be None",
        "receipt_line": "receipt_line cannot be None",
        "receipt_lines": "receipt_lines cannot be None",
        "letter": "letter cannot be None",
        "letters": "letters cannot be None",
    }

    # Parameter validation message patterns
    PARAM_VALIDATION = {
        "required": "{param} cannot be None",
        "type_mismatch": "{param} must be an instance of {class_name}",
        "list_required": "{param} must be a list",
        "list_type_mismatch": (
            "All items in {param} must be instances of {class_name}"
        ),
    }

    # Type mismatch messages for specific entities
    TYPE_MISMATCH_MESSAGES = {
        "receipt": "receipt must be an instance of Receipt",
        "receipts": "receipts must be a list of Receipt instances",
        "image": "image must be an instance of Image",
        "images": "images must be a list of Image instances",
        "word": "word must be an instance of Word",
        "words": "words must be a list of Word instances",
        "line": "line must be an instance of Line",
        "lines": "lines must be a list of Line instances",
        "receipt_line": "receipt_line must be an instance of ReceiptLine",
        "receipt_lines": ("receipt_lines must be a list of ReceiptLine instances"),
        "letter": "letter must be an instance of Letter",
        "letters": "letters must be a list of Letter instances",
    }

    # List required messages for specific entities
    LIST_REQUIRED_MESSAGES = {
        "receipts": "receipts must be a list",
        "images": "images must be a list",
        "words": "words must be a list",
        "lines": "lines must be a list",
        "receipt_lines": "receipt_lines must be a list",
        "letters": "letters must be a list",
    }

    # Parameter name mappings for backward compatibility
    PARAM_NAME_MAPPINGS = {
        "entity": "item",
        "entities": "items",
    }


# =============================================================================
# ERROR CONTEXT EXTRACTION
# =============================================================================


class ErrorContextExtractor:
    """Extracts contextual information from DynamoDB ClientError exceptions."""

    @staticmethod
    def extract_context(error: ClientError, operation: str, **kwargs) -> Dict[str, Any]:
        """Extract relevant context from a ClientError for debugging."""
        context = {
            "operation": operation,
            "error_code": error.response.get("Error", {}).get("Code", "Unknown"),
            "error_message": error.response.get("Error", {}).get(
                "Message", "Unknown error"
            ),
            "request_id": error.response.get("ResponseMetadata", {}).get("RequestId"),
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
        self.context_extractor.extract_context(error, operation, **context_kwargs)

        # Map AWS error codes to domain exceptions
        if error_code == "ConditionalCheckFailedException":
            if "add_" in operation:
                entity_type = operation.replace("add_", "")
                # Keep snake_case to match parameter naming convention
                raise EntityAlreadyExistsError(f"{entity_type} already exists")
            if any(op in operation for op in ["update_", "delete_"]):
                self._raise_not_found_error(operation, context_kwargs)
                return

            raise EntityValidationError(f"Conditional check failed: {error_message}")

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
                f"DynamoDB server error during {operation}: " f"{error_message}"
            )

        if error_code == "ResourceNotFoundException":
            raise OperationError(
                f"DynamoDB resource not found during {operation}: " f"{error_message}"
            )

        # Generic DynamoDB error
        raise DynamoDBError(
            f"DynamoDB error during {operation}: {error_code} - " f"{error_message}"
        )

    def _raise_not_found_error(self, operation: str, context: Dict[str, Any]) -> None:
        """Raise EntityNotFoundError with operation-specific message."""
        if operation in self.config.ENTITY_NOT_FOUND_PATTERNS:
            message = self.config.ENTITY_NOT_FOUND_PATTERNS[operation].format(**context)
        else:
            # Try to extract entity type from context for more
            # descriptive message
            entity_type = context.get("entity_type", "")
            entity_name = context.get("entity_name", "")

            # Use the more specific one if available
            if entity_type:
                message = f"{entity_type} not found during {operation}"
            elif entity_name:
                message = f"{entity_name} not found during {operation}"
            else:
                message = f"entity not found during {operation}"
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
                context = _extract_operation_context(operation_name, args, kwargs)
                error_handler.handle_client_error(e, operation_name, **context)
                return None
            except (
                EntityAlreadyExistsError,
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

    # Check if this is a batch operation (plural)
    is_batch = any(
        plural in operation_name
        for plural in ["receipts", "entities", "words", "lines", "letters"]
    )

    if is_batch and len(args) > 0 and isinstance(args[0], list):
        # For batch operations, extract info from the list of entities
        entities = args[0]
        if entities:
            # Get the entity type from the first item
            entity_type = type(entities[0]).__name__.lower()
            context["entity_type"] = entity_type
            context["entity_count"] = len(entities)

            # For small batches, include some IDs for context
            if len(entities) <= 3:
                if hasattr(entities[0], "receipt_id"):
                    context["receipt_ids"] = [e.receipt_id for e in entities]
                if hasattr(entities[0], "image_id"):
                    context["image_ids"] = list(set(e.image_id for e in entities))
    else:
        # Single entity operations - also try to extract entity type
        if len(args) > 0 and hasattr(args[0], "__class__"):
            entity_type = type(args[0]).__name__.lower()
            context["entity_type"] = entity_type

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
        # Handle special case where entities becomes receipts/words/etc.
        if entity_name == "entities" and "entity_type" in context:
            # Use the actual entity type for more specific messages
            context["entity_name"] = context["entity_type"] + "s"
        else:
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

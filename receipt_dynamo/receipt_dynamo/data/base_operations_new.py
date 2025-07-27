"""
Base classes and mixins for DynamoDB operations to reduce code duplication.

This module provides common functionality that can be shared across all
DynamoDB data access classes in the receipt_dynamo package.

This is a backward compatibility layer that imports from the new modular structure.
"""

# Import everything from the new modular structure
from .base_operations import *

# Maintain backward compatibility by also importing the old class structure
from .base_operations.base import DynamoDBBaseOperations
from .base_operations.error_config import ErrorMessageConfig
from .base_operations.error_handlers import (
    handle_dynamodb_errors,
    ErrorHandler,
)
from .base_operations.error_context import ErrorContextExtractor
from .base_operations.mixins import (
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
)
from .base_operations.validators import (
    ValidationMessageGenerator,
    EntityValidator,
)

# For backward compatibility, create aliases for the main classes
EntityErrorHandler = ErrorHandler  # Old name alias

# Re-export everything to maintain existing imports
__all__ = [
    "DynamoDBBaseOperations",
    "ErrorMessageConfig",
    "EntityErrorHandler",
    "ErrorHandler",
    "ErrorContextExtractor",
    "ValidationMessageGenerator",
    "EntityValidator",
    "handle_dynamodb_errors",
    "SingleEntityCRUDMixin",
    "BatchOperationsMixin",
    "TransactionalOperationsMixin",
]

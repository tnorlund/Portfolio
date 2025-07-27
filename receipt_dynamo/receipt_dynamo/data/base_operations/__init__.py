"""
Base operations package for DynamoDB data access.

This package provides modular, reusable components for building
DynamoDB data access classes with consistent error handling,
validation, and operation patterns.
"""

from .base import DynamoDBBaseOperations
from .error_config import ErrorMessageConfig
from .error_context import ErrorContextExtractor
from .error_handlers import ErrorHandler, handle_dynamodb_errors
from .mixins import (
    BatchOperationsMixin,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
)
from .validators import EntityValidator, ValidationMessageGenerator

__all__ = [
    # Main base class
    "DynamoDBBaseOperations",
    # Error handling
    "ErrorHandler",
    "handle_dynamodb_errors",
    "ErrorMessageConfig",
    "ErrorContextExtractor",
    # Validation
    "EntityValidator",
    "ValidationMessageGenerator",
    # Mixins for composable functionality
    "SingleEntityCRUDMixin",
    "BatchOperationsMixin",
    "TransactionalOperationsMixin",
]

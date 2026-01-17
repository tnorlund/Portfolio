"""
Base operations package for DynamoDB data access.

This package provides modular, reusable components for building
DynamoDB data access classes with consistent error handling,
validation, and operation patterns.
"""

from .base import DynamoDBBaseOperations
from .error_handling import (
    ErrorContextExtractor,
    ErrorHandler,
    ErrorMessageConfig,
    handle_dynamodb_errors,
)
from .flattened_mixin import FlattenedStandardMixin
from .mixins import (
    BatchOperationsMixin,
    QueryByParentMixin,
    QueryByTypeMixin,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
)
from .types import *
from .validators import EntityValidator, ValidationMessageGenerator

__all__ = [
    # Main base class
    "DynamoDBBaseOperations",
    # Flattened mixin (recommended for most accessors)
    "FlattenedStandardMixin",
    # Error handling
    "ErrorHandler",
    "handle_dynamodb_errors",
    "ErrorMessageConfig",
    "ErrorContextExtractor",
    # Validation
    "EntityValidator",
    "ValidationMessageGenerator",
    # Composable mixins for custom accessor patterns
    "SingleEntityCRUDMixin",
    "BatchOperationsMixin",
    "TransactionalOperationsMixin",
    "QueryByTypeMixin",
    "QueryByParentMixin",
]

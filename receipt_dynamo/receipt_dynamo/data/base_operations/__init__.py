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
    CommonValidationMixin,
    QueryByParentMixin,
    QueryByTypeMixin,
    SimplifiedAccessorMixin,
    SingleEntityCRUDMixin,
    StandardAccessorMixin,
    TransactionalOperationsMixin,
)
from .types import *
from .validators import EntityValidator, ValidationMessageGenerator

__all__ = [
    # Main base class
    "DynamoDBBaseOperations",
    # Consolidated accessor mixins (recommended)
    "StandardAccessorMixin",
    "SimplifiedAccessorMixin",
    "FlattenedStandardMixin",
    # Error handling
    "ErrorHandler",
    "handle_dynamodb_errors",
    "ErrorMessageConfig",
    "ErrorContextExtractor",
    # Validation
    "EntityValidator",
    "ValidationMessageGenerator",
    # Original mixins for composable functionality
    "SingleEntityCRUDMixin",
    "BatchOperationsMixin",
    "TransactionalOperationsMixin",
    "QueryByTypeMixin",
    "QueryByParentMixin",
    "CommonValidationMixin",
    # Flattened mixin that respects pylint's max-ancestors limit
    "FlattenedStandardMixin",
]

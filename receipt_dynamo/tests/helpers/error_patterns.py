"""
Test helpers for error message patterns.

This module provides utilities and constants for testing error messages
in a way that's compatible with both old and new error message formats.
"""

import re
from typing import Union, Pattern, Optional


class ErrorPatterns:
    """Common error message patterns for tests."""
    
    # EntityAlreadyExistsError patterns
    ALREADY_EXISTS_SIMPLE = "already exists"
    ALREADY_EXISTS_ENTITY = re.compile(r"(Entity already exists: \w+|already exists)")
    
    # EntityNotFoundError patterns
    NOT_FOUND_SIMPLE = "does not exist"
    NOT_FOUND_ENTITY = re.compile(r"Entity does not exist: \w+")
    NOT_FOUND_QUEUE = re.compile(r"Queue.*not found")
    NOT_FOUND_JOB = re.compile(r"Job with job id.*does not exist")
    
    # Validation error patterns  
    VALIDATION_INVALID = re.compile(r"One or more parameters.*invalid")
    VALIDATION_CANNOT_BE_NONE = re.compile(r"\w+ cannot be None")
    VALIDATION_MUST_BE_INSTANCE = re.compile(r"\w+ must be an instance of the \w+ class")
    VALIDATION_MUST_BE_LIST = re.compile(r"\w+ must be a list")
    
    @staticmethod
    def matches_any(text: str, *patterns: Union[str, Pattern]) -> bool:
        """Check if text matches any of the given patterns."""
        for pattern in patterns:
            if isinstance(pattern, str):
                if pattern in text:
                    return True
            else:
                if pattern.search(text):
                    return True
        return False
    
    @staticmethod
    def entity_already_exists(entity_type: Optional[str] = None) -> Union[str, Pattern]:
        """Get pattern for entity already exists error."""
        if entity_type:
            # Return pattern that matches both old and new formats
            return re.compile(f"(Entity already exists: {entity_type}|already exists)")
        return ErrorPatterns.ALREADY_EXISTS_SIMPLE
    
    @staticmethod
    def entity_not_found(entity_type: Optional[str] = None) -> Union[str, Pattern]:
        """Get pattern for entity not found error."""
        if entity_type:
            return re.compile(f"Entity does not exist: {entity_type}")
        return ErrorPatterns.NOT_FOUND_SIMPLE


class FlexibleErrorMatcher:
    """Context manager for flexible error matching in tests."""
    
    def __init__(self, exception_type: type, *patterns: Union[str, Pattern]):
        self.exception_type = exception_type
        self.patterns = patterns
        self.exception = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            raise AssertionError(f"Expected {self.exception_type.__name__} but no exception was raised")
        
        if not issubclass(exc_type, self.exception_type):
            return False
        
        self.exception = exc_val
        error_message = str(exc_val)
        
        if self.patterns and not ErrorPatterns.matches_any(error_message, *self.patterns):
            raise AssertionError(
                f"Error message '{error_message}' does not match any expected patterns: {self.patterns}"
            )
        
        return True


# Convenience functions for common test patterns
def assert_already_exists_error(func, *args, entity_type: Optional[str] = None, **kwargs):
    """Assert that a function raises EntityAlreadyExistsError with appropriate message."""
    from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
    
    pattern = ErrorPatterns.entity_already_exists(entity_type)
    with FlexibleErrorMatcher(EntityAlreadyExistsError, pattern):
        func(*args, **kwargs)


def assert_not_found_error(func, *args, entity_type: Optional[str] = None, **kwargs):
    """Assert that a function raises EntityNotFoundError with appropriate message."""
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
    
    pattern = ErrorPatterns.entity_not_found(entity_type)
    with FlexibleErrorMatcher(EntityNotFoundError, pattern):
        func(*args, **kwargs)


def assert_validation_error(func, *args, message_pattern: Optional[Union[str, Pattern]] = None, **kwargs):
    """Assert that a function raises DynamoDBValidationError."""
    from receipt_dynamo.data.shared_exceptions import DynamoDBValidationError
    
    patterns = [message_pattern] if message_pattern else []
    with FlexibleErrorMatcher(DynamoDBValidationError, *patterns):
        func(*args, **kwargs)
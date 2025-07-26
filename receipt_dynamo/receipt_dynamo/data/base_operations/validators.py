"""
Validation utilities for DynamoDB operations.

This module provides consistent validation logic for entities and parameters
across all DynamoDB data access classes.
"""

from typing import Any, List, Type

from .error_config import ErrorMessageConfig


class ValidationMessageGenerator:
    """Generates consistent validation error messages."""
    
    def __init__(self, config: ErrorMessageConfig):
        self.config = config

    def generate_required_message(self, param_name: str) -> str:
        """Generate a 'required parameter' error message."""
        # Check for special cases first
        if param_name in self.config.REQUIRED_PARAM_MESSAGES:
            return self.config.REQUIRED_PARAM_MESSAGES[param_name]
            
        # Default pattern
        return self.config.PARAM_VALIDATION["required"].format(param=param_name)

    def generate_type_mismatch_message(self, param_name: str, class_name: str) -> str:
        """Generate a 'type mismatch' error message."""
        # Check for special cases first
        key = param_name if param_name in self.config.TYPE_MISMATCH_MESSAGES else class_name
        if key in self.config.TYPE_MISMATCH_MESSAGES:
            return self.config.TYPE_MISMATCH_MESSAGES[key]
            
        # Default pattern
        return self.config.PARAM_VALIDATION["type_mismatch"].format(
            param=param_name, class_name=class_name
        )

    def generate_list_required_message(self, param_name: str, class_name: str) -> str:
        """Generate a 'list required' error message."""
        # Check for special cases first
        if param_name in self.config.TYPE_MISMATCH_MESSAGES:
            return self.config.TYPE_MISMATCH_MESSAGES[param_name]
            
        # Default pattern
        return self.config.PARAM_VALIDATION["list_required"].format(
            param=param_name, class_name=class_name
        )

    def generate_list_type_mismatch_message(self, param_name: str, class_name: str) -> str:
        """Generate a 'list type mismatch' error message."""
        # Check for special cases first
        if param_name in self.config.TYPE_MISMATCH_MESSAGES:
            return self.config.TYPE_MISMATCH_MESSAGES[param_name]
            
        # Default pattern
        return self.config.PARAM_VALIDATION["list_type_mismatch"].format(
            param=param_name, class_name=class_name
        )

    def normalize_param_name(self, param_name: str) -> str:
        """Normalize parameter name for consistent messaging."""
        # Apply mappings for backward compatibility
        if param_name in self.config.PARAM_NAME_MAPPINGS:
            return self.config.PARAM_NAME_MAPPINGS[param_name]
        return param_name


class EntityValidator:
    """Provides consistent entity validation logic."""
    
    def __init__(self, config: ErrorMessageConfig):
        self.config = config
        self.message_generator = ValidationMessageGenerator(config)

    def validate_entity(self, entity: Any, entity_class: Type, param_name: str) -> None:
        """
        Common entity validation logic with consistent error messages.

        Args:
            entity: The entity to validate
            entity_class: The expected class of the entity
            param_name: Name of parameter for error messages

        Raises:
            ValueError: If validation fails
        """
        if entity is None:
            raise ValueError(self.message_generator.generate_required_message(param_name))

        if not isinstance(entity, entity_class):
            raise ValueError(
                self.message_generator.generate_type_mismatch_message(param_name, entity_class.__name__)
            )

    def validate_entity_list(self, entities: List[Any], entity_class: Type, param_name: str) -> None:
        """
        Validate a list of entities with consistent error messages.

        Args:
            entities: List of entities to validate
            entity_class: Expected class of entities
            param_name: Name of parameter for error messages

        Raises:
            ValueError: If validation fails
        """
        if entities is None:
            raise ValueError(self.message_generator.generate_required_message(param_name))

        if not isinstance(entities, list):
            raise ValueError(
                self.message_generator.generate_list_required_message(param_name, entity_class.__name__)
            )

        for entity in entities:
            if not isinstance(entity, entity_class):
                raise ValueError(
                    self.message_generator.generate_list_type_mismatch_message(param_name, entity_class.__name__)
                )

    def transform_validation_message(self, message: str, operation: str) -> str:
        """Transform validation messages for backward compatibility."""
        # Handle "given were" transformations
        if "receipt_label_analysis" in operation:
            # These operations expect "were" not "given were"
            message = message.replace(
                "One or more parameters given were invalid",
                "One or more parameters were invalid"
            )
        elif any(op in operation for op in [
            "receipt_line_item_analysis", "receipt_line_item_analyses",
            "receipt_validation_result", 
            "receipt_structure_analysis", "receipt_structure_analyses",
            "receipt_chatgpt_validation", "receipt_chat_gpt_validation"
        ]):
            # These operations expect "given were"
            message = message.replace(
                "One or more parameters were invalid",
                "One or more parameters given were invalid"
            )
            
        return message
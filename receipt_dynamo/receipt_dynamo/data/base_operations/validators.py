"""
Validation utilities for DynamoDB operations.

This module provides consistent validation logic for entities and parameters
across all DynamoDB data access classes.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

from .error_config import ErrorMessageConfig


class ValidationMessageGenerator:
    """Generates consistent validation error messages."""

    def __init__(self, config: ErrorMessageConfig) -> None:
        self.config: ErrorMessageConfig = config
        # Pre-build lookup dictionary for efficient message generation
        self._message_generators: Dict[str, Callable[[str, str], str]] = {
            "required": self._generate_required_message,
            "type_mismatch": self._generate_type_mismatch_message,
            "list_required": self._generate_list_required_message,
            "list_type_mismatch": self._generate_list_type_mismatch_message,
        }

    def generate_message(
        self,
        message_type: str,
        param_name: str,
        class_name: Optional[str] = None,
    ) -> str:
        """
        Generate validation error message using dictionary-based lookup.

        Args:
            message_type: Type of validation error (required,
                type_mismatch, etc.)
            param_name: Parameter name for the error
            class_name: Expected class name (optional, for type mismatch
                errors)

        Returns:
            Formatted error message

        Raises:
            ValueError: If message_type is not supported
        """
        if message_type not in self._message_generators:
            raise ValueError(f"Unsupported message type: {message_type}")

        generator = self._message_generators[message_type]
        return generator(param_name, class_name or "")

    def generate_required_message(self, param_name: str) -> str:
        """Generate a 'required parameter' error message."""
        return self.generate_message("required", param_name)

    def generate_type_mismatch_message(
        self, param_name: str, class_name: str
    ) -> str:
        """Generate a 'type mismatch' error message."""
        return self.generate_message("type_mismatch", param_name, class_name)

    def generate_list_required_message(
        self, param_name: str, class_name: str
    ) -> str:
        """Generate a 'list required' error message."""
        return self.generate_message("list_required", param_name, class_name)

    def generate_list_type_mismatch_message(
        self, param_name: str, class_name: str
    ) -> str:
        """Generate a 'list type mismatch' error message."""
        return self.generate_message(
            "list_type_mismatch", param_name, class_name
        )

    def _generate_required_message(
        self, param_name: str, _class_name: Optional[str] = None
    ) -> str:
        """Internal method for generating required parameter messages."""
        # Check for special cases first
        if param_name in self.config.REQUIRED_PARAM_MESSAGES:
            return self.config.REQUIRED_PARAM_MESSAGES[param_name]

        # Default pattern
        return self.config.PARAM_VALIDATION["required"].format(
            param=param_name
        )

    def _generate_type_mismatch_message(
        self, param_name: str, class_name: str
    ) -> str:
        """Internal method for generating type mismatch messages."""
        # Check for special cases first
        key = (
            param_name
            if param_name in self.config.TYPE_MISMATCH_MESSAGES
            else class_name
        )
        if key in self.config.TYPE_MISMATCH_MESSAGES:
            return self.config.TYPE_MISMATCH_MESSAGES[key]

        # Default pattern
        return self.config.PARAM_VALIDATION["type_mismatch"].format(
            param=param_name, class_name=class_name
        )

    def _generate_list_required_message(
        self, param_name: str, class_name: str
    ) -> str:
        """Internal method for generating list required messages."""
        # Check for special cases first
        if param_name in self.config.LIST_REQUIRED_MESSAGES:
            return self.config.LIST_REQUIRED_MESSAGES[param_name]

        # Default pattern
        return self.config.PARAM_VALIDATION["list_required"].format(
            param=param_name, class_name=class_name
        )

    def _generate_list_type_mismatch_message(
        self, param_name: str, class_name: str
    ) -> str:
        """Internal method for generating list type mismatch messages."""
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

    def __init__(self, config: ErrorMessageConfig) -> None:
        self.config: ErrorMessageConfig = config
        self.message_generator: ValidationMessageGenerator = (
            ValidationMessageGenerator(config)
        )
        # Dictionary-based validation rules for better maintainability
        self._validation_rules: Dict[
            str, List[Tuple[str, Callable[[Dict[str, Any]], None]]]
        ] = {
            "single_entity": [
                ("null_check", self._validate_not_null),
                ("type_check", self._validate_instance_type),
            ],
            "entity_list": [
                ("null_check", self._validate_not_null),
                ("list_check", self._validate_is_list),
                ("list_contents_check", self._validate_list_contents),
            ],
        }

    def validate_entity(
        self, entity: Any, entity_class: Type[Any], param_name: str
    ) -> None:
        """
        Common entity validation logic with consistent error messages.

        Args:
            entity: The entity to validate
            entity_class: The expected class of the entity
            param_name: Name of parameter for error messages

        Raises:
            ValueError: If validation fails
        """
        context: Dict[str, Any] = {
            "entity": entity,
            "entity_class": entity_class,
            "param_name": param_name,
        }

        self._run_validation_chain("single_entity", context)

    def validate_entity_list(
        self, entities: List[Any], entity_class: Type[Any], param_name: str
    ) -> None:
        """
        Validate a list of entities with consistent error messages.

        Args:
            entities: List of entities to validate
            entity_class: Expected class of entities
            param_name: Name of parameter for error messages

        Raises:
            ValueError: If validation fails
        """
        context: Dict[str, Any] = {
            "entity": entities,
            "entity_class": entity_class,
            "param_name": param_name,
        }

        self._run_validation_chain("entity_list", context)

    def _run_validation_chain(
        self, validation_type: str, context: Dict[str, Any]
    ) -> None:
        """
        Execute validation chain based on validation type.

        Args:
            validation_type: Type of validation chain to run
            context: Validation context containing entity, class, and
                param info

        Raises:
            ValueError: If any validation in the chain fails
        """
        if validation_type not in self._validation_rules:
            raise ValueError(f"Unknown validation type: {validation_type}")

        rules = self._validation_rules[validation_type]
        for _, validator_func in rules:
            try:
                validator_func(context)
            except ValueError as e:
                # Re-raise with context about which validation failed
                raise ValueError(str(e)) from e

    def _validate_not_null(self, context: Dict[str, Any]) -> None:
        """Validate that entity is not None."""
        if context["entity"] is None:
            raise ValueError(
                self.message_generator.generate_required_message(
                    context["param_name"]
                )
            )

    def _validate_instance_type(self, context: Dict[str, Any]) -> None:
        """Validate that entity is of expected type."""
        entity = context["entity"]
        entity_class = context["entity_class"]
        param_name = context["param_name"]

        if not isinstance(entity, entity_class):
            raise ValueError(
                self.message_generator.generate_type_mismatch_message(
                    param_name, entity_class.__name__
                )
            )

    def _validate_is_list(self, context: Dict[str, Any]) -> None:
        """Validate that entity is a list."""
        entity = context["entity"]
        entity_class = context["entity_class"]
        param_name = context["param_name"]

        if not isinstance(entity, list):
            raise ValueError(
                self.message_generator.generate_list_required_message(
                    param_name, entity_class.__name__
                )
            )

    def _validate_list_contents(self, context: Dict[str, Any]) -> None:
        """Validate that all items in list are of expected type."""
        entities = context["entity"]
        entity_class = context["entity_class"]
        param_name = context["param_name"]

        for entity in entities:
            if not isinstance(entity, entity_class):
                raise ValueError(
                    self.message_generator.generate_list_type_mismatch_message(
                        param_name, entity_class.__name__
                    )
                )

    def transform_validation_message(
        self, message: str, operation: str
    ) -> str:
        """Transform validation messages for backward compatibility using
        dictionary-based patterns."""
        # Define transformation rules using dictionary lookup for better
        # maintainability
        transformation_rules: Dict[
            str, Dict[str, Union[List[str], Dict[str, str]]]
        ] = {
            # Operations that expect "were" (remove "given")
            "remove_given": {
                "pattern_match": [],
                "transformations": {
                    "One or more parameters given were invalid": (
                        "One or more parameters were invalid"
                    )
                },
            },
            # Operations that expect "given were" (add "given")
            "add_given": {
                "pattern_match": [
                    "receipt_label_analysis",
                    "receipt_line_item_analysis",
                    "receipt_line_item_analyses",
                    "receipt_validation_result",
                    "receipt_structure_analysis",
                    "receipt_structure_analyses",
                    "receipt_chatgpt_validation",
                    "receipt_chat_gpt_validation",
                ],
                "transformations": {
                    "One or more parameters were invalid": (
                        "One or more parameters given were invalid"
                    )
                },
            },
        }

        # Apply transformations based on operation patterns
        transformed_message = message
        for _, rule_config in transformation_rules.items():
            pattern_match = rule_config["pattern_match"]
            transformations = rule_config["transformations"]

            # Check if operation matches any pattern in this rule
            if isinstance(pattern_match, list) and any(
                pattern in operation for pattern in pattern_match
            ):
                # Apply all transformations for this rule
                if isinstance(transformations, dict):
                    for old_text, new_text in transformations.items():
                        transformed_message = transformed_message.replace(
                            old_text, new_text
                        )
                break  # Only apply first matching rule

        return transformed_message

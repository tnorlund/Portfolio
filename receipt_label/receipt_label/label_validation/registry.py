"""Registry for label validators."""

from typing import Dict, Optional, Type

from receipt_label.constants import CORE_LABELS

from .base import BaseLabelValidator


class ValidatorRegistry:
    """Registry for all label validators."""

    def __init__(self):
        self._validators: Dict[str, Type[BaseLabelValidator]] = {}
        self._label_to_validator: Dict[str, str] = {}

    def register(self, validator_class: Type[BaseLabelValidator]):
        """Register a validator class."""
        validator = validator_class()
        for label in validator.supported_labels:
            if label not in CORE_LABELS:
                raise ValueError(f"Label {label} not in CORE_LABELS")
            self._validators[label] = validator_class
            self._label_to_validator[label] = validator_class.__name__

    def get_validator(self, label: str) -> Optional[BaseLabelValidator]:
        """Get validator instance for a label."""
        validator_class = self._validators.get(label)
        if validator_class:
            return validator_class()
        return None

    def get_all_supported_labels(self) -> set[str]:
        """Get all labels that have validators."""
        return set(self._validators.keys())

    def get_missing_labels(self) -> set[str]:
        """Get CORE_LABELS that don't have validators."""
        return set(CORE_LABELS.keys()) - self.get_all_supported_labels()


# Global registry instance
_registry = ValidatorRegistry()


def get_validator(label: str) -> Optional[BaseLabelValidator]:
    """Get validator for a label."""
    return _registry.get_validator(label)


def register_validator(validator_class: Type[BaseLabelValidator]):
    """Register a validator."""
    _registry.register(validator_class)


def get_all_supported_labels() -> set[str]:
    """Get all labels that have validators."""
    return _registry.get_all_supported_labels()


def get_missing_labels() -> set[str]:
    """Get CORE_LABELS that don't have validators."""
    return _registry.get_missing_labels()


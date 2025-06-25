"""Utility functions for receipt labeling."""

import importlib.metadata

from .address import (
    compare_addresses,
    format_address,
    normalize_address,
    parse_address,
)
from .clients import get_clients, get_client_manager
from .client_manager import ClientConfig, ClientManager
from .date import (
    extract_datetime,
    format_datetime,
    get_date_range,
    get_time_difference,
    is_valid_date,
    is_valid_time,
    parse_datetime,
)
from .validation import (
    validate_address,
    validate_amounts,
    validate_business_name,
    validate_datetime,
    validate_phone_number,
    validate_receipt_data,
    validate_receipt_format,
)
from .cost_calculator import AICostCalculator
from .ai_usage_tracker import AIUsageTracker

__all__ = [
    # Address utilities
    "normalize_address",
    "parse_address",
    "format_address",
    "compare_addresses",
    # Date utilities
    "parse_datetime",
    "extract_datetime",
    "format_datetime",
    "is_valid_date",
    "is_valid_time",
    "get_date_range",
    "get_time_difference",
    # Validation utilities
    "validate_business_name",
    "validate_phone_number",
    "validate_address",
    "validate_datetime",
    "validate_amounts",
    "validate_receipt_data",
    "validate_receipt_format",
    # Client utilities
    "get_clients",
    "get_client_manager",
    "ClientConfig",
    "ClientManager",
    # AI cost tracking
    "AICostCalculator",
    "AIUsageTracker",
]


def get_package_version() -> str:
    """
    Get the current version of the receipt_label package.

    Returns:
        str: The version string of the receipt_label package.
    """
    try:
        return importlib.metadata.version("receipt_label")
    except importlib.metadata.PackageNotFoundError:
        # If installed in development mode or not installed via pip
        return "unknown"

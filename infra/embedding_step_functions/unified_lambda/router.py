"""Smart router for Lambda handler selection.

This module provides the main entry point for the unified Lambda container.
It routes requests to the appropriate handler based on configuration.
"""

import os
from typing import Any, Dict

from handlers import HANDLER_REGISTRY


def get_handler_name() -> str:
    """Determine which handler to use based on environment configuration.
    
    Returns:
        Handler name from environment variable
        
    Raises:
        ValueError: If HANDLER_TYPE is not set or invalid
    """
    handler_type = os.environ.get("HANDLER_TYPE")
    
    if not handler_type:
        raise ValueError(
            "HANDLER_TYPE environment variable must be set. "
            f"Valid values: {', '.join(HANDLER_REGISTRY.keys())}"
        )
    
    if handler_type not in HANDLER_REGISTRY:
        raise ValueError(
            f"Invalid HANDLER_TYPE: {handler_type}. "
            f"Valid values: {', '.join(HANDLER_REGISTRY.keys())}"
        )
    
    return handler_type


# Create singleton handler instance at module load time
# This allows Lambda to reuse the handler across invocations
_handler_type = get_handler_name()
_handler_class = HANDLER_REGISTRY[_handler_type]
_handler_instance = _handler_class()


def lambda_handler(event: Dict[str, Any], context: Any) -> Any:
    """Main Lambda entry point that routes to the appropriate handler.
    
    This is the function that AWS Lambda will invoke.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        Handler response (raw data for Step Functions, HTTP response for API Gateway)
    """
    return _handler_instance(event, context)
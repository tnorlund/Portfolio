"""Response formatting utilities for Lambda functions.

Handles formatting responses appropriately for different invocation sources
(Step Functions vs API Gateway).
"""

import json
from typing import Any, Dict


def is_step_function_invocation(event: Dict[str, Any]) -> bool:
    """Check if this Lambda is being invoked from Step Functions.

    Step Functions invocations can be detected by:
    1. Direct invocation (no httpMethod)
    2. Absence of API Gateway fields

    Args:
        event: Lambda event

    Returns:
        True if invoked from Step Functions, False otherwise
    """
    # If there's an httpMethod, it's from API Gateway
    if "httpMethod" in event:
        return False

    # If requestContext exists with apiId, it's from API Gateway
    if "requestContext" in event and "apiId" in event.get("requestContext", {}):
        return False

    # Otherwise, assume it's from Step Functions or direct invocation
    return True


def format_response(
    data: Any,
    event: Dict[str, Any],
    is_error: bool = False,
    status_code: int | None = None,
) -> Any:
    """Format response based on invocation source.

    Args:
        data: Response data
        event: Lambda event (used to detect invocation source)
        is_error: Whether this is an error response
        status_code: HTTP status code (defaults to 500 for errors, 200 for success)

    Returns:
        Raw data for Step Functions, HTTP response for API Gateway
    """
    # For Step Functions, return raw data or raise exception
    if is_step_function_invocation(event):
        if is_error:
            # Step Functions handle exceptions through state machine error handling
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(data["error"])
            raise RuntimeError(str(data))
        return data

    # For API Gateway, return HTTP response
    if status_code is None:
        status_code = 500 if is_error else 200

    return {
        "statusCode": status_code,
        "body": (json.dumps(data, default=str) if not isinstance(data, str) else data),
        "headers": {"Content-Type": "application/json"},
    }

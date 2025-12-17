"""AWS Lambda entry point for unified embedding container.

This is the main entry point that AWS Lambda invokes.
All routing logic is delegated to the router module.

Force rebuild: 2025-11-16 - Added delta validation and retry logic.
"""

from typing import Any, Dict

import router


def lambda_handler(event: Dict[str, Any], context: Any) -> Any:
    """AWS Lambda handler function.

    Args:
        event: Lambda event from Step Functions or API Gateway
        context: Lambda runtime context

    Returns:
        Response appropriate for the invocation source
    """
    return router.route_request(event, context)

"""AWS Lambda entry point for unified embedding container.

This is the main entry point that AWS Lambda invokes.
All routing logic is delegated to the router module.
"""

import router


def lambda_handler(event, context):
    """AWS Lambda handler function.
    
    Args:
        event: Lambda event from Step Functions or API Gateway
        context: Lambda runtime context
        
    Returns:
        Response appropriate for the invocation source
    """
    return router.route_request(event, context)
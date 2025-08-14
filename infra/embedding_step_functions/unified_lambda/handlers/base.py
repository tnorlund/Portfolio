"""Base handler class for all Lambda functions."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class BaseLambdaHandler(ABC):
    """Abstract base class for Lambda handlers.

    Provides common functionality like logging, error handling,
    and response formatting.
    """

    def __init__(self, name: str):
        """Initialize handler with a name for logging."""
        self.name = name
        self.logger = logging.getLogger(name)

    def __call__(self, event: Dict[str, Any], context: Any) -> Any:
        """Main entry point for Lambda execution.

        Handles common concerns like logging and error handling,
        then delegates to the specific handler implementation.

        For Step Functions, returns raw result without HTTP response wrapper.
        """
        self.logger.info(f"Starting {self.name} handler")
        self.logger.debug(f"Event: {json.dumps(event, default=str)}")

        try:
            # Pre-process hook
            event = self.pre_process(event, context)

            # Main handler logic
            result = self.handle(event, context)

            # Post-process hook
            result = self.post_process(result, context)

            self.logger.info(f"Successfully completed {self.name} handler")

            # Check if we're being invoked from Step Functions
            # Step Functions doesn't need HTTP-style responses
            if self._is_step_function_invocation(event):
                return result

            return self._success_response(result)

        except Exception as e:
            self.logger.error(
                f"Error in {self.name} handler: {str(e)}", exc_info=True
            )
            # Step Functions handle exceptions differently
            if self._is_step_function_invocation(event):
                raise
            return self._error_response(str(e))

    @abstractmethod
    def handle(self, event: Dict[str, Any], context: Any) -> Any:
        """Main handler logic to be implemented by subclasses.

        Returns:
            Any: Raw data for Step Functions, dict for API Gateway responses
        """
        pass

    def pre_process(
        self, event: Dict[str, Any], context: Any
    ) -> Dict[str, Any]:
        """Hook for pre-processing. Override if needed."""
        return event

    def post_process(self, result: Any, context: Any) -> Any:
        """Hook for post-processing. Override if needed."""
        return result

    def _success_response(self, body: Any) -> Dict[str, Any]:
        """Format successful response."""
        return {
            "statusCode": 200,
            "body": (
                json.dumps(body, default=str)
                if not isinstance(body, str)
                else body
            ),
            "headers": {"Content-Type": "application/json"},
        }

    def _error_response(
        self, error_message: str, status_code: int = 500
    ) -> Dict[str, Any]:
        """Format error response."""
        return {
            "statusCode": status_code,
            "body": json.dumps({"error": error_message}),
            "headers": {"Content-Type": "application/json"},
        }

    def _is_step_function_invocation(self, event: Dict[str, Any]) -> bool:
        """Check if this Lambda is being invoked from Step Functions.

        Step Functions invocations can be detected by:
        1. Direct invocation (no httpMethod)
        2. Presence of Step Function-specific fields
        3. Absence of API Gateway fields
        """
        # If there's an httpMethod, it's from API Gateway
        if "httpMethod" in event:
            return False

        # If requestContext exists and has apiId, it's from API Gateway
        if "requestContext" in event and "apiId" in event.get(
            "requestContext", {}
        ):
            return False

        # Otherwise, assume it's from Step Functions or direct invocation
        # Step Functions pass data directly without HTTP wrapper
        return True

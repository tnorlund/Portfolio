"""Base handler class for all Lambda functions."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
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
        
    def __call__(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Main entry point for Lambda execution.
        
        Handles common concerns like logging and error handling,
        then delegates to the specific handler implementation.
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
            return self._success_response(result)
            
        except Exception as e:
            self.logger.error(f"Error in {self.name} handler: {str(e)}", exc_info=True)
            return self._error_response(str(e))
    
    @abstractmethod
    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Main handler logic to be implemented by subclasses."""
        pass
    
    def pre_process(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Hook for pre-processing. Override if needed."""
        return event
    
    def post_process(self, result: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Hook for post-processing. Override if needed."""
        return result
    
    def _success_response(self, body: Any) -> Dict[str, Any]:
        """Format successful response."""
        return {
            "statusCode": 200,
            "body": json.dumps(body, default=str) if not isinstance(body, str) else body,
            "headers": {
                "Content-Type": "application/json"
            }
        }
    
    def _error_response(self, error_message: str, status_code: int = 500) -> Dict[str, Any]:
        """Format error response."""
        return {
            "statusCode": status_code,
            "body": json.dumps({"error": error_message}),
            "headers": {
                "Content-Type": "application/json"
            }
        }
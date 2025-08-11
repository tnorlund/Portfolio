"""Smart router for Lambda handler selection.

Routes requests to appropriate handlers based on environment configuration.
No module-level state for better testability and clarity.
"""

import os
import logging
from typing import Any, Dict, Optional

# Import handlers when needed, not at module level
from handlers import (
    word_polling,
    line_polling,
    compaction,
    find_unembedded,
    submit_openai,
    list_pending,
)
from utils.response import format_response

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Handler mapping
HANDLER_MAP = {
    "word_polling": word_polling.handle,
    "line_polling": line_polling.handle,
    "compaction": compaction.handle,
    "find_unembedded": find_unembedded.handle,
    "submit_openai": submit_openai.handle,
    "list_pending": list_pending.handle,
}


def route_request(event: Dict[str, Any], context: Any) -> Any:
    """Route request to appropriate handler based on environment.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        Formatted response appropriate for invocation source
        
    Raises:
        ValueError: If HANDLER_TYPE is not set or invalid
    """
    # Get handler type from environment
    handler_type = os.environ.get("HANDLER_TYPE")
    
    if not handler_type:
        raise ValueError(
            f"HANDLER_TYPE environment variable must be set. "
            f"Valid values: {', '.join(HANDLER_MAP.keys())}"
        )
    
    # Get the handler function
    handler = HANDLER_MAP.get(handler_type)
    
    if not handler:
        raise ValueError(
            f"Invalid HANDLER_TYPE: {handler_type}. "
            f"Valid values: {', '.join(HANDLER_MAP.keys())}"
        )
    
    logger.info(f"Routing to {handler_type} handler")
    
    try:
        # Execute the handler
        result = handler(event, context)
        
        # Format response based on invocation source
        return format_response(result, event)
        
    except Exception as e:
        logger.error(f"Error in {handler_type} handler: {str(e)}", exc_info=True)
        
        # Let format_response handle error formatting
        return format_response(
            {"error": str(e)}, 
            event, 
            is_error=True
        )
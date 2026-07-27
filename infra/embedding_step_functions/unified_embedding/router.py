"""Smart router for Lambda handler selection.

Routes requests to appropriate handlers based on environment configuration.
No module-level state for better testability and clarity.
"""

import logging
import os
from importlib import import_module
from typing import Any, Dict

from utils import response as response_utils

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Only container-deployed handler types belong here. Lightweight discovery,
# list, normalization, and finalization handlers are zip Lambdas. Lazy imports
# avoid loading compaction (and its AWS clients) in every submit/poll process.
HANDLER_MODULES = {
    "word_polling": "handlers.word_polling",
    "line_polling": "handlers.line_polling",
    "compaction": "handlers.compaction",
    "submit_openai": "handlers.submit_openai",
    "submit_words_openai": "handlers.submit_words_openai",
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
            f"Valid values: {', '.join(HANDLER_MODULES.keys())}"
        )

    # Get the handler function
    module_name = HANDLER_MODULES.get(handler_type)

    if not module_name:
        raise ValueError(
            f"Invalid HANDLER_TYPE: {handler_type}. "
            f"Valid values: {', '.join(HANDLER_MODULES.keys())}"
        )
    handler = import_module(module_name).handle

    logger.info("Routing to %s handler", handler_type)

    try:
        # Execute the handler
        result = handler(event, context)

        # Format response based on invocation source
        return response_utils.format_response(result, event)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error in %s handler: %s", handler_type, str(e), exc_info=True)

        # Let format_response handle error formatting
        return response_utils.format_response({"error": str(e)}, event, is_error=True)

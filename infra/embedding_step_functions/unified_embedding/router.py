"""Smart router for Lambda handler selection.

Routes requests to appropriate handlers based on environment configuration.
No module-level state for better testability and clarity.
"""

import os
import logging
from typing import Any, Dict

# Import handlers when needed, not at module level
from handlers import word_polling
from handlers import line_polling
from handlers import compaction
from handlers import find_unembedded
from handlers import submit_openai
from handlers import list_pending
from handlers import split_into_chunks
from handlers import find_unembedded_words
from handlers import submit_words_openai
from handlers import mark_batches_complete
from handlers import create_chunk_groups
from utils import response as response_utils

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
    "split_into_chunks": split_into_chunks.handle,
    "find_unembedded_words": find_unembedded_words.handle,
    "submit_words_openai": submit_words_openai.handle,
    "mark_batches_complete": mark_batches_complete.handle,
    "create_chunk_groups": create_chunk_groups.handle,
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

    logger.info("Routing to %s handler", handler_type)

    try:
        # Execute the handler
        result = handler(event, context)

        # Format response based on invocation source
        return response_utils.format_response(result, event)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "Error in %s handler: %s", handler_type, str(e), exc_info=True
        )

        # Let format_response handle error formatting
        return response_utils.format_response(
            {"error": str(e)}, event, is_error=True
        )

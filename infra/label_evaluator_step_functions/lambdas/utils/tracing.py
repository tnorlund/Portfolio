"""LangSmith tracing utilities for Lambda functions."""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# LangSmith tracing - flush before Lambda exits
_get_langsmith_client: Optional[Callable[..., Any]] = None
try:
    from langsmith.run_trees import get_cached_client

    _get_langsmith_client = get_cached_client
    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False


def flush_langsmith_traces() -> None:
    """Flush pending LangSmith traces before Lambda returns.

    This should be called before Lambda exits to ensure all traces
    are sent to LangSmith. Safe to call even if LangSmith is not available.
    """
    if HAS_LANGSMITH and _get_langsmith_client is not None:
        try:
            client = _get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed")
        except Exception as e:
            logger.warning("Failed to flush LangSmith traces: %s", e)

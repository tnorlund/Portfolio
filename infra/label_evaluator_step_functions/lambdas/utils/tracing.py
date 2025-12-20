"""LangSmith tracing utilities for Lambda functions."""

import logging

logger = logging.getLogger(__name__)

# LangSmith tracing - flush before Lambda exits
try:
    from langsmith.run_trees import get_cached_client as get_langsmith_client

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    get_langsmith_client = None


def flush_langsmith_traces() -> None:
    """Flush pending LangSmith traces before Lambda returns.

    This should be called before Lambda exits to ensure all traces
    are sent to LangSmith. Safe to call even if LangSmith is not available.
    """
    if HAS_LANGSMITH and get_langsmith_client:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed")
        except Exception as e:
            logger.warning(f"Failed to flush LangSmith traces: {e}")

"""Retry logic with exponential backoff for LangGraph nodes."""

import asyncio
import logging
from functools import wraps
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    func: Callable,
    *args,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    should_retry: Optional[Callable[[Exception], bool]] = None,
    **kwargs,
) -> Any:
    """
    Retry a function with exponential backoff.

    Args:
        func: The async function to retry
        *args: Positional arguments to pass to func
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        max_delay: Maximum delay between retries in seconds
        backoff_factor: Multiplier for delay after each retry
        should_retry: Function to determine if error should be retried
        **kwargs: Keyword arguments to pass to func

    Returns:
        Result from func

    Raises:
        Last exception if all retries fail
    """
    
    def is_retriable_error(exception: Exception) -> bool:
        """Determine if an error should trigger a retry."""
        error_str = str(exception).lower()
        
        # Rate limiting
        if "rate limit" in error_str or "429" in error_str:
            logger.warning("⚠️ Rate limit detected (HTTP 429), will retry")
            return True
        
        # Network errors
        if any(term in error_str for term in ["timeout", "connection", "network", "unreachable"]):
            logger.warning("⚠️ Network error detected, will retry")
            return True
        
        # HTTP 5xx errors (server errors)
        if any(code in error_str for code in ["500", "502", "503", "504"]):
            logger.warning("⚠️ Server error detected, will retry")
            return True
        
        return False

    if should_retry is None:
        should_retry = is_retriable_error

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                # Last attempt failed
                logger.error(f"❌ All {max_retries} attempts failed. Last error: {e}")
                raise
            
            if not should_retry(e):
                # Error is not retriable
                logger.error(f"❌ Non-retriable error: {e}")
                raise
            
            # Calculate delay with exponential backoff
            delay = min(initial_delay * (backoff_factor ** attempt), max_delay)
            
            logger.warning(
                f"⚠️ Attempt {attempt + 1}/{max_retries} failed: {e}. "
                f"Retrying in {delay:.2f}s..."
            )
            
            await asyncio.sleep(delay)
    
    # Should never reach here
    raise RuntimeError("Retry loop ended unexpectedly")


def retry_node(max_retries: int = 3):
    """
    Decorator to add retry logic to LangGraph node functions.

    Usage:
        @retry_node(max_retries=3)
        async def phase1_currency_analysis(state, ollama_api_key):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await retry_with_backoff(
                func,
                *args,
                max_retries=max_retries,
                **kwargs
            )
        return wrapper
    return decorator


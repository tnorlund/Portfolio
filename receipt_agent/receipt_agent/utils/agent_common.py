"""
Common utilities for agent workflows.

This module provides shared functionality for creating LLM instances and
agent nodes with retry logic, eliminating code duplication across workflows.
"""

import logging
import random
import time
from typing import Any, Callable, Optional

from langchain_ollama import ChatOllama
from receipt_agent.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_ollama_llm(
    settings: Optional[Settings] = None,
    temperature: float = 0.0,
    timeout: int = 120,
) -> ChatOllama:
    """
    Create a ChatOllama LLM instance with standard configuration.

    Args:
        settings: Optional settings (uses get_settings() if None)
        temperature: LLM temperature (default 0.0 for deterministic)
        timeout: Request timeout in seconds

    Returns:
        Configured ChatOllama instance
    """
    if settings is None:
        settings = get_settings()

    api_key = settings.ollama_api_key.get_secret_value()

    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": (
                {"Authorization": f"Bearer {api_key}"} if api_key else {}
            ),
            "timeout": timeout,
        },
        temperature=temperature,
    )


def create_agent_node_with_retry(
    llm: ChatOllama,
    agent_name: str = "agent",
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_wait: float = 30.0,
) -> Callable:
    """
    Create an agent node function with retry logic.

    This handles:
    - Rate limit errors (429) - fail fast
    - Connection errors - retry with exponential backoff
    - Server errors (5xx) - retry with exponential backoff
    - Timeout errors - retry with exponential backoff

    Args:
        llm: The LLM to invoke
        agent_name: Name for logging (e.g., "harmonizer", "place_finder")
        max_retries: Maximum number of retries
        base_delay: Base delay in seconds for exponential backoff
        max_wait: Maximum wait time in seconds (caps exponential backoff)

    Returns:
        Agent node function that takes state and returns dict
    """

    def agent_node(state: Any) -> dict:
        """Call the LLM to decide next action with retry logic."""
        messages = state.messages

        last_error = None
        for attempt in range(max_retries):
            try:
                response = llm.invoke(messages)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    logger.debug(
                        f"{agent_name} tool calls: "
                        f"{[tc.get('name') if isinstance(tc, dict) else getattr(tc, 'name', str(tc)) for tc in response.tool_calls]}"
                    )

                return {"messages": [response]}

            except Exception as e:
                last_error = e
                error_str = str(e)

                # Check if this is a rate limit (429) - fail fast, don't retry
                is_rate_limit = (
                    "429" in error_str
                    or "rate limit" in error_str.lower()
                    or "rate_limit" in error_str.lower()
                    or "too many concurrent requests" in error_str.lower()
                    or "too many requests" in error_str.lower()
                    or "OllamaRateLimitError" in error_str
                )

                if is_rate_limit:
                    logger.warning(
                        f"Rate limit detected in {agent_name} (attempt {attempt + 1}): "
                        f"{error_str[:200]}. Failing immediately to trigger circuit breaker."
                    )
                    raise RuntimeError(
                        f"Rate limit error in {agent_name}: {error_str}"
                    ) from e

                # Check for connection errors
                is_connection_error = (
                    "status code: -1" in error_str
                    or ("status_code" in error_str and "-1" in error_str)
                    or (
                        "connection" in error_str.lower()
                        and (
                            "refused" in error_str.lower()
                            or "reset" in error_str.lower()
                            or "failed" in error_str.lower()
                            or "error" in error_str.lower()
                        )
                    )
                    or "dns" in error_str.lower()
                    or "name resolution" in error_str.lower()
                    or (
                        "network" in error_str.lower()
                        and "unreachable" in error_str.lower()
                    )
                )

                # For other retryable errors, use exponential backoff
                is_retryable = (
                    is_connection_error
                    or "500" in error_str
                    or "502" in error_str
                    or "503" in error_str
                    or "504" in error_str
                    or "Internal Server Error" in error_str
                    or "internal server error" in error_str.lower()
                    or "service unavailable" in error_str.lower()
                    or "timeout" in error_str.lower()
                    or "timed out" in error_str.lower()
                )

                if is_retryable and attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    jitter = random.uniform(0, base_delay)
                    wait_time = (base_delay * (2**attempt)) + jitter
                    wait_time = min(wait_time, max_wait)

                    error_type = (
                        "connection" if is_connection_error else "server"
                    )
                    logger.warning(
                        f"Ollama {error_type} error in {agent_name} "
                        f"(attempt {attempt + 1}/{max_retries}): {error_str[:200]}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    # Note: This is a sync function executed in a thread pool by LangGraph
                    # Using time.sleep is appropriate here
                    time.sleep(wait_time)
                    continue
                else:
                    # Not retryable or max retries reached
                    if attempt >= max_retries - 1:
                        logger.exception(
                            f"Ollama LLM call failed after {max_retries} attempts "
                            f"in {agent_name}: {error_str}"
                        )
                    raise RuntimeError(
                        f"Failed to get LLM response in {agent_name}: {error_str}"
                    ) from e

        # Should never reach here
        raise RuntimeError(
            f"Unexpected error: Failed to get LLM response in {agent_name}"
        ) from last_error

    return agent_node

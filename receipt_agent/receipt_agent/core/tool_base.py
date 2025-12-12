"""
Base tool interface and utilities for agent tools.

Provides common patterns for tool creation, retry logic, and tracing.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """
    Base class for agent tools.

    Provides common interface and utilities for tool implementations.
    Subclasses should implement the execute method.
    """

    def __init__(
        self,
        name: str,
        description: str,
        retry_on_error: bool = True,
        max_retries: int = 3,
    ):
        """
        Initialize base tool.

        Args:
            name: Tool name
            description: Tool description
            retry_on_error: Whether to retry on errors
            max_retries: Maximum retry attempts
        """
        self.name = name
        self.description = description
        self.retry_on_error = retry_on_error
        self.max_retries = max_retries

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """
        Execute the tool with given arguments.

        Args:
            **kwargs: Tool-specific arguments

        Returns:
            Tool result
        """
        pass

    def __call__(self, **kwargs: Any) -> Any:
        """Call the tool with retry logic."""
        if self.retry_on_error:
            return self._execute_with_retry(**kwargs)
        return self.execute(**kwargs)

    def _execute_with_retry(self, **kwargs: Any) -> Any:
        """Execute with retry logic."""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return self.execute(**kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"Tool {self.name} failed (attempt {attempt + 1}/{self.max_retries}): {e}"
                    )
                    continue
                else:
                    logger.error(
                        f"Tool {self.name} failed after {self.max_retries} attempts"
                    )
                    raise

        raise RuntimeError(
            f"Unexpected error in tool {self.name}"
        ) from last_error


def create_tool_with_retry(
    tool_func: Callable,
    max_retries: int = 3,
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Callable:
    """
    Wrap a tool function with retry logic.

    Args:
        tool_func: The tool function to wrap
        max_retries: Maximum retry attempts
        retry_on: Optional function to determine if an error should be retried.
                  If None, all errors are retried.

    Returns:
        Wrapped tool function with retry logic
    """

    def wrapped(**kwargs: Any) -> Any:
        last_error = None
        for attempt in range(max_retries):
            try:
                return tool_func(**kwargs)
            except Exception as e:
                last_error = e
                should_retry = retry_on(e) if retry_on else True
                if should_retry and attempt < max_retries - 1:
                    logger.warning(
                        f"Tool {tool_func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    continue
                else:
                    raise

        raise RuntimeError(
            f"Unexpected error in tool {tool_func.__name__}"
        ) from last_error

    return wrapped


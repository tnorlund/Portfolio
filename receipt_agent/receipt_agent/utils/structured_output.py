"""Utilities for strict LLM structured-output invocation."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .llm_factory import LLMRateLimitError

T = TypeVar("T")

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}

DEFAULT_STRICT_STRUCTURED_OUTPUT = True
DEFAULT_STRUCTURED_OUTPUT_RETRIES = 3

_INITIAL_RETRY_BACKOFF_SECONDS = 0.1
_MAX_RETRY_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class StructuredOutputResult(Generic[T]):
    """Result of a structured-output invocation attempt."""

    success: bool
    response: T | None
    attempts: int
    error_type: str | None = None
    error_message: str | None = None


def _retry_backoff_seconds(attempt: int) -> float:
    """Return exponential backoff delay for a failed attempt."""
    return float(
        min(
            _MAX_RETRY_BACKOFF_SECONDS,
            _INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)),
        )
    )


def build_structured_failure_decisions(
    count: int,
    *,
    failure_reason: str,
    extra_fields: Mapping[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic NEEDS_REVIEW decisions for strict-mode failures."""
    base_decision: dict[str, Any] = {
        "decision": "NEEDS_REVIEW",
        "reasoning": failure_reason,
        "suggested_label": None,
        "confidence": "low",
    }
    if extra_fields:
        base_decision.update(extra_fields)
    return [base_decision.copy() for _ in range(count)]


def _parse_bool_env(
    env_name: str,
    default: bool,
    logger_instance: logging.Logger,
) -> bool:
    raw_value = os.environ.get(env_name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    logger_instance.warning(
        "Invalid %s value '%s'; falling back to %s",
        env_name,
        raw_value,
        default,
    )
    return default


def _parse_int_env(
    env_name: str,
    default: int,
    minimum: int,
    logger_instance: logging.Logger,
) -> int:
    raw_value = os.environ.get(env_name)
    if raw_value is None:
        return default

    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        logger_instance.warning(
            "Invalid %s value '%s'; falling back to %d",
            env_name,
            raw_value,
            default,
        )
        return default

    return max(minimum, parsed)


def get_structured_output_settings(
    *,
    logger_instance: logging.Logger | None = None,
) -> tuple[bool, int]:
    """Return (strict_enabled, retries) from environment with safe defaults."""
    resolved_logger = logger_instance or logger
    strict_enabled = _parse_bool_env(
        env_name="LLM_STRICT_STRUCTURED_OUTPUT",
        default=DEFAULT_STRICT_STRUCTURED_OUTPUT,
        logger_instance=resolved_logger,
    )
    retries = _parse_int_env(
        env_name="LLM_STRUCTURED_OUTPUT_RETRIES",
        default=DEFAULT_STRUCTURED_OUTPUT_RETRIES,
        minimum=1,
        logger_instance=resolved_logger,
    )
    return strict_enabled, retries


def invoke_structured_with_retry(
    *,
    llm: Any,
    schema: type[T],
    input_payload: Any,
    retries: int,
    config: Mapping[str, Any] | None = None,
) -> StructuredOutputResult[T]:
    """Invoke with structured output retries (sync)."""
    retries = max(retries, 1)

    if not hasattr(llm, "with_structured_output"):
        return StructuredOutputResult(
            success=False,
            response=None,
            attempts=0,
            error_type="missing_with_structured_output",
            error_message=(
                f"LLM {type(llm).__name__} does not support "
                "with_structured_output"
            ),
        )

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            structured_llm = llm.with_structured_output(schema)
            if config is None:
                response: T = structured_llm.invoke(input_payload)
            else:
                response = structured_llm.invoke(input_payload, config=config)
            return StructuredOutputResult(
                success=True,
                response=response,
                attempts=attempt,
            )
        except LLMRateLimitError:
            raise
        except Exception as error:
            logger.warning(
                "Structured output attempt %d/%d failed: %s",
                attempt,
                retries,
                error,
            )
            last_error = error
            if attempt < retries:
                time.sleep(_retry_backoff_seconds(attempt))

    return StructuredOutputResult(
        success=False,
        response=None,
        attempts=retries,
        error_type=type(last_error).__name__ if last_error else None,
        error_message=str(last_error) if last_error else None,
    )


async def ainvoke_structured_with_retry(
    *,
    llm: Any,
    schema: type[T],
    input_payload: Any,
    retries: int,
    config: Mapping[str, Any] | None = None,
) -> StructuredOutputResult[T]:
    """Invoke with structured output retries (async)."""
    retries = max(retries, 1)

    if not hasattr(llm, "with_structured_output"):
        return StructuredOutputResult(
            success=False,
            response=None,
            attempts=0,
            error_type="missing_with_structured_output",
            error_message=(
                f"LLM {type(llm).__name__} does not support "
                "with_structured_output"
            ),
        )

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            structured_llm = llm.with_structured_output(schema)
            if hasattr(structured_llm, "ainvoke"):
                if config is None:
                    response: T = await structured_llm.ainvoke(input_payload)
                else:
                    response = await structured_llm.ainvoke(
                        input_payload, config=config
                    )
            else:
                if config is None:
                    response = await asyncio.to_thread(
                        structured_llm.invoke,
                        input_payload,
                    )
                else:
                    response = await asyncio.to_thread(
                        structured_llm.invoke,
                        input_payload,
                        config=config,
                    )

            return StructuredOutputResult(
                success=True,
                response=response,
                attempts=attempt,
            )
        except LLMRateLimitError:
            raise
        except Exception as error:
            logger.warning(
                "Async structured output attempt %d/%d failed: %s",
                attempt,
                retries,
                error,
            )
            last_error = error
            if attempt < retries:
                await asyncio.sleep(_retry_backoff_seconds(attempt))

    return StructuredOutputResult(
        success=False,
        response=None,
        attempts=retries,
        error_type=type(last_error).__name__ if last_error else None,
        error_message=str(last_error) if last_error else None,
    )

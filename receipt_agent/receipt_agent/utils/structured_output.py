"""Utilities for strict LLM structured-output invocation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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


def _build_json_schema_hint(schema: type[T]) -> str:
    """Build a short instruction telling the LLM to respond in JSON matching the schema."""
    try:
        json_schema = json.dumps(schema.model_json_schema(), indent=2)
    except Exception:
        json_schema = schema.__name__
    return (
        f"Respond ONLY with valid JSON matching this schema (no markdown, "
        f"no prose, no code fences):\n{json_schema}"
    )


def _append_json_hint(input_payload: Any, schema: type[T]) -> Any:
    """Append a JSON schema hint to the input payload for raw fallback calls."""
    hint = _build_json_schema_hint(schema)
    # Handle list-of-messages format (most common)
    if isinstance(input_payload, list):
        from copy import copy

        payload = copy(input_payload)
        # Append as a HumanMessage-style tuple or dict
        payload.append(("human", hint))
        return payload
    # Handle string prompt
    if isinstance(input_payload, str):
        return input_payload + "\n\n" + hint
    # Unknown format — return as-is and hope for the best
    return input_payload


def _try_repair_and_parse(
    raw_text: str,
    schema: type[T],
) -> T | None:
    """Try to repair malformed LLM JSON and parse with the Pydantic schema.

    Handles three common failure modes:
    1. Markdown wrapping: ```json ... ``` or leading prose before JSON
    2. Double opening braces: {\\n{ → {
    3. Array-to-object wrapping: [...] → {"evaluations": [...]} or {"reviews": [...]}

    Returns the parsed model on success, None on failure.
    """
    # Step 1: Strip markdown code fences and leading prose
    cleaned = raw_text.strip()
    if "```" in cleaned:
        match = re.search(
            r"```[a-zA-Z0-9_-]*\s*(.*?)\s*```", cleaned, re.DOTALL
        )
        if match:
            cleaned = match.group(1).strip()

    # Strip leading non-JSON characters (periods, prose, whitespace)
    first_brace = -1
    first_bracket = -1
    for i, ch in enumerate(cleaned):
        if ch == "{" and first_brace == -1:
            first_brace = i
        if ch == "[" and first_bracket == -1:
            first_bracket = i
        if first_brace != -1 and first_bracket != -1:
            break

    json_start = -1
    if first_brace != -1 and first_bracket != -1:
        json_start = min(first_brace, first_bracket)
    elif first_brace != -1:
        json_start = first_brace
    elif first_bracket != -1:
        json_start = first_bracket

    if json_start > 0:
        cleaned = cleaned[json_start:]

    # Step 2: Fix double braces: "{\n{" → "{" and "}}" → "}"
    if re.match(r"^\{\s*\{", cleaned):
        cleaned = re.sub(r"^\{\s*\{", "{", cleaned)
        if re.search(r"\}\s*\}$", cleaned):
            cleaned = re.sub(r"\}\s*\}$", "}", cleaned)

    # Step 3: Try parsing as-is first
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try stripping trailing junk after last } or ]
        last_brace = cleaned.rfind("}")
        last_bracket = cleaned.rfind("]")
        last_close = max(last_brace, last_bracket)
        if last_close > 0:
            try:
                obj = json.loads(cleaned[: last_close + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Step 4: If parsed as array, wrap in object for Pydantic model
    if isinstance(obj, list):
        # Detect which key the schema expects
        list_field = None
        for field_name, field_info in schema.model_fields.items():
            annotation = field_info.annotation
            origin = getattr(annotation, "__origin__", None)
            if origin is list:
                list_field = field_name
                break
        if list_field:
            obj = {list_field: obj}

    # Step 5: Validate with Pydantic
    try:
        return schema.model_validate(obj)
    except Exception:
        return None


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
            structured_llm = llm.with_structured_output(
                schema, include_raw=True
            )
            if config is None:
                result = structured_llm.invoke(input_payload)
            else:
                result = structured_llm.invoke(input_payload, config=config)
            if result.get("parsing_error") is not None:
                # Try to repair the raw response before giving up
                raw_msg = result.get("raw")
                raw_text = (
                    getattr(raw_msg, "content", "") if raw_msg else ""
                )
                if raw_text:
                    repaired = _try_repair_and_parse(raw_text, schema)
                    if repaired is not None:
                        logger.info(
                            "Structured output repaired on attempt %d/%d",
                            attempt,
                            retries,
                        )
                        return StructuredOutputResult(
                            success=True,
                            response=repaired,
                            attempts=attempt,
                        )
                raise result["parsing_error"]
            response: T = result["parsed"]
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

    # All structured attempts failed — try one raw LLM call + manual repair
    try:
        logger.info("Attempting raw LLM fallback for %s", schema.__name__)
        hinted_payload = _append_json_hint(input_payload, schema)
        if config is None:
            raw_result = llm.invoke(hinted_payload)
        else:
            raw_result = llm.invoke(hinted_payload, config=config)
        raw_text = getattr(raw_result, "content", "")
        if raw_text:
            repaired = _try_repair_and_parse(raw_text, schema)
            if repaired is not None:
                logger.info(
                    "Raw LLM fallback repaired response for %s",
                    schema.__name__,
                )
                return StructuredOutputResult(
                    success=True,
                    response=repaired,
                    attempts=retries + 1,
                )
            else:
                logger.warning(
                    "Raw LLM fallback returned unparseable text for %s: %.200s",
                    schema.__name__,
                    raw_text,
                )
        else:
            logger.warning(
                "Raw LLM fallback returned empty content for %s",
                schema.__name__,
            )
    except LLMRateLimitError:
        raise
    except Exception as fallback_error:
        logger.warning(
            "Raw LLM fallback also failed for %s: %s",
            schema.__name__,
            fallback_error,
        )

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
            structured_llm = llm.with_structured_output(
                schema, include_raw=True
            )
            if hasattr(structured_llm, "ainvoke"):
                if config is None:
                    result = await structured_llm.ainvoke(input_payload)
                else:
                    result = await structured_llm.ainvoke(
                        input_payload, config=config
                    )
            else:
                if config is None:
                    result = await asyncio.to_thread(
                        structured_llm.invoke,
                        input_payload,
                    )
                else:
                    result = await asyncio.to_thread(
                        structured_llm.invoke,
                        input_payload,
                        config=config,
                    )

            if result.get("parsing_error") is not None:
                # Try to repair the raw response before giving up
                raw_msg = result.get("raw")
                raw_text = (
                    getattr(raw_msg, "content", "") if raw_msg else ""
                )
                if raw_text:
                    repaired = _try_repair_and_parse(raw_text, schema)
                    if repaired is not None:
                        logger.info(
                            "Async structured output repaired on attempt %d/%d",
                            attempt,
                            retries,
                        )
                        return StructuredOutputResult(
                            success=True,
                            response=repaired,
                            attempts=attempt,
                        )
                raise result["parsing_error"]
            response: T = result["parsed"]
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

    # All structured attempts failed — try one raw LLM call + manual repair
    try:
        logger.info(
            "Attempting async raw LLM fallback for %s", schema.__name__
        )
        hinted_payload = _append_json_hint(input_payload, schema)
        if hasattr(llm, "ainvoke"):
            if config is None:
                raw_result = await llm.ainvoke(hinted_payload)
            else:
                raw_result = await llm.ainvoke(
                    hinted_payload, config=config
                )
        else:
            if config is None:
                raw_result = await asyncio.to_thread(
                    llm.invoke, hinted_payload
                )
            else:
                raw_result = await asyncio.to_thread(
                    llm.invoke, hinted_payload, config=config
                )
        raw_text = getattr(raw_result, "content", "")
        if raw_text:
            repaired = _try_repair_and_parse(raw_text, schema)
            if repaired is not None:
                logger.info(
                    "Async raw LLM fallback repaired response for %s",
                    schema.__name__,
                )
                return StructuredOutputResult(
                    success=True,
                    response=repaired,
                    attempts=retries + 1,
                )
            else:
                logger.warning(
                    "Async raw LLM fallback returned unparseable text for %s: %.200s",
                    schema.__name__,
                    raw_text,
                )
        else:
            logger.warning(
                "Async raw LLM fallback returned empty content for %s",
                schema.__name__,
            )
    except LLMRateLimitError:
        raise
    except Exception as fallback_error:
        logger.warning(
            "Async raw LLM fallback also failed for %s: %s",
            schema.__name__,
            fallback_error,
        )

    return StructuredOutputResult(
        success=False,
        response=None,
        attempts=retries,
        error_type=type(last_error).__name__ if last_error else None,
        error_message=str(last_error) if last_error else None,
    )

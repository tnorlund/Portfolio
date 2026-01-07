"""JSON field parsing utilities for LangSmith traces.

This module provides utilities for parsing JSON string fields
from Parquet exports into typed structures.
"""

import json
import logging
from typing import Any, Optional

from receipt_langsmith.entities.base import RuntimeInfo, TraceMetadata

logger = logging.getLogger(__name__)


def parse_json(value: Optional[str]) -> Optional[dict[str, Any] | list[Any]]:
    """Safely parse a JSON string.

    Args:
        value: JSON string to parse, or None.

    Returns:
        Parsed JSON (dict or list), or None if parsing fails.
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        logger.debug("Failed to parse JSON: %s", e)
        return None


def parse_extra(extra_json: Optional[str]) -> tuple[TraceMetadata, RuntimeInfo]:
    """Parse the 'extra' JSON field into metadata and runtime info.

    The 'extra' field typically has this structure:
    ```json
    {
        "metadata": {
            "execution_id": "...",
            "merchant_name": "...",
            "image_id": "...",
            "receipt_id": 1
        },
        "runtime": {
            "sdk_version": "0.6.0",
            "langchain_core_version": "1.2.6",
            "runtime_version": "3.12.12",
            "platform": "Linux-..."
        }
    }
    ```

    Args:
        extra_json: JSON string from the 'extra' field.

    Returns:
        Tuple of (TraceMetadata, RuntimeInfo) with parsed values.
    """
    extra = parse_json(extra_json)
    if not isinstance(extra, dict):
        return TraceMetadata(), RuntimeInfo()

    # Parse metadata
    metadata_dict = extra.get("metadata", {}) or {}
    metadata = TraceMetadata(
        execution_id=metadata_dict.get("execution_id"),
        merchant_name=metadata_dict.get("merchant_name"),
        image_id=metadata_dict.get("image_id"),
        receipt_id=metadata_dict.get("receipt_id"),
        state_name=metadata_dict.get("state_name"),
        model=metadata_dict.get("model"),
        temperature=metadata_dict.get("temperature"),
        provider=metadata_dict.get("provider"),
    )

    # Parse runtime
    runtime_dict = extra.get("runtime", {}) or {}
    runtime = RuntimeInfo(
        sdk_version=runtime_dict.get("sdk_version"),
        langchain_core_version=runtime_dict.get("langchain_core_version"),
        runtime_version=runtime_dict.get("runtime_version"),
        platform=runtime_dict.get("platform"),
    )

    return metadata, runtime


def parse_tags(tags_json: Optional[str]) -> list[str]:
    """Parse the 'tags' JSON array field.

    Args:
        tags_json: JSON array string from the 'tags' field.

    Returns:
        List of tag strings.
    """
    tags = parse_json(tags_json)
    if isinstance(tags, list):
        return [str(t) for t in tags if t]
    return []


def parse_inputs(inputs_json: Optional[str]) -> dict[str, Any]:
    """Parse the 'inputs' JSON field.

    Args:
        inputs_json: JSON string from the 'inputs' field.

    Returns:
        Dict of input key-value pairs.
    """
    inputs = parse_json(inputs_json)
    return inputs if isinstance(inputs, dict) else {}


def parse_outputs(outputs_json: Optional[str]) -> dict[str, Any]:
    """Parse the 'outputs' JSON field.

    Args:
        outputs_json: JSON string from the 'outputs' field.

    Returns:
        Dict of output key-value pairs.
    """
    outputs = parse_json(outputs_json)
    return outputs if isinstance(outputs, dict) else {}

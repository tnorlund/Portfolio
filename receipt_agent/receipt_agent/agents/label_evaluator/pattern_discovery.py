"""Line item pattern discovery for receipts.

This module provides functions to discover line item patterns from receipt data
using LLM analysis. It can be used both locally for development and in Lambda.

The hybrid approach combines:
1. Raw receipt structure (lines with y-positions and words)
2. Chroma-enriched validated label examples from the same merchant

Usage:
    from receipt_agent.agents.label_evaluator.pattern_discovery import (
        build_receipt_structure,
        build_discovery_prompt,
        discover_patterns_with_llm,
        query_label_examples_from_chroma,
    )

    # Fetch receipt data
    receipts_data = build_receipt_structure(dynamo_client, "Sprouts Farmers Market")

    # Optionally get Chroma-validated examples
    label_examples = query_label_examples_from_chroma(
        chroma_client, "Sprouts Farmers Market"
    )

    # Build prompt with both sources
    prompt = build_discovery_prompt(
        "Sprouts Farmers Market",
        receipts_data,
        label_examples=label_examples,
    )

    # Call LLM
    patterns = discover_patterns_with_llm(prompt)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from receipt_agent.prompts.structured_outputs import PatternDiscoveryResponse

logger = logging.getLogger(__name__)


# Labels relevant for line item pattern discovery
LINE_ITEM_LABELS = [
    "PRODUCT_NAME",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "QUANTITY",
    "DISCOUNT",
    "TAX",
]


class DynamoClientProtocol(Protocol):
    """Protocol for DynamoDB client - allows duck typing."""

    def get_receipt_places_by_merchant(
        self, merchant_name: str, limit: int = 3
    ) -> tuple[list, Any]: ...

    def list_receipt_words_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list | tuple[list, Any]: ...

    def list_receipt_word_labels_for_receipt(
        self, image_id: str, receipt_id: int
    ) -> tuple[list, Any]: ...


@dataclass
class LabelExample:
    """A validated label example from ChromaDB."""

    word_text: str
    label: str
    x_position: float
    y_position: float
    left_neighbor: str
    right_neighbor: str


@dataclass
class LabelExamples:
    """Collection of validated label examples by label type."""

    examples_by_label: dict[str, list[LabelExample]] = field(
        default_factory=dict
    )
    merchant_name: str = ""
    total_examples: int = 0

    def add_example(self, label: str, example: LabelExample) -> None:
        """Add an example to the collection."""
        if label not in self.examples_by_label:
            self.examples_by_label[label] = []
        self.examples_by_label[label].append(example)
        self.total_examples += 1

    def get_examples(self, label: str, limit: int = 5) -> list[LabelExample]:
        """Get examples for a specific label."""
        return self.examples_by_label.get(label, [])[:limit]

    def format_for_prompt(self) -> str:
        """Format examples for inclusion in LLM prompt."""
        if not self.examples_by_label:
            return ""

        lines = [f'VALIDATED LABEL EXAMPLES FROM "{self.merchant_name}":']
        for label in LINE_ITEM_LABELS:
            examples = self.get_examples(label, limit=5)
            if examples:
                example_strs = []
                for ex in examples:
                    context = f"[{ex.left_neighbor}] {ex.word_text} [{ex.right_neighbor}]"
                    example_strs.append(
                        f'"{ex.word_text}" at x={ex.x_position:.2f} ({context})'
                    )
                lines.append(f"  {label}: {', '.join(example_strs)}")
            else:
                lines.append(f"  {label}: (no validated examples found)")

        return "\n".join(lines)


@dataclass
class PatternDiscoveryConfig:
    """Configuration for pattern discovery."""

    ollama_api_key: str = ""
    ollama_base_url: str = "https://ollama.com"
    ollama_model: str = "gpt-oss:120b-cloud"
    max_receipts: int = 3
    max_lines_per_receipt: int = 80
    focus_on_line_items: bool = True  # Smart line selection

    @classmethod
    def from_env(cls) -> "PatternDiscoveryConfig":
        """Create config from environment variables."""
        return cls(
            ollama_api_key=os.environ.get("OLLAMA_API_KEY", ""),
            ollama_base_url=os.environ.get(
                "OLLAMA_BASE_URL", "https://ollama.com"
            ),
            ollama_model=os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
        )


# =============================================================================
# ChromaDB Query Functions
# =============================================================================


def query_label_examples_from_chroma(
    chroma_client: Any,
    merchant_name: str,
    embed_fn: Any,
    labels: list[str] | None = None,
    max_per_label: int = 10,
) -> LabelExamples:
    """Query ChromaDB for validated label examples from a specific merchant.

    Uses semantic search with label names to find examples.

    Args:
        chroma_client: ChromaDB client (DualChromaClient or similar)
        merchant_name: Name of the merchant to query
        embed_fn: Embedding function to generate query embeddings
        labels: List of label types to query (defaults to LINE_ITEM_LABELS)
        max_per_label: Maximum examples to fetch per label type

    Returns:
        LabelExamples containing validated examples by label type
    """
    if labels is None:
        labels = LINE_ITEM_LABELS

    result = LabelExamples(merchant_name=merchant_name)

    try:
        for label in labels:
            try:
                # Search using the label name as query text
                # This finds words that are semantically similar to the label concept
                query_embedding = embed_fn([label.lower().replace("_", " ")])

                query_result = chroma_client.query(
                    collection_name="words",
                    query_embeddings=query_embedding,
                    n_results=max_per_label * 3,  # Get extra to filter
                    where={
                        "$and": [
                            {"merchant_name": {"$eq": merchant_name}},
                            {
                                "label_status": {
                                    "$in": ["validated", "auto_suggested"]
                                }
                            },
                        ]
                    },
                    include=["metadatas", "distances"],
                )

                metadatas = query_result.get("metadatas", [[]])[0]
                count = 0
                for metadata in metadatas:
                    if count >= max_per_label:
                        break
                    # Check if this word has the label we're looking for
                    valid_labels_str = metadata.get("valid_labels", "")
                    if label in valid_labels_str:
                        example = LabelExample(
                            word_text=metadata.get("text", ""),
                            label=label,
                            x_position=metadata.get("x", 0.5),
                            y_position=metadata.get("y", 0.5),
                            left_neighbor=metadata.get("left", "<EDGE>"),
                            right_neighbor=metadata.get("right", "<EDGE>"),
                        )
                        result.add_example(label, example)
                        count += 1

            except Exception as e:
                logger.debug("Error querying label %s: %s", label, e)
                continue

    except Exception as e:
        logger.warning("Error querying Chroma for label examples: %s", e)

    logger.info(
        "Found %d label examples for %s from Chroma",
        result.total_examples,
        merchant_name,
    )
    return result


def query_label_examples_simple(
    chroma_client: Any,
    merchant_name: str,
    embed_fn: Any | None = None,
    max_total: int = 50,
) -> LabelExamples:
    """Query ChromaDB for label examples using a simpler approach.

    Uses semantic search for "receipt line item" to find relevant words,
    then filters by merchant and groups by label.

    Args:
        chroma_client: ChromaDB client
        merchant_name: Name of the merchant to query
        embed_fn: Embedding function (required for query)
        max_total: Maximum total examples to fetch

    Returns:
        LabelExamples containing validated examples by label type
    """
    result = LabelExamples(merchant_name=merchant_name)

    if embed_fn is None:
        logger.warning("embed_fn required for query_label_examples_simple")
        return result

    try:
        # Generate embedding for a general query
        query_embedding = embed_fn(["product price total quantity"])

        # Query for validated words from this merchant
        query_result = chroma_client.query(
            collection_name="words",
            query_embeddings=query_embedding,
            n_results=max_total,
            where={
                "$and": [
                    {"merchant_name": {"$eq": merchant_name}},
                    {"label_status": {"$in": ["validated", "auto_suggested"]}},
                ]
            },
            include=["metadatas", "distances"],
        )

        metadatas = query_result.get("metadatas", [[]])[0]
        for metadata in metadatas:
            # Parse valid_labels (comma-separated string)
            valid_labels_str = metadata.get("valid_labels", "")
            valid_labels = [
                lbl.strip()
                for lbl in valid_labels_str.split(",")
                if lbl.strip()
            ]

            for label in valid_labels:
                if label in LINE_ITEM_LABELS:
                    example = LabelExample(
                        word_text=metadata.get("text", ""),
                        label=label,
                        x_position=metadata.get("x", 0.5),
                        y_position=metadata.get("y", 0.5),
                        left_neighbor=metadata.get("left", "<EDGE>"),
                        right_neighbor=metadata.get("right", "<EDGE>"),
                    )
                    result.add_example(label, example)

    except Exception as e:
        logger.warning("Error querying Chroma for label examples: %s", e)

    logger.info(
        "Found %d label examples for %s from Chroma",
        result.total_examples,
        merchant_name,
    )
    return result


def build_receipt_structure(
    dynamo_client: DynamoClientProtocol,
    merchant_name: str,
    limit: int = 3,
    focus_on_line_items: bool = True,
    max_lines: int = 80,
) -> list[dict]:
    """Build structured receipt data for LLM analysis.

    Args:
        dynamo_client: DynamoDB client for fetching receipt data
        merchant_name: Name of the merchant to analyze
        limit: Maximum number of receipts to fetch
        focus_on_line_items: If True, prioritize the line items section over header
        max_lines: Maximum lines per receipt to include

    Returns:
        List of receipt structures with lines and word data
    """
    result = dynamo_client.get_receipt_places_by_merchant(
        merchant_name, limit=limit
    )
    receipt_places = result[0] if result else []

    if not receipt_places:
        logger.warning(
            "No receipt places found for merchant: %s", merchant_name
        )
        return []

    receipts_data = []

    for place in receipt_places:
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                place.image_id, place.receipt_id
            )
            # Handle implementations that return (items, last_key) tuple
            if isinstance(words, tuple):
                words = words[0]
            labels_result = dynamo_client.list_receipt_word_labels_for_receipt(
                place.image_id, place.receipt_id
            )
            labels = labels_result[0] if labels_result else []
        except Exception:
            logger.exception(
                "Error fetching receipt data for %s#%s",
                place.image_id,
                place.receipt_id,
            )
            continue

        if not words:
            continue

        # Build label lookup (only VALID labels)
        labels_by_word: dict[tuple[int, int], list[str]] = {}
        for label in labels:
            if label.validation_status == "VALID":
                key = (label.line_id, label.word_id)
                if key not in labels_by_word:
                    labels_by_word[key] = []
                labels_by_word[key].append(label.label)

        # Group words by line
        lines: dict[int, list] = {}
        for word in words:
            if word.line_id not in lines:
                lines[word.line_id] = []
            lines[word.line_id].append(word)

        # Sort lines by y-position (top to bottom)
        sorted_lines = []
        for line_id, line_words in lines.items():
            avg_y = sum(w.bounding_box["y"] for w in line_words) / len(
                line_words
            )
            line_words.sort(key=lambda w: w.bounding_box["x"])
            sorted_lines.append((line_id, avg_y, line_words))
        sorted_lines.sort(key=lambda x: -x[1])  # Highest y (top) first

        # Build structured representation - include ALL lines
        receipt_lines = []
        lines_with_line_item_labels = []

        for idx, (line_id, y_pos, line_words) in enumerate(sorted_lines):
            words_data = []
            has_line_item_label = False
            for word in line_words:
                key = (line_id, word.word_id)
                word_labels = labels_by_word.get(key, [])
                # Check if any label is a line item label
                if any(lbl in LINE_ITEM_LABELS for lbl in word_labels):
                    has_line_item_label = True
                words_data.append(
                    {
                        "text": word.text,
                        "x": round(word.bounding_box["x"], 3),
                        "labels": word_labels if word_labels else None,
                    }
                )

            line_data = {
                "line_id": line_id,
                "y": round(y_pos, 3),
                "words": words_data,
            }
            receipt_lines.append(line_data)

            if has_line_item_label:
                lines_with_line_item_labels.append(idx)

        # Smart line selection: focus on line items section
        if focus_on_line_items and lines_with_line_item_labels:
            # Find the range of lines containing line item labels
            first_item_idx = min(lines_with_line_item_labels)
            last_item_idx = max(lines_with_line_item_labels)

            # Include some context before and after
            context_lines = 5
            start_idx = max(0, first_item_idx - context_lines)
            end_idx = min(
                len(receipt_lines), last_item_idx + context_lines + 1
            )

            # If the section is too large, prioritize from the start
            if end_idx - start_idx > max_lines:
                end_idx = start_idx + max_lines

            selected_lines = receipt_lines[start_idx:end_idx]
            logger.info(
                "Selected lines %d-%d (of %d total) for line items section",
                start_idx,
                end_idx,
                len(receipt_lines),
            )
        else:
            # No line item labels found - include all lines up to limit
            selected_lines = receipt_lines[:max_lines]

        if selected_lines:
            receipts_data.append(
                {
                    "receipt_id": f"{place.image_id[:8]}_{place.receipt_id}",
                    "image_id": place.image_id,
                    "receipt_num": place.receipt_id,
                    "line_count": len(selected_lines),
                    "total_lines": len(receipt_lines),
                    "lines": selected_lines,
                }
            )

    return receipts_data[:limit]


def build_discovery_prompt(
    merchant_name: str,
    receipts_data: list[dict],
    label_examples: LabelExamples | None = None,
) -> str:
    """Build the LLM prompt for pattern discovery.

    Args:
        merchant_name: Name of the merchant
        receipts_data: List of receipt structures from build_receipt_structure
        label_examples: Optional Chroma-validated label examples

    Returns:
        Prompt string for the LLM
    """
    # Format receipt data for the prompt
    simplified = []
    for receipt in receipts_data[:2]:
        lines = []
        # Include more lines now that we're focused on line items section
        for line in receipt["lines"][:60]:
            words_str = " ".join(
                (
                    f"{w['text']}[{','.join(w['labels'])}]"
                    if w["labels"]
                    else w["text"]
                )
                for w in line["words"]
            )
            lines.append(f"  y={line['y']:.2f} | {words_str}")
        simplified.append("\n".join(lines))

    receipts_text = "\n\n---\n\n".join(simplified)

    # Build the label examples section if available
    label_examples_section = ""
    if label_examples and label_examples.total_examples > 0:
        label_examples_section = f"""

{label_examples.format_for_prompt()}

Use these validated examples to understand where each label type typically appears.
"""

    prompt = f"""Analyze the following receipt data from "{merchant_name}".

Each line shows: y-position | words (with [LABELS] for labeled words)
- Words WITHOUT labels are shown as plain text (e.g., product names, descriptions)
- Words WITH labels are shown as text[LABEL1,LABEL2] format
- The y-position indicates vertical position (higher = toward top of receipt)
{label_examples_section}
RECEIPT DATA:
{receipts_text}

---

STEP 1: Determine the receipt type by analyzing the structure:

- ITEMIZED: Multiple products/services listed with individual prices (grocery, retail, restaurant with itemized bill)
- SERVICE: Single charge, appointment-based, or non-itemized format (medical visit, spa service, parking, simple transaction)

STEP 2: If ITEMIZED, identify the line item patterns. If SERVICE, skip pattern analysis.

Respond with ONLY a JSON object (no other text):

If SERVICE receipt:
{{
  "merchant": "{merchant_name}",
  "receipt_type": "service",
  "receipt_type_reason": "brief explanation of why this is a service receipt",
  "item_structure": null,
  "lines_per_item": null,
  "item_start_marker": null,
  "item_end_marker": null,
  "barcode_pattern": null,
  "x_position_zones": null,
  "label_positions": null,
  "grouping_rule": null,
  "special_markers": null,
  "product_name_patterns": null
}}

If ITEMIZED receipt:
{{
  "merchant": "{merchant_name}",
  "receipt_type": "itemized",
  "receipt_type_reason": "brief explanation of why this is an itemized receipt",
  "item_structure": "single-line" or "multi-line",
  "lines_per_item": {{"typical": N, "min": N, "max": N}},
  "item_start_marker": "description of what marks the start of a new item",
  "item_end_marker": "description of what marks the end of an item",
  "barcode_pattern": "regex pattern for SKU/barcode if found, or null",
  "x_position_zones": {{
    "left": [0.0, 0.3],
    "center": [0.3, 0.6],
    "right": [0.6, 1.0]
  }},
  "label_positions": {{
    "PRODUCT_NAME": "left" or "center" or "right" or "varies",
    "LINE_TOTAL": "left" or "center" or "right",
    "UNIT_PRICE": "left" or "center" or "right" or "not_found",
    "QUANTITY": "left" or "center" or "right" or "varies"
  }},
  "grouping_rule": "plain english description of how to group words into line items",
  "special_markers": ["list of special markers like <A>, *, etc. if found"],
  "product_name_patterns": ["common patterns for product names"]
}}

Respond with ONLY the JSON object, no markdown code blocks or other text."""

    return prompt


def discover_patterns_with_llm(
    prompt: str,
    config: PatternDiscoveryConfig | None = None,
    trace_ctx: Any | None = None,
) -> dict | None:
    """Call LLM to discover patterns.

    Args:
        prompt: The prompt to send to the LLM
        config: Configuration for the LLM call (uses env vars if None)
        trace_ctx: Optional LangSmith trace context for tracing

    Returns:
        Parsed JSON response from LLM, or None if failed
    """
    if config is None:
        config = PatternDiscoveryConfig.from_env()

    if not config.ollama_api_key:
        logger.error("OLLAMA_API_KEY not set")
        return None

    llm_inputs = {
        "model": config.ollama_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a receipt analysis expert. Respond only with valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
    }

    try:
        # If we have a trace context with child_trace, use it
        if trace_ctx is not None:
            return _call_llm_with_tracing(
                prompt, config, llm_inputs, trace_ctx
            )

        # No tracing - direct call
        return _call_llm_direct(config, llm_inputs)

    except json.JSONDecodeError as e:
        logger.exception("Failed to parse LLM response as JSON: %s", e)
        return None
    except Exception:
        logger.exception("LLM call failed")
        return None


def _call_llm_direct(
    config: PatternDiscoveryConfig, llm_inputs: dict
) -> dict | None:
    """Make LLM call without tracing."""
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{config.ollama_base_url}/api/chat",
            headers={
                "Authorization": f"Bearer {config.ollama_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.ollama_model,
                "messages": llm_inputs["messages"],
                "stream": False,
                "options": {"temperature": 0.1},
            },
        )
        response.raise_for_status()
        result = response.json()

    content = result.get("message", {}).get("content", "")
    return _parse_llm_response(content)


def _call_llm_with_tracing(
    prompt: str,
    config: PatternDiscoveryConfig,
    llm_inputs: dict,
    trace_ctx: Any,
) -> dict | None:
    """Make LLM call with LangSmith tracing."""
    # Import child_trace dynamically to avoid import errors when not in Lambda
    try:
        from tracing import child_trace
    except ImportError:
        # Fall back to direct call if tracing not available
        logger.warning("Tracing not available, falling back to direct call")
        return _call_llm_direct(config, llm_inputs)

    with child_trace(
        "ollama_pattern_discovery",
        trace_ctx,
        run_type="llm",
        metadata={
            "model": config.ollama_model,
            "prompt_length": len(prompt),
        },
        inputs=llm_inputs,
    ) as llm_trace_ctx:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{config.ollama_base_url}/api/chat",
                headers={
                    "Authorization": f"Bearer {config.ollama_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.ollama_model,
                    "messages": llm_inputs["messages"],
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            response.raise_for_status()
            result = response.json()

        content = result.get("message", {}).get("content", "")

        # Capture raw output in trace
        llm_trace_ctx.set_outputs(
            {
                "raw_response": (
                    content[:2000] if len(content) > 2000 else content
                ),
                "model": result.get("model"),
                "done_reason": result.get("done_reason"),
            }
        )

        return _parse_llm_response(content)


def _parse_llm_response(content: str) -> dict | None:
    """Parse LLM response, handling markdown code blocks.

    First attempts to parse using the PatternDiscoveryResponse Pydantic model,
    which validates the schema and constrains enum values.
    Falls back to manual JSON parsing if structured parsing fails.
    """
    content = content.strip()

    # Remove markdown code blocks if present
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    # Try structured parsing first (validates schema and enum values)
    try:
        parsed = json.loads(content)
        structured_response = PatternDiscoveryResponse.model_validate(parsed)
        return structured_response.to_dict()
    except Exception as e:
        logger.debug(
            "Structured parsing failed, falling back to manual parsing: %s", e
        )

    # Fallback to manual JSON parsing for backwards compatibility
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return None


def get_default_patterns(merchant_name: str, reason: str = "unknown") -> dict:
    """Return default patterns when discovery fails.

    Args:
        merchant_name: Name of the merchant
        reason: Why default patterns are being used

    Returns:
        Default pattern dictionary matching the flat schema
    """
    return {
        # Metadata
        "merchant": merchant_name,
        "receipt_type": "unknown",
        "receipt_type_reason": reason,
        "auto_generated": True,
        # Structure
        "item_structure": "unknown",
        "lines_per_item": {"typical": 2, "min": 1, "max": 5},
        "item_start_marker": "PRODUCT_NAME or barcode",
        "item_end_marker": "LINE_TOTAL label",
        "grouping_rule": "Group all words between consecutive LINE_TOTAL labels",
        # Position info
        "label_positions": {
            "PRODUCT_NAME": "left",
            "LINE_TOTAL": "right",
            "UNIT_PRICE": "right",
            "QUANTITY": "varies",
        },
        "x_position_zones": {
            "left": [0.0, 0.3],
            "center": [0.3, 0.6],
            "right": [0.6, 1.0],
        },
        # Pattern matching
        "barcode_pattern": r"\d{10,14}",
        "special_markers": [],
        "product_name_patterns": [],
    }

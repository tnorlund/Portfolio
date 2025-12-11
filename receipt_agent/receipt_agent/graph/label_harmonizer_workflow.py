"""
Agentic workflow for harmonizing labels on a whole receipt.

This workflow uses an LLM agent to reason about a receipt and validate all labels
together, ensuring financial consistency and correctness.

Key Features:
- Processes entire receipt text as source of truth
- Uses sub-agents for currency detection, totals validation, line item parsing
- Validates financial math (line items + tax = grand total)
- Uses ChromaDB similarity search for label suggestions
- Provides detailed reasoning and confidence scores
"""

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Annotated, Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, create_react_agent
from pydantic import BaseModel, Field

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.agent_common import (
    create_agent_node_with_retry,
    create_ollama_llm,
)
from receipt_agent.utils.receipt_fetching import (
    fetch_receipt_details_with_fallback,
)
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


# ==============================================================================
# Helpers
# ==============================================================================


def label_to_dict(lb: Any) -> dict:
    """
    Serialize a ReceiptWordLabel to a plain dict suitable for state/tool use.
    Includes fields required to round-trip with ReceiptWordLabel(**dict).
    """
    return {
        "image_id": str(lb.image_id),
        "receipt_id": int(lb.receipt_id),
        "line_id": int(lb.line_id),
        "word_id": int(lb.word_id),
        "label": lb.label or "",
        "reasoning": getattr(lb, "reasoning", None),
        "timestamp_added": getattr(lb, "timestamp_added", None),
        "validation_status": getattr(lb, "validation_status", None),
        "label_proposed_by": getattr(lb, "label_proposed_by", None),
        "label_consolidated_from": getattr(
            lb, "label_consolidated_from", None
        ),
    }


def extract_pricing_table_from_words(words: list[dict]) -> dict:
    """
    Deterministically group words into a pricing table using geometry.

    Returns a structure with rows, columns, and cells. Filters out rows that
    don't resemble line items (no digits and no financial/line-item label).
    """
    if not words:
        return {"error": "No words found"}

    prepared = []
    for w in words:
        bb = w.get("bounding_box") or {}
        x = bb.get("x")
        y = bb.get("y")
        width = bb.get("width")
        height = bb.get("height")
        if x is None or y is None or width is None or height is None:
            # skip words without geometry; they won't help with table layout
            continue
        cx = x + width / 2
        cy = y + height / 2
        prepared.append(
            {
                "text": w.get("text", ""),
                "line_id": w.get("line_id"),
                "word_id": w.get("word_id"),
                "bbox": bb,
                "cx": cx,
                "cy": cy,
                "width": width,
                "height": height,
                "label": w.get("label"),
            }
        )

    if not prepared:
        return {"error": "No words with geometry found"}

    # Row grouping by y-center
    prepared.sort(key=lambda w: w["cy"])
    median_height = sorted([w["height"] for w in prepared])[len(prepared) // 2]
    row_tol = max(3.0, median_height * 0.75)
    rows = []
    current_row = []
    last_y = None
    for w in prepared:
        if last_y is None or abs(w["cy"] - last_y) <= row_tol:
            current_row.append(w)
            last_y = w["cy"] if last_y is None else (last_y + w["cy"]) / 2
        else:
            rows.append(current_row)
            current_row = [w]
            last_y = w["cy"]
    if current_row:
        rows.append(current_row)

    # Column inference across all rows
    x_centers = []
    for row in rows:
        for w in row:
            x_centers.append(w["cx"])
    x_centers.sort()
    median_width = sorted([w["width"] for w in prepared])[len(prepared) // 2]
    col_tol = max(5.0, median_width * 1.0)
    col_bands = []
    current_band = []
    for xc in x_centers:
        if not current_band or abs(xc - current_band[-1]) <= col_tol:
            current_band.append(xc)
        else:
            col_bands.append(current_band)
            current_band = [xc]
    if current_band:
        col_bands.append(current_band)
    columns = [
        {"col": i, "x_center": sum(b) / len(b)}
        for i, b in enumerate(col_bands)
    ]

    # Assign columns
    def assign_col(xc: float) -> int:
        best = 0
        best_dist = float("inf")
        for c in columns:
            d = abs(xc - c["x_center"])
            if d < best_dist:
                best_dist = d
                best = c["col"]
        return best

    # Filter to line-item-like rows
    keep_rows = []
    financial_labels = {
        "UNIT_PRICE",
        "LINE_TOTAL",
        "QUANTITY",
        "PRODUCT_NAME",
        "TOTAL",
        "SUBTOTAL",
        "TAX",
    }
    for row in rows:
        has_digit = any(any(ch.isdigit() for ch in w["text"]) for w in row)
        has_fin_label = any(
            (w.get("label") or "").upper() in financial_labels for w in row
        )
        if has_digit or has_fin_label:
            keep_rows.append(row)

    structured_rows = []
    for ridx, row in enumerate(keep_rows):
        cells = []
        for w in sorted(row, key=lambda w: w["cx"]):
            cells.append(
                {
                    "row": ridx,
                    "col": assign_col(w["cx"]),
                    "text": w["text"],
                    "line_id": w["line_id"],
                    "word_id": w["word_id"],
                    "bbox": w["bbox"],
                    "label": w.get("label"),
                }
            )
        structured_rows.append({"row": ridx, "cells": cells})

    return {
        "rows": structured_rows,
        "columns": columns,
        "total_rows": len(structured_rows),
        "total_columns": len(columns),
    }


# ==============================================================================
# Agent State
# ==============================================================================


class LabelHarmonizerAgentState(BaseModel):
    """State for the label harmonizer agent workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID containing the receipt")
    receipt_id: int = Field(description="Receipt ID within the image")

    # Receipt data (loaded by tools)
    receipt_text: Optional[str] = Field(
        default=None, description="Full receipt text"
    )
    words: list[dict] = Field(
        default_factory=list, description="Receipt words"
    )
    lines: list[dict] = Field(
        default_factory=list, description="Receipt lines"
    )
    labels: list[dict] = Field(
        default_factory=list, description="Current labels"
    )

    # Analysis results
    currency: Optional[str] = Field(
        default=None, description="Detected currency"
    )
    totals_validation: Optional[dict] = Field(
        default=None, description="Totals validation results"
    )
    line_items: list[dict] = Field(
        default_factory=list, description="Parsed line items"
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    # Final result
    harmonization_result: Optional[dict] = Field(
        default=None, description="Final harmonization result"
    )

    class Config:
        arbitrary_types_allowed = True


# ==============================================================================
# System Prompt
# ==============================================================================

LABEL_HARMONIZER_PROMPT = """You are a receipt label harmonizer. Your job is to validate and harmonize all labels on a receipt, ensuring they are correct and financially consistent.

## Your Task

You're given a receipt with words, lines, and labels. Your job is to:
1. Examine the receipt text (source of truth)
2. Validate all labels are correct
3. Detect currency from receipt text
4. Validate financial totals (grand total, subtotal, tax, line items)
5. Identify and fix any label errors
6. Submit your harmonization decisions

## Available Tools

### Core Tools
- `get_line_id_text_list`: List lines with IDs top-to-bottom (no geometry)
- `run_table_subagent`: Given a start/end line_id, infer columns for that block and store results
- `run_label_subagent`: Scoped pass for a single CORE_LABEL (optionally limited to the table range)
- `submit_harmonization` (REQUIRED): Submit your harmonization decisions

## Strategy

1. **Start** with `get_line_id_text_list` to list lines top-to-bottom with line_ids and text (no geometry).
2. **Table block**: If there is a line-item/total block, call `run_table_subagent` with the start/end line_ids to infer columns; keep this in state for downstream steps. Geometry (centroid/left/right) stays within the sub-agent.
3. **Per-label focused passes**: For each CORE_LABEL of interest (e.g., TOTAL, SUBTOTAL, TAX, LINE_TOTAL, UNIT_PRICE, QUANTITY, PRODUCT_NAME, PAYMENT_METHOD, DATE, TIME, ADDRESS_LINE, PHONE_NUMBER), call `run_label_subagent` (optionally scoped to the table range) to get a focused summary; then propose fixes and aggregate.
4. **Submit decisions** with `submit_harmonization` once, aggregating the per-label updates.

## Financial Validation

Use `validate_financial_consistency` to run a sub-agent that:
- Detects currency from receipt text
- Validates grand total = subtotal + tax (with tolerance)
- Validates subtotal = sum of line totals
- Identifies which labels are incorrect
- Proposes corrections with reasoning

## Label Validation Rules

- Labels must match what's actually printed on the receipt
- Use receipt text as source of truth (not external APIs)
- If a label doesn't match receipt text, it's likely wrong
- Use similarity search to find similar words with correct labels

## Decision Guidelines

### Confidence Scoring
- High (0.8-1.0): Clear evidence from receipt text, financial math checks out
- Medium (0.5-0.8): Some uncertainty but reasonable decision
- Low (0.0-0.5): Significant uncertainty, may need manual review

### When to Update Labels
- Label doesn't match receipt text
- Financial math doesn't add up (and label is likely wrong)
- Similar words have different labels (inconsistency)
- Label is missing but should exist

### When NOT to Update
- Label matches receipt text and financial math checks out
- Uncertainty is too high (confidence < 0.5)
- Receipt text is unclear or ambiguous

## Important Rules

1. ALWAYS start with `get_line_id_text_list` to see the receipt lines and IDs.
2. For table-like financial sections, call `run_table_subagent` with the chosen line-id range to understand columns before labeling totals/line items; geometry is handled inside the sub-agent.
3. For each label type, prefer `run_label_subagent` (optionally scoped to the table range) to keep context tight.
4. Use receipt text as source of truth (not external APIs).
5. ALWAYS end with `submit_harmonization`.
6. Be thorough but efficient.

Begin by getting the receipt text, then validate financial consistency."""


# ==============================================================================
# Tool Factory for Label Harmonizer
# ==============================================================================


def create_label_harmonizer_tools(
    dynamo_client: "DynamoClient",
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    receipt_data: Optional[dict] = None,
    settings: Optional[Settings] = None,
) -> tuple[list[Any], dict]:
    """
    Create tools for the label harmonizer agent.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client for similarity search
        embed_fn: Embedding function
        receipt_data: Dict to hold current receipt context

    Returns:
        (tools, state_holder)
    """
    if receipt_data is None:
        receipt_data = {}
    if settings is None:
        settings = get_settings()

    state = {
        "receipt": receipt_data,
        "result": None,
        "chroma_client": chroma_client,
        "embed_fn": embed_fn,
        "dynamo_client": dynamo_client,
        "settings": settings,
    }

    # Pre-build a simple table sub-agent graph (LLM + single analyze_block tool)
    def _build_table_subagent_graph() -> Any:
        @tool
        def analyze_block(line_id_start: int, line_id_end: int) -> dict:
            """Infer columns for the given line_id range using stored line geometry."""
            return _analyze_line_block_columns_impl(
                line_id_start=line_id_start,
                line_id_end=line_id_end,
            )

        sub_llm = create_ollama_llm(settings)
        # Mirror the GH multi-agent example: wrap the sub-agent with create_react_agent
        return create_react_agent(
            sub_llm,
            tools=[analyze_block],
            prompt=(
                "You are a table structure helper. "
                "Call the analyze_block tool exactly once with the provided line_id range. "
                "Return only the tool result."
            ),
        )

    state["table_subagent_graph"] = _build_table_subagent_graph()

    # ========== RECEIPT DATA TOOLS ==========

    @tool
    def get_receipt_text() -> dict:
        """
        Get the full receipt text formatted for display.

        Returns:
        - receipt_text: Formatted receipt text (receipt-space grouping)
        - line_count: Number of lines
        - word_count: Number of words

        Use this first to see the full receipt.
        """
        receipt = state["receipt"]
        if not receipt:
            logger.info("LH3 tool get_receipt_text: no receipt in state")
            return {"error": "No receipt data loaded"}

        lines = receipt.get("lines", [])

        if not lines:
            logger.info("LH3 tool get_receipt_text: receipt has no lines")
            return {"error": "No lines found"}

        # Format receipt text; fall back to simple join if fields are missing
        receipt_text = ""
        try:
            from receipt_dynamo.entities import ReceiptLine

            receipt_lines = [
                ReceiptLine(**line) if isinstance(line, dict) else line
                for line in lines
            ]

            # Sort top-to-bottom (y desc) and merge visually contiguous lines.
            sorted_lines = sorted(
                receipt_lines,
                key=lambda line: line.calculate_centroid()[1],
                reverse=True,
            )
            merged_rows: list[dict] = []
            for ln in sorted_lines:
                centroid_y = ln.calculate_centroid()[1]
                if merged_rows:
                    prev_ln = merged_rows[-1]["last_line"]
                    if (
                        prev_ln.bottom_left["y"]
                        < centroid_y
                        < prev_ln.top_left["y"]
                    ):
                        merged_rows[-1]["lines"].append(ln)
                        merged_rows[-1]["text_parts"].append(ln.text or "")
                        merged_rows[-1]["last_line"] = ln
                        continue
                merged_rows.append(
                    {
                        "lines": [ln],
                        "text_parts": [ln.text or ""],
                        "last_line": ln,
                    }
                )

            formatted_rows = []
            for row in merged_rows:
                ids = [l.line_id for l in row["lines"]]
                id_label = (
                    f"{min(ids)}-{max(ids)}"
                    if len(set(ids)) > 1
                    else str(ids[0])
                )
                row_text = " ".join(
                    [t for t in row["text_parts"] if t]
                ).strip()
                formatted_rows.append(f"{id_label}: {row_text}")

            receipt_text = "\n".join(formatted_rows)
        except Exception as e:
            logger.info(
                "LH3 tool get_receipt_text: fallback formatting: %s", e
            )
            receipt_text = "\n".join(
                f"{l.get('line_id', '')}: {l.get('text', '')}"
                for l in lines
                if l
            )

        if not (receipt_text or "").strip():
            logger.info(
                "LH3 tool get_receipt_text: empty receipt_text after formatting"
            )
            return {"error": "No receipt text available"}

        return {
            "receipt_text": receipt_text,
            "line_count": len(lines),
            "word_count": len(receipt.get("words", [])),
        }

    @tool
    def get_receipt_words() -> dict:
        """
        Get all words on the receipt with their current labels.

        Returns:
        - words: List of words with text, line_id, word_id, and current label
        """
        receipt = state["receipt"]
        if not receipt:
            logger.info("LH3 tool get_receipt_words: no receipt in state")
            return {"error": "No receipt data loaded"}

        words = receipt.get("words", [])
        if not words:
            logger.info("LH3 tool get_receipt_words: receipt has no words")
            return {"error": "No words found"}
        labels = receipt.get("labels", [])

        # If labels are empty but words exist, optionally try a one-time reload of labels
        if not labels:
            dynamo = state.get("dynamo_client")
            image_id = receipt.get("image_id")
            rec_id = receipt.get("receipt_id")
            if dynamo and image_id and rec_id:
                try:
                    page, lek = dynamo.list_receipt_word_labels_for_receipt(
                        image_id=image_id, receipt_id=rec_id
                    )
                    extra = page or []
                    # If there are more pages, fetch them too
                    while lek:
                        page, lek = (
                            dynamo.list_receipt_word_labels_for_receipt(
                                image_id=image_id,
                                receipt_id=rec_id,
                                last_evaluated_key=lek,
                            )
                        )
                        extra.extend(page or [])
                    receipt["labels"] = [label_to_dict(lb) for lb in extra]
                    labels = receipt.get("labels", [])
                    logger.info(
                        "LH3 tool get_receipt_words: reloaded labels count=%s",
                        len(labels),
                    )
                except Exception as e:
                    logger.info(
                        "LH3 tool get_receipt_words: label reload failed: %s",
                        e,
                    )

        # Create label lookup
        label_lookup = {}
        for label in labels:
            key = (
                label.get("line_id"),
                label.get("word_id"),
            )
            label_lookup[key] = label

        # Combine words with labels
        word_data = []
        for word in words:
            key = (word.get("line_id"), word.get("word_id"))
            label = label_lookup.get(key, {})
            word_data.append(
                {
                    "line_id": word.get("line_id"),
                    "word_id": word.get("word_id"),
                    "text": word.get("text", ""),
                    "label": label.get("label"),
                    "validation_status": label.get("validation_status"),
                }
            )

        return {"words": word_data, "total_words": len(word_data)}

    @tool
    def get_line_id_text_list() -> dict:
        """
        List lines top-to-bottom with ids and text only (no geometry).
        """
        receipt = state["receipt"]
        if not receipt:
            return {"error": "No receipt data loaded"}
        lines = receipt.get("lines", [])
        if not lines:
            return {"error": "No lines found"}

        def centroid_y(ln: dict) -> float:
            tl = ln.get("top_left") or {}
            tr = ln.get("top_right") or {}
            bl = ln.get("bottom_left") or {}
            br = ln.get("bottom_right") or {}
            return (
                tl.get("y", 0)
                + tr.get("y", 0)
                + bl.get("y", 0)
                + br.get("y", 0)
            ) / 4

        summary = [
            {"line_id": ln.get("line_id"), "text": ln.get("text", "")}
            for ln in lines
        ]
        summary_sorted = sorted(
            summary, key=lambda ln: centroid_y(ln), reverse=True
        )
        return {
            "lines": summary_sorted,
            "note": "y=0 is bottom; sorted top-to-bottom by decreasing y",
        }

    @tool
    def get_labels_by_type(label_type: str) -> dict:
        """
        Get all labels of a specific type.

        Args:
            label_type: CORE_LABEL type (e.g., GRAND_TOTAL, TAX, SUBTOTAL)

        Returns:
        - labels: List of labels with word text and positions
        """
        receipt = state["receipt"]
        if not receipt:
            logger.info("LH3 tool get_labels_by_type: no receipt in state")
            return {"error": "No receipt data loaded"}

        labels = receipt.get("labels", [])
        if not labels:
            logger.info("LH3 tool get_labels_by_type: no labels in state")
            return {"error": "No labels found"}
        words = receipt.get("words", [])

        # Create word lookup
        word_lookup = {}
        for word in words:
            key = (word.get("line_id"), word.get("word_id"))
            word_lookup[key] = word

        # Filter labels by type
        matching_labels = []
        for label in labels:
            if label.get("label") == label_type:
                key = (label.get("line_id"), label.get("word_id"))
                word = word_lookup.get(key, {})
                matching_labels.append(
                    {
                        "line_id": label.get("line_id"),
                        "word_id": label.get("word_id"),
                        "text": word.get("text", ""),
                        "validation_status": label.get("validation_status"),
                    }
                )

        return {
            "label_type": label_type,
            "labels": matching_labels,
            "count": len(matching_labels),
        }

    # ========== FINANCIAL VALIDATION SUB-AGENT TOOL ==========

    @tool
    def validate_financial_consistency() -> dict:
        """
        Validate financial consistency using a sub-agent.

        This tool runs a financial validation sub-agent that:
        - Detects currency
        - Validates grand total = subtotal + tax
        - Validates subtotal = sum of line totals
        - Identifies which labels need correction

        Returns:
        - currency: Detected currency
        - is_valid: Whether financial math checks out
        - issues: List of issues found
        - corrections: List of label corrections needed (each with line_id, word_id, current_label, correct_label, reasoning, confidence)
        """
        receipt = state["receipt"]
        if not receipt:
            logger.info(
                "LH3 tool validate_financial_consistency: no receipt in state"
            )
            return {"error": "No receipt data loaded"}

        receipt_text = receipt.get("receipt_text", "")
        labels = receipt.get("labels", [])
        words = receipt.get("words", [])
        if not labels:
            logger.info(
                "LH3 tool validate_financial_consistency: no labels in state"
            )
        if not words:
            logger.info(
                "LH3 tool validate_financial_consistency: no words in state"
            )

        # Run financial validation sub-agent (sync wrapper for async function)
        try:
            from receipt_agent.graph.financial_validation_workflow import (
                create_financial_validation_graph,
                run_financial_validation,
            )

            # Create sub-agent graph (reuse if exists, or create new)
            if "financial_validation_graph" not in state:
                graph, sub_state_holder = create_financial_validation_graph()
                state["financial_validation_graph"] = graph
                state["financial_validation_state"] = sub_state_holder
            else:
                graph = state["financial_validation_graph"]
                sub_state_holder = state["financial_validation_state"]

            # Run async sub-agent in sync context
            result = asyncio.run(
                run_financial_validation(
                    graph=graph,
                    state_holder=sub_state_holder,
                    receipt_text=receipt_text,
                    labels=labels,
                    words=words,
                )
            )

            # Store corrections in state for main agent to use
            corrections = result.get("corrections", [])
            if corrections:
                state["financial_corrections"] = corrections

            return result
        except Exception as e:
            logger.exception(f"Financial validation sub-agent failed: {e}")
            return {
                "currency": None,
                "is_valid": False,
                "issues": [{"type": "error", "message": str(e)}],
                "corrections": [],
            }

    # ========== SIMILARITY SEARCH TOOL ==========

    @tool
    def search_similar_labels(
        word_text: str, label_type: Optional[str] = None
    ) -> dict:
        """
        Search ChromaDB for similar words with labels.

        Args:
            word_text: Word text to search for
            label_type: Optional label type to filter results

        Returns:
        - similar_words: List of similar words with their labels
        - count: Number of matches found
        """
        if not state.get("chroma_client") or not state.get("embed_fn"):
            return {
                "error": "ChromaDB not available",
                "similar_words": [],
                "count": 0,
            }

        try:
            chroma = state["chroma_client"]
            embed_fn = state["embed_fn"]

            # Generate embedding
            embedding = embed_fn([word_text])[0]

            # Search ChromaDB
            results = chroma.query(
                query_embeddings=[embedding],
                n_results=10,
            )

            similar_words = []
            if results and results.get("documents"):
                for i, doc in enumerate(results["documents"][0]):
                    metadata = (
                        results.get("metadatas", [[]])[0][i]
                        if results.get("metadatas")
                        else {}
                    )
                    distance = (
                        results.get("distances", [[]])[0][i]
                        if results.get("distances")
                        else 1.0
                    )
                    similarity = (
                        1.0 - distance
                    )  # Convert distance to similarity

                    word_label = metadata.get("label")
                    if label_type and word_label != label_type:
                        continue

                    similar_words.append(
                        {
                            "word_text": doc,
                            "label": word_label,
                            "similarity": similarity,
                            "merchant": metadata.get("merchant_name"),
                        }
                    )

            return {
                "similar_words": similar_words,
                "count": len(similar_words),
            }
        except Exception as e:
            logger.exception(f"ChromaDB search failed: {e}")
            return {
                "error": str(e),
                "similar_words": [],
                "count": 0,
            }

    # ========== DECISION TOOL ==========

    @tool
    def submit_harmonization(
        updates: list[dict],
        currency: Optional[str] = None,
        totals_valid: bool = True,
        confidence: float = 0.8,
        reasoning: str = "",
    ) -> dict:
        """
        Submit harmonization decisions.

        Args:
            updates: List of label updates, each with:
                - line_id: Line ID
                - word_id: Word ID
                - label: New label type (CORE_LABEL)
                - validation_status: Optional validation status (VALID, PENDING, etc.)
                - reasoning: Optional reasoning for this specific update
            currency: Detected currency (from financial validation sub-agent)
            totals_valid: Whether financial totals are valid
            confidence: Confidence score (0.0 to 1.0)
            reasoning: General reasoning for all decisions

        Note: You can also use corrections from `validate_financial_consistency`
        by including them in the updates list. Each correction should have:
        - line_id, word_id: Word position
        - label: Correct label type
        - reasoning: Why this correction is needed
        - validation_status: Usually VALID if correction is confident

        Returns:
        - success: Whether submission was successful
        - updates_applied: Number of updates
        """
        receipt = state["receipt"]
        if not receipt:
            return {"error": "No receipt data loaded", "success": False}

        # Add receipt identifiers and reasoning to each update
        image_id = receipt.get("image_id", "")
        receipt_id = receipt.get("receipt_id", 0)

        enriched_updates = []
        for update in updates:
            enriched_update = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": update["line_id"],
                "word_id": update["word_id"],
                "label": update["label"],
                "validation_status": update.get("validation_status", "VALID"),
                "reasoning": update.get("reasoning", reasoning),
            }
            enriched_updates.append(enriched_update)

        # Store result
        state["result"] = {
            "updates": enriched_updates,
            "currency": currency,
            "totals_valid": totals_valid,
            "confidence": confidence,
            "reasoning": reasoning,
        }

    # ========== PRICING TABLE EXTRACTION TOOL ==========

    @tool
    def extract_pricing_table() -> dict:
        """
        Extract a structured pricing table (rows/columns) from words/lines using geometry.

        Heuristics:
        - Group words into rows by y-center proximity.
        - Infer columns by clustering x-centers.
        - Keep only rows that look like line items (has a digit or a financial/line-item label).
        - Return cells with text, line_id, word_id, bbox, row, col.
        """
        receipt = state["receipt"]
        if not receipt:
            return {"error": "No receipt data loaded"}

        words = receipt.get("words", [])
        return extract_pricing_table_from_words(words)

    # ========== COLUMN ANALYZER (LINE-RANGE) ==========

    def _analyze_line_block_columns_impl(
        line_id_start: int, line_id_end: int
    ) -> dict:
        receipt = state["receipt"]
        if not receipt:
            return {"error": "No receipt data loaded"}
        lines = receipt.get("lines", [])
        if not lines:
            return {"error": "No lines found"}

        block = [
            ln
            for ln in lines
            if isinstance(ln, dict)
            and ln.get("line_id") is not None
            and line_id_start <= ln.get("line_id") <= line_id_end
        ]
        if not block:
            return {"error": "No lines in range"}

        prepared = []
        for ln in block:
            tl = ln.get("top_left") or {}
            tr = ln.get("top_right") or {}
            bl = ln.get("bottom_left") or {}
            br = ln.get("bottom_right") or {}
            left_x = (tl.get("x", 0) + bl.get("x", 0)) / 2
            right_x = (tr.get("x", 0) + br.get("x", 0)) / 2
            cx = (left_x + right_x) / 2
            cy = (
                tl.get("y", 0)
                + tr.get("y", 0)
                + bl.get("y", 0)
                + br.get("y", 0)
            ) / 4
            prepared.append(
                {
                    "line_id": ln.get("line_id"),
                    "text": ln.get("text", ""),
                    "left_x": left_x,
                    "right_x": right_x,
                    "cx": cx,
                    "cy": cy,
                }
            )

        # Infer columns via clustering on x-centers
        x_centers = sorted(p["cx"] for p in prepared)
        if not x_centers:
            return {"error": "No geometry available"}
        # tolerance ~ 1/4 of median span
        spans = sorted(
            (p["right_x"] - p["left_x"])
            for p in prepared
            if p["right_x"] >= p["left_x"]
        )
        median_span = spans[len(spans) // 2] if spans else 0.05
        col_tol = max(0.02, median_span * 0.5)

        bands = []
        current = []
        for xc in x_centers:
            if not current or abs(xc - current[-1]) <= col_tol:
                current.append(xc)
            else:
                bands.append(current)
                current = [xc]
        if current:
            bands.append(current)
        columns = [
            {"col": i, "x_center": sum(b) / len(b)}
            for i, b in enumerate(bands)
        ]

        def assign_col(xc: float) -> int:
            best, dist = 0, float("inf")
            for c in columns:
                d = abs(xc - c["x_center"])
                if d < dist:
                    best, dist = c["col"], d
            return best

        lines_with_cols = []
        for p in sorted(
            prepared, key=lambda v: v["cy"], reverse=True
        ):  # higher y = higher on page
            lines_with_cols.append(
                {
                    "line_id": p["line_id"],
                    "col": assign_col(p["cx"]),
                    "text": p["text"],
                    "left_x": p["left_x"],
                    "right_x": p["right_x"],
                    "centroid": {"x": p["cx"], "y": p["cy"]},
                }
            )

        return {
            "columns": columns,
            "lines": lines_with_cols,
            "note": "y=0 is bottom; lines sorted top-to-bottom by decreasing y",
        }

    @tool
    def analyze_line_block_columns(
        line_id_start: int, line_id_end: int
    ) -> dict:
        """
        Given an inclusive line_id range, infer column positions and assign lines.

        Assumptions:
        - Coordinates are in receipt space with y=0 at the bottom (higher y = visually higher).
        - Uses line geometry (top_left/top_right/bottom_left/bottom_right) from preloaded state.
        """
        return _analyze_line_block_columns_impl(
            line_id_start=line_id_start, line_id_end=line_id_end
        )

    # ========== COLUMN SUB-AGENT WRAPPER ==========

    @tool
    def run_table_subagent(line_id_start: int, line_id_end: int) -> dict:
        """
        Run the pre-built table sub-agent graph to infer columns for a line-id range.
        Stores result in state['column_analysis'] and returns it.
        """
        subgraph = state.get("table_subagent_graph")
        if subgraph is None:
            return {"error": "table sub-agent graph not available"}

        receipt_ref = state.get("receipt", {})
        image_id = str(receipt_ref.get("image_id", ""))
        receipt_id = receipt_ref.get("receipt_id", 0)
        try:
            receipt_id = int(receipt_id)
        except Exception:
            receipt_id = 0

        # Follow the GH example: invoke the sub-agent with messages-only state
        result_msg = subgraph.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "Call analyze_block exactly once using the provided "
                            f"line_id_start={line_id_start} and line_id_end={line_id_end}. "
                            "Return only the tool result."
                        )
                    )
                ]
            }
        )
        col_result = None
        if result_msg and result_msg.get("messages"):
            for m in result_msg["messages"]:
                if isinstance(m, AIMessage) and isinstance(m.content, dict):
                    col_result = m.content
                    break
        if not col_result:
            col_result = {"error": "table sub-agent returned no result"}

        state["table_line_range"] = {
            "line_id_start": line_id_start,
            "line_id_end": line_id_end,
        }
        state["column_analysis"] = col_result
        return col_result

    @tool
    def run_label_subagent(
        label_type: str,
        line_id_start: int | None = None,
        line_id_end: int | None = None,
        use_column_analysis: bool = True,
    ) -> dict:
        """
        Focused pass for a single CORE_LABEL.

        Returns a scoped view: words, labels, and optional column info for the specified line range.
        """
        receipt = state["receipt"]
        if not receipt:
            return {"error": "No receipt data loaded"}

        words = receipt.get("words", [])
        labels = receipt.get("labels", [])
        col_analysis = (
            state.get("column_analysis") if use_column_analysis else None
        )

        def in_range(line_id: int) -> bool:
            if line_id_start is None or line_id_end is None:
                return True
            return line_id_start <= line_id <= line_id_end

        scoped_words = [w for w in words if in_range(w.get("line_id", -1))]
        scoped_labels = [
            lb
            for lb in labels
            if lb.get("label") == label_type
            and in_range(lb.get("line_id", -1))
        ]

        return {
            "label_type": label_type,
            "line_range": {
                "start": line_id_start,
                "end": line_id_end,
            },
            "words": scoped_words,
            "labels": scoped_labels,
            "column_analysis": col_analysis,
        }

        return {
            "success": True,
            "updates_applied": len(enriched_updates),
            "currency": currency,
            "totals_valid": totals_valid,
            "confidence": confidence,
        }

    # Collect all tools
    tools = [
        get_line_id_text_list,
        run_table_subagent,
        run_label_subagent,
        submit_harmonization,
    ]

    return tools, state


# ==============================================================================
# Graph Creation
# ==============================================================================


def create_label_harmonizer_graph(
    dynamo_client: "DynamoClient",
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the label harmonizer agent graph.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client for similarity search
        embed_fn: Embedding function
        settings: Settings for the agent

    Returns:
        (graph, state_holder)
    """
    if settings is None:
        settings = get_settings()

    llm = create_ollama_llm(settings)

    # Create state holder (shared state for tools)
    state_holder = {
        "dynamo_client": dynamo_client,
        "chroma_client": chroma_client,
        "embed_fn": embed_fn,
        "receipt": {},
        "result": None,
    }

    # Create tools (they reference state_holder, so they'll see updates)
    tools, _ = create_label_harmonizer_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        receipt_data=state_holder["receipt"],  # Pass reference to state
        settings=settings,
    )

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # Create agent node with retry logic
    agent_node = create_agent_node_with_retry(
        llm=llm_with_tools,
        agent_name="label-harmonizer",
    )

    # Create tool node (handles async tools)
    tool_node = ToolNode(tools)

    # Create graph
    workflow = StateGraph(LabelHarmonizerAgentState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add edges
    def should_continue(state: LabelHarmonizerAgentState) -> str:
        """Check if we should continue to tools or end."""
        if not state.messages:
            return END
        last_message = state.messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")

    # Compile graph
    graph = workflow.compile()

    return graph, state_holder


# ==============================================================================
# Run Agent
# ==============================================================================


async def run_label_harmonizer_agent(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    dry_run: bool = True,
) -> Any:
    """
    Run the label harmonizer agent for a single receipt.

    Args:
        graph: Compiled LangGraph graph
        state_holder: State holder dict
        image_id: Image ID containing the receipt
        receipt_id: Receipt ID within the image
        dry_run: If True, only report what would be updated

    Returns:
        ReceiptLabelResult object
    """
    from receipt_agent.tools.label_harmonizer_v3 import ReceiptLabelResult

    dynamo_client = state_holder.get("dynamo_client")
    if not dynamo_client:
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["DynamoDB client not available"],
        )

    # Load receipt data
    logger.info(
        "LH3 run: fetching receipt details image_id=%s receipt_id=%s",
        image_id,
        receipt_id,
    )
    receipt_details = fetch_receipt_details_with_fallback(
        dynamo_client=dynamo_client,
        image_id=image_id,
        receipt_id=receipt_id,
    )
    if receipt_details:
        logger.info(
            "LH3 receipt fetched: lines=%s words=%s labels=%s",
            len(receipt_details.lines or []),
            len(receipt_details.words or []),
            len(receipt_details.labels or []),
        )

    if not receipt_details or not receipt_details.lines:
        logger.info(
            "LH3 receipt missing or has no lines image_id=%s receipt_id=%s",
            image_id,
            receipt_id,
        )
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["Receipt not found or has no lines"],
        )

    # Extract data from receipt_details
    lines = receipt_details.lines
    words = receipt_details.words
    labels = receipt_details.labels

    # If labels are empty, try to fetch them separately (with pagination)
    if not labels:
        logger.info(
            "LH3 labels empty from ReceiptDetails, fetching word labels separately"
        )
        try:
            fetched: list[Any] = []
            lek = None
            while True:
                page, lek = dynamo_client.list_receipt_word_labels_for_receipt(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    last_evaluated_key=lek,
                )
                fetched.extend(page or [])
                if not lek:
                    break
            labels = fetched
            logger.info(
                "LH3 fetched %s labels via list_receipt_word_labels_for_receipt",
                len(labels),
            )
        except Exception as e:
            logger.info("LH3 could not fetch labels separately: %s", e)
            labels = []

    # Prepare receipt data for state
    def _safe_geom(obj: Any) -> dict:
        return {
            "bounding_box": getattr(obj, "bounding_box", None),
            "top_left": getattr(obj, "top_left", None),
            "top_right": getattr(obj, "top_right", None),
            "bottom_left": getattr(obj, "bottom_left", None),
            "bottom_right": getattr(obj, "bottom_right", None),
            "angle_degrees": getattr(obj, "angle_degrees", None),
            "angle_radians": getattr(obj, "angle_radians", None),
            "confidence": getattr(obj, "confidence", None),
        }

    words_data = []
    for w in words:
        entry = {
            "image_id": str(w.image_id),
            "receipt_id": int(w.receipt_id),
            "line_id": int(w.line_id),
            "word_id": int(w.word_id),
            "text": w.text or "",
        }
        entry.update(_safe_geom(w))
        words_data.append(entry)

    lines_data = []
    for l in lines:
        entry = {
            "image_id": str(l.image_id),
            "receipt_id": int(l.receipt_id),
            "line_id": int(l.line_id),
            "text": l.text or "",
        }
        entry.update(_safe_geom(l))
        lines_data.append(entry)

    labels_data = [label_to_dict(label) for label in labels]

    # Format receipt text; fall back to simple join if fields are missing
    try:
        from receipt_dynamo.entities import ReceiptLine

        receipt_lines = [
            ReceiptLine(**line) if isinstance(line, dict) else line
            for line in lines
        ]
        receipt_text = format_receipt_text_receipt_space(receipt_lines)
    except Exception as e:
        logger.info("LH3 receipt_text fallback formatting: %s", e)
        receipt_text = "\n".join(l.get("text", "") for l in lines if l)

    if not (receipt_text or "").strip():
        logger.info(
            "LH3 receipt_text empty after formatting, image_id=%s receipt_id=%s",
            image_id,
            receipt_id,
        )
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["Receipt has no text after formatting"],
        )

    # Update state holder with receipt data (mutate existing dict to preserve reference used by tools)
    receipt_state = state_holder.get("receipt")
    if receipt_state is None:
        receipt_state = {}
        state_holder["receipt"] = receipt_state

    receipt_state.clear()
    receipt_state.update(
        {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "words": words_data,
            "lines": lines_data,
            "labels": labels_data,
            "receipt_text": receipt_text,
        }
    )

    logger.info(
        "LH3 state ready: words=%s lines=%s labels=%s text_len=%s",
        len(words_data),
        len(lines_data),
        len(labels_data),
        len(receipt_text or ""),
    )

    # Create initial state with system prompt
    initial_state = LabelHarmonizerAgentState(
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_text=receipt_text,
        words=words_data,
        lines=lines_data,
        labels=labels_data,
        messages=[
            SystemMessage(content=LABEL_HARMONIZER_PROMPT),
            HumanMessage(
                content=f"Please harmonize labels for receipt {image_id}#{receipt_id}. "
                f"Start by getting the receipt text, then validate financial consistency."
            ),
        ],
    )

    # Run agent
    try:
        final_state = await graph.ainvoke(
            initial_state.dict(), config={"recursion_limit": 50}
        )
    except Exception as e:
        logger.exception(f"Agent execution failed: {e}")
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=[str(e)],
        )

    # Extract result
    result_data = state_holder.get("result")
    if not result_data:
        return ReceiptLabelResult(
            image_id=image_id,
            receipt_id=receipt_id,
            errors=["No harmonization result submitted"],
        )

    # Build updates list (already enriched by submit_harmonization tool)
    updates = result_data.get("updates", [])

    # Extract totals issues from validation
    totals_issues = []
    validation_result = result_data.get("totals_validation", {})
    if isinstance(validation_result, dict):
        totals_issues = validation_result.get("issues", [])

    # Build result
    result = ReceiptLabelResult(
        image_id=image_id,
        receipt_id=receipt_id,
        total_labels=len(labels_data),
        labels_updated=len(updates),
        currency_detected=result_data.get("currency"),
        totals_valid=result_data.get("totals_valid", False),
        totals_issues=totals_issues,
        confidence=result_data.get("confidence", 0.0),
        reasoning=result_data.get("reasoning", ""),
        updates=updates,
    )

    return result

"""
Tool factory for the Label Harmonizer agent.

Creates all tools needed for the label harmonizer workflow.
"""

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Callable, Optional, TypedDict, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from receipt_agent.agents.label_harmonizer.tools.helpers import (
    extract_pricing_table_from_words,
    label_to_dict,
)
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.subagents.financial_validation.utils import extract_number
from receipt_agent.utils.agent_common import create_ollama_llm

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


class LabelHarmonizerStateDict(TypedDict):
    """Type definition for the state dictionary used in label harmonizer tools."""

    receipt: dict[str, Any]
    result: Optional[dict[str, Any]]
    chroma_client: Optional[Any]
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]]
    dynamo_client: "DynamoClient"
    settings: Settings
    table_subagent_graph: Any
    column_analysis: Any
    column_analysis_summary: Optional[str]
    column_analysis_history: list[dict]


def create_label_harmonizer_tools(
    dynamo_client: "DynamoClient",
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    receipt_data: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> tuple[list[Any], LabelHarmonizerStateDict]:
    """
    Create tools for the label harmonizer agent.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client for similarity search
        embed_fn: Embedding function
        receipt_data: Dict to hold current receipt context
        settings: Settings for the agent

    Returns:
        (tools, state_holder)
    """
    if receipt_data is None:
        receipt_data = {}
    if settings is None:
        settings = get_settings()

    state: LabelHarmonizerStateDict = {
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
                "You are a table structure helper for analyzing financial sections "
                "of receipts. Your job is to identify column structure and provide "
                "a clear description for the main agent.\n\n"
                "Call the analyze_block tool exactly once with the provided "
                "line_id range. The tool will return column positions, semantic "
                "hints, and a summary. Return the complete tool result including "
                "the 'summary' field which describes the column structure in "
                "natural language for the main agent to understand."
            ),
        )

    state["table_subagent_graph"] = _build_table_subagent_graph()

    # Pre-build a simple financial sub-agent graph (LLM + financial discovery tools)
    def _build_financial_subagent_graph() -> Any:
        @tool
        def analyze_receipt_structure() -> dict:
            """Get concise receipt structure overview for financial analysis."""
            receipt = state.get("receipt", {})
            words = receipt.get("words", [])
            table_structure = state.get("column_analysis")

            # Extract basic structure info
            lines_by_id = {}
            for word in words:
                line_id = word.get("line_id")
                if line_id not in lines_by_id:
                    lines_by_id[line_id] = []
                lines_by_id[line_id].append(word.get("text", ""))

            # Build CONCISE line preview (last 10 lines where financial data usually is)
            line_texts = {}
            for line_id, word_texts in lines_by_id.items():
                line_texts[line_id] = " ".join(word_texts)

            # Get last 10 lines (usually contain totals)
            sorted_line_ids = sorted(line_texts.keys())
            preview_line_ids = (
                sorted_line_ids[-10:]
                if len(sorted_line_ids) > 10
                else sorted_line_ids
            )

            return {
                "total_words": len(words),
                "total_lines": len(lines_by_id),
                "has_table_structure": table_structure is not None,
                "line_preview_last_10": {
                    str(line_id): line_texts[line_id]
                    for line_id in preview_line_ids
                },
                "note": "Showing last 10 lines (where financial totals typically appear)",
            }

        @tool
        def identify_numeric_candidates() -> dict:
            """Find numeric values in the receipt, limiting to top candidates to avoid context overflow."""
            receipt = state.get("receipt", {})
            words = receipt.get("words", [])

            # Group words by line for context
            lines_by_id = {}
            for word in words:
                line_id = word.get("line_id")
                if line_id not in lines_by_id:
                    lines_by_id[line_id] = []
                lines_by_id[line_id].append(word)

            numeric_candidates = []

            for word in words:
                text = word.get("text", "")
                numeric_value = extract_number(text)

                if numeric_value is not None:
                    line_id = word.get("line_id")

                    # Get abbreviated line context (first 3 words before + this word + first 3 words after)
                    line_words = lines_by_id.get(line_id, [])
                    word_idx = next(
                        (
                            i
                            for i, w in enumerate(line_words)
                            if w.get("word_id") == word.get("word_id")
                        ),
                        0,
                    )
                    context_words = line_words[
                        max(0, word_idx - 3) : min(
                            len(line_words), word_idx + 4
                        )
                    ]
                    line_context = " ".join(
                        [w.get("text", "") for w in context_words]
                    )

                    numeric_candidates.append(
                        {
                            "line_id": line_id,
                            "word_id": word.get("word_id"),
                            "text": text,
                            "numeric_value": numeric_value,
                            "line_context": line_context,
                        }
                    )

            # Sort by value and limit to top 20 candidates to prevent context overflow
            numeric_candidates.sort(
                key=lambda x: x["numeric_value"], reverse=True
            )
            top_candidates = numeric_candidates[:20]

            return {
                "numeric_candidates": top_candidates,
                "total_numeric_values": len(numeric_candidates),
                "showing_top": len(top_candidates),
                "note": "Limited to top 20 candidates by value to prevent context overflow",
            }

        @tool
        def reason_about_financial_layout(
            reasoning: str, candidate_assignments: list[dict]
        ) -> dict:
            """Apply reasoning to identify which numeric values represent which financial types."""
            if not candidate_assignments:
                return {"error": "No candidate assignments provided"}

            # Check for duplicate word assignments
            seen_words = set()
            duplicates = []
            for assignment in candidate_assignments:
                word_key = (
                    assignment.get("line_id"),
                    assignment.get("word_id"),
                )
                if word_key in seen_words:
                    duplicates.append(word_key)
                seen_words.add(word_key)

            if duplicates:
                dup_str = ", ".join(
                    [f"line {lid} word {wid}" for lid, wid in duplicates]
                )
                logger.warning(
                    "⚠️  Duplicate word assignments detected: %s", dup_str
                )
                return {
                    "error": f"DUPLICATE ASSIGNMENTS: Each word can only have one financial type. "
                    f"The following words were assigned multiple times: {dup_str}. "
                    f"Please revise your assignments to use each word only once."
                }

            # Store the LLM's reasoning and proposed assignments in shared state
            state["financial_reasoning"] = reasoning
            state["proposed_assignments"] = candidate_assignments

            return {
                "reasoning_recorded": True,
                "total_assignments": len(candidate_assignments),
                "ready_for_verification": True,
            }

        @tool
        def test_mathematical_relationships() -> dict:
            """Test whether your proposed financial assignments follow correct receipt mathematics."""
            proposed = state.get("proposed_assignments", [])
            if not proposed:
                return {"error": "No proposed assignments to test."}

            # Extract values by type
            values_by_type = {}
            for assignment in proposed:
                financial_type = assignment.get("proposed_type")
                value = assignment.get("value")
                if financial_type not in values_by_type:
                    values_by_type[financial_type] = []
                values_by_type[financial_type].append(value)

            verification_results = []
            tolerance = 0.01

            # Test 1: GRAND_TOTAL = SUBTOTAL + TAX
            if (
                "GRAND_TOTAL" in values_by_type
                and "SUBTOTAL" in values_by_type
            ):
                grand_total = values_by_type["GRAND_TOTAL"][0]
                subtotal = values_by_type["SUBTOTAL"][0]
                tax = values_by_type.get("TAX", [0])[0]

                calculated_total = subtotal + tax
                difference = grand_total - calculated_total
                math_correct = abs(difference) <= tolerance

                verification_results.append(
                    {
                        "test_name": "GRAND_TOTAL = SUBTOTAL + TAX",
                        "passes": math_correct,
                        "difference": difference,
                    }
                )

            # Store results in shared state
            state["verification_results"] = verification_results

            tests_passed = sum(
                1
                for result in verification_results
                if result.get("passes", False)
            )
            total_tests = len(verification_results)

            return {
                "total_tests": total_tests,
                "tests_passed": tests_passed,
                "all_tests_pass": tests_passed == total_tests,
            }

        @tool
        def finalize_financial_context(
            final_reasoning: str,
            confidence_assessment: str,
            currency: str = "USD",
        ) -> dict:
            """Submit your final financial analysis as CONCISE context for downstream label assignment."""
            proposed = state.get("proposed_assignments", [])
            verification_results = state.get("verification_results", [])

            if not proposed:
                return {"error": "No proposed assignments available."}

            # Build CONCISE financial context (only essential info)
            financial_candidates = {}
            for assignment in proposed:
                financial_type = assignment.get("proposed_type")
                if financial_type not in financial_candidates:
                    financial_candidates[financial_type] = []

                # Store only essential info to reduce context size
                financial_candidates[financial_type].append(
                    {
                        "line_id": assignment.get("line_id"),
                        "word_id": assignment.get("word_id"),
                        "value": assignment.get("value"),
                        "confidence": assignment.get("confidence", 0.8),
                    }
                )

            # Calculate mathematical validation summary
            total_tests = len(verification_results)
            passed_tests = len(
                [r for r in verification_results if r.get("passes", False)]
            )

            # Store CONCISE result optimized for main agent (no verbose reasoning)
            final_result = {
                "financial_candidates": financial_candidates,
                "mathematical_validation": {
                    "verified": passed_tests,
                    "total_tests": total_tests,
                    "all_valid": passed_tests == total_tests,
                },
                "currency": currency,
                "confidence": confidence_assessment,
                "summary": (
                    final_reasoning[:200] + "..."
                    if len(final_reasoning) > 200
                    else final_reasoning
                ),
            }

            state["financial_context"] = final_result

            return {
                "success": True,
                "context_generated": True,
                "financial_types_count": len(financial_candidates),
                "mathematical_tests_passed": f"{passed_tests}/{total_tests}",
            }

        financial_tools = [
            analyze_receipt_structure,
            identify_numeric_candidates,
            reason_about_financial_layout,
            test_mathematical_relationships,
            finalize_financial_context,
        ]

        sub_llm = create_ollama_llm(settings)
        return create_react_agent(
            sub_llm,
            tools=financial_tools,
            prompt=(
                "You are a financial discovery agent. Identify key financial values efficiently.\n\n"
                "Execute these steps IN ORDER (call each tool ONCE):\n"
                "1. analyze_receipt_structure() - Get receipt overview\n"
                "2. identify_numeric_candidates() - Find numeric values (limited to top 20)\n"
                "3. reason_about_financial_layout() - Assign financial types (GRAND_TOTAL, SUBTOTAL, TAX, LINE_TOTAL)\n"
                "4. test_mathematical_relationships() - Verify math\n"
                "5. finalize_financial_context() - Submit concise results\n\n"
                "BE CONCISE. Focus on identifying the 4 key financial types. "
                "After finalize_financial_context, STOP immediately.\n\n"
                "For step 3: candidate_assignments is a list of dicts with: "
                "line_id, word_id, value, proposed_type, confidence (0.0-1.0)\n\n"
                "CRITICAL RULE: Each word (identified by line_id + word_id) can only have ONE financial type. "
                "Do NOT assign the same word to multiple types (e.g., don't label word 5 on line 10 as both GRAND_TOTAL and SUBTOTAL)."
            ),
        )

    try:
        logger.debug("Building financial subagent graph...")
        state["financial_subagent_graph"] = _build_financial_subagent_graph()
        logger.debug("Financial subagent graph created successfully")
    except Exception as e:
        logger.exception("Failed to create financial subagent graph: %s", e)

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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
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
            if (
                dynamo
                and image_id
                and rec_id
                and hasattr(dynamo, "list_receipt_word_labels_for_receipt")
            ):
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
                    receipt_data = state.get("receipt")
                    if isinstance(receipt_data, dict):
                        receipt_data["labels"] = [
                            label_to_dict(lb) for lb in extra
                        ]
                        labels = receipt_data.get("labels", [])
                    else:
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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
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

    # ========== ENHANCED FINANCIAL VALIDATION SUB-AGENT TOOL ==========

    @tool
    def validate_financial_consistency() -> dict:
        """
        Discover and validate financial consistency using LLM reasoning with retry and fallback.

        This tool runs an LLM-driven financial discovery sub-agent that:
        - Analyzes receipt structure to understand financial layout
        - Uses reasoning (not hard-coded rules) to identify financial values
        - Detects LINE_TOTAL, SUBTOTAL, TAX, and GRAND_TOTAL candidates
        - Validates mathematical relationships between identified values
        - Provides concise context for downstream label assignment

        Returns a concise summary to avoid overwhelming the main agent with context.
        """
        financial_subagent = state.get("financial_subagent_graph")
        if financial_subagent is None:
            logger.warning(
                "Financial sub-agent graph not available, using fallback"
            )
            return _simple_financial_fallback()

        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
            return {"error": "No receipt data loaded"}

        # Retry logic for financial sub-agent with exponential backoff
        # Get retry configuration from environment or use defaults
        import os

        max_retries = int(
            os.environ.get("FINANCIAL_SUBAGENT_MAX_RETRIES", "3")
        )
        base_delay = float(
            os.environ.get("FINANCIAL_SUBAGENT_BASE_DELAY", "2.0")
        )

        for attempt in range(max_retries):
            try:
                # Reset financial sub-agent state before each retry attempt
                # to avoid stale results causing false "success"
                state["financial_context"] = {}
                state["proposed_assignments"] = []
                state["verification_results"] = []
                state["financial_reasoning"] = ""
                state["final_result"] = None

                # Invoke sub-agent with simplified message to reduce context
                financial_subagent.invoke(
                    {
                        "messages": [
                            HumanMessage(
                                content="Analyze this receipt to identify key financial values "
                                "(GRAND_TOTAL, SUBTOTAL, TAX, LINE_TOTAL). Execute the 5-step process."
                            )
                        ]
                    }
                )

                # Extract result from the financial context stored in state
                financial_context = state.get("financial_context", {})
                if financial_context and financial_context.get(
                    "financial_candidates"
                ):
                    candidates = financial_context.get(
                        "financial_candidates", {}
                    )
                    logger.info(
                        f"Financial sub-agent succeeded: found {len(candidates)} types"
                    )

                    # Log detailed financial discoveries
                    logger.info(
                        "Financial context summary: types_found=%s, currency=%s, confidence=%s, math_valid=%s",
                        list(candidates.keys()),
                        financial_context.get("currency"),
                        financial_context.get("confidence"),
                        financial_context.get(
                            "mathematical_validation", {}
                        ).get("all_valid"),
                    )

                    # Log each financial type with values
                    for fin_type, entries in candidates.items():
                        values = [
                            f"{e.get('value')} (line {e.get('line_id')}, conf={e.get('confidence', 0):.2f})"
                            for e in entries[:3]
                        ]  # First 3 entries
                        logger.info(f"  {fin_type}: {', '.join(values)}")

                    return financial_context
                else:
                    logger.warning(
                        "Financial sub-agent returned no candidates, trying fallback"
                    )
                    return _simple_financial_fallback()

            except Exception as e:
                error_str = str(e)
                is_retryable = (
                    "500" in error_str
                    or "502" in error_str
                    or "503" in error_str
                    or "Internal Server Error" in error_str
                    or "timeout" in error_str.lower()
                )

                if is_retryable and attempt < max_retries - 1:
                    import time

                    wait_time = base_delay * (2**attempt)
                    logger.warning(
                        f"Financial sub-agent failed (attempt {attempt + 1}/{max_retries}): {error_str[:150]}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(
                        f"Financial sub-agent failed after {attempt + 1} attempts: {error_str[:200]}. "
                        "Using simple fallback."
                    )
                    return _simple_financial_fallback()

        # Should not reach here, but just in case
        return _simple_financial_fallback()

    def _simple_financial_fallback() -> dict:
        """Simple rule-based fallback when LLM-driven approach fails."""
        receipt = state.get("receipt", {})
        labels = receipt.get("labels", [])
        words = receipt.get("words", [])

        # Create a lookup dict for fast word access by (line_id, word_id)
        word_lookup = {}
        for word in words:
            key = (word.get("line_id"), word.get("word_id"))
            word_lookup[key] = word.get("text", "")

        # Extract existing financial labels as candidates
        financial_candidates = {}
        for label in labels:
            label_type = label.get("label", "")
            if label_type in ["GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL"]:
                if label_type not in financial_candidates:
                    financial_candidates[label_type] = []

                # Look up the word text
                word_key = (label.get("line_id"), label.get("word_id"))
                word_text = word_lookup.get(word_key, "")

                # Parse numeric value from text (strip currency symbols, commas, handle negatives)
                # Skip percentages (TAX might be a percentage, but we want the dollar amount)
                parsed_value = None
                if word_text:
                    # For TAX labels, check if it's a percentage - if so, skip parsing
                    # (we want the dollar amount, not the percentage)
                    if label_type == "TAX" and "%" in word_text:
                        # Try to find a dollar amount nearby, but for fallback, set to None
                        parsed_value = None
                    else:
                        parsed_value = extract_number(word_text)

                financial_candidates[label_type].append(
                    {
                        "line_id": label.get("line_id"),
                        "word_id": label.get("word_id"),
                        "text": word_text,
                        "value": parsed_value,  # float or None when parsing fails
                        "confidence": 0.6,  # Lower confidence for fallback
                    }
                )

        logger.warning(
            f"Using financial fallback: extracted {len(financial_candidates)} types "
            f"from {len(labels)} existing labels"
        )

        return {
            "financial_candidates": financial_candidates,
            "mathematical_validation": {
                "verified": 0,
                "total_tests": 0,
                "all_valid": False,
            },
            "currency": "USD",
            "confidence": "low",
            "summary": "Used simple fallback - LLM reasoning unavailable",
            "fallback_used": True,
        }

    # ========== SIMILARITY SEARCH TOOL ==========

    @tool
    def search_similar_labels(
        word_text: str, label_type: Optional[str] = None
    ) -> dict:
        """
        Search ChromaDB for similar words with labels, with merchant context.

        Args:
            word_text: Word text to search for
            label_type: Optional label type to filter results

        Returns:
        - similar_words: List of similar words with labels, merchant info, and same_merchant flag
        - count: Number of matches found
        - current_merchant: Current receipt's merchant name
        - current_place_id: Current receipt's place_id
        """
        chroma = state.get("chroma_client")
        embed_fn_val = state.get("embed_fn")
        if not chroma or not embed_fn_val:
            return {
                "error": "ChromaDB not available",
                "similar_words": [],
                "count": 0,
            }

        try:
            chroma = chroma
            embed_fn = embed_fn_val

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
                            "place_id": metadata.get("place_id"),
                        }
                    )

            # Add context about current receipt's merchant
            receipt = state.get("receipt", {})
            current_metadata = receipt.get("metadata", {})
            current_merchant = current_metadata.get("merchant_name")
            current_place_id = current_metadata.get("place_id")

            # Enhance results with same-merchant indicator
            if current_merchant or current_place_id:
                for result in similar_words:
                    result["same_merchant"] = (
                        current_merchant
                        and result.get("merchant") == current_merchant
                    ) or (
                        current_place_id
                        and result.get("place_id") == current_place_id
                    )

            return {
                "similar_words": similar_words,
                "count": len(similar_words),
                "current_merchant": current_merchant,
                "current_place_id": current_place_id,
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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
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

        return {
            "success": True,
            "updates_applied": len(enriched_updates),
            "currency": currency,
            "totals_valid": totals_valid,
            "confidence": confidence,
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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
            return {"error": "No receipt data loaded"}

        words = receipt.get("words", [])
        return extract_pricing_table_from_words(words)

    # ========== COLUMN ANALYZER (LINE-RANGE) ==========

    def _analyze_line_block_columns_impl(
        line_id_start: int, line_id_end: int
    ) -> dict:
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
            return {"error": "No receipt data loaded"}
        lines = receipt.get("lines", [])
        if not lines:
            return {"error": "No lines found"}

        block = []
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            line_id = ln.get("line_id")
            if line_id is not None and line_id_start <= line_id <= line_id_end:
                block.append(ln)
        if not block:
            return {"error": "No lines in range"}

        # Get words for semantic analysis
        words = receipt.get("words", [])
        block_words = []
        for w in words:
            if not isinstance(w, dict):
                continue
            word_line_id = w.get("line_id")
            if (
                word_line_id is not None
                and line_id_start <= word_line_id <= line_id_end
            ):
                block_words.append(w)

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
        # Sort columns by x_center (left to right)
        column_x_centers = sorted([sum(b) / len(b) for b in bands])
        columns = [
            {"col": i, "x_center": xc} for i, xc in enumerate(column_x_centers)
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
            assigned_col = assign_col(p["cx"])
            lines_with_cols.append(
                {
                    "line_id": p["line_id"],
                    "col": assigned_col,
                    "text": p["text"],
                    "left_x": p["left_x"],
                    "right_x": p["right_x"],
                    "centroid": {"x": p["cx"], "y": p["cy"]},
                }
            )

        # Calculate column spans (left/right bounds)
        col_bounds = {}
        for line_data in lines_with_cols:
            col = line_data["col"]
            if col not in col_bounds:
                col_bounds[col] = {
                    "left": line_data["left_x"],
                    "right": line_data["right_x"],
                }
            else:
                col_bounds[col]["left"] = min(
                    col_bounds[col]["left"], line_data["left_x"]
                )
                col_bounds[col]["right"] = max(
                    col_bounds[col]["right"], line_data["right_x"]
                )

        # Add spans to columns
        for col_info in columns:
            col_num = col_info["col"]
            if col_num in col_bounds:
                col_info["left_bound"] = col_bounds[col_num]["left"]
                col_info["right_bound"] = col_bounds[col_num]["right"]
            else:
                col_info["left_bound"] = col_info["x_center"]
                col_info["right_bound"] = col_info["x_center"]

        # Analyze column content for semantic hints
        def analyze_column_content(col_num: int) -> dict:
            """Analyze text content in a column to infer its purpose."""
            col_lines = [l for l in lines_with_cols if l["col"] == col_num]
            col_texts = [l["text"].strip() for l in col_lines if l["text"]]

            # Get words in this column
            col_words = []
            for word in block_words:
                word_bb = word.get("bounding_box") or {}
                word_cx = (
                    word_bb.get("x", 0) + word_bb.get("width", 0) / 2
                    if word_bb.get("x") is not None
                    else None
                )
                if word_cx is not None:
                    word_col = assign_col(word_cx)
                    if word_col == col_num:
                        col_words.append(word.get("text", ""))

            all_text = " ".join(col_texts + col_words).lower()

            # Detect numeric content
            numeric_pattern = re.compile(r"[\d.,$€£¥]")
            has_numbers = bool(numeric_pattern.search(all_text))

            # Detect totals keywords
            totals_keywords = [
                "total",
                "subtotal",
                "tax",
                "amount",
                "due",
                "balance",
                "sum",
            ]
            has_totals_keywords = any(kw in all_text for kw in totals_keywords)

            # Detect quantity keywords
            quantity_keywords = ["qty", "quantity", "count", "x", "#"]
            has_quantity_keywords = any(
                kw in all_text for kw in quantity_keywords
            )

            # Detect price keywords
            price_keywords = ["price", "cost", "each", "unit"]
            has_price_keywords = any(kw in all_text for kw in price_keywords)

            # Detect product/description keywords
            product_keywords = ["item", "product", "description", "name"]
            has_product_keywords = any(
                kw in all_text for kw in product_keywords
            )

            hints = []
            if has_totals_keywords:
                hints.append("totals")
            if has_quantity_keywords:
                hints.append("quantity")
            if has_price_keywords:
                hints.append("price")
            if has_product_keywords:
                hints.append("product")
            if has_numbers and not hints:
                hints.append("numeric")

            return {
                "content_type": "numeric" if has_numbers else "text",
                "hints": hints,
                "sample_text": col_texts[0] if col_texts else "",
            }

        # Add semantic hints to columns
        for col_info in columns:
            semantic = analyze_column_content(col_info["col"])
            col_info["content_type"] = semantic["content_type"]
            col_info["hints"] = semantic["hints"]
            col_info["sample_text"] = semantic["sample_text"]

        result = {
            "columns": columns,
            "lines": lines_with_cols,
            "line_range": {"start": line_id_start, "end": line_id_end},
            "note": "y=0 is bottom; lines sorted top-to-bottom by decreasing y",
        }

        return result

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

        receipt_ref = state.get("receipt")
        if not receipt_ref or not isinstance(receipt_ref, dict):
            receipt_ref = {}
        image_id = str(receipt_ref.get("image_id", ""))
        receipt_id = receipt_ref.get("receipt_id", 0)
        try:
            receipt_id = int(receipt_id)
        except Exception:
            receipt_id = 0

        # Carry prior summaries so the sub-agent can refine/append descriptions
        prior_summary = state.get("column_analysis_summary")

        # Follow the GH example: invoke the sub-agent with messages-only state
        result_msg = subgraph.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "Analyze the financial section structure for lines "
                            f"{line_id_start} to {line_id_end}. "
                            "Call analyze_block exactly once with these parameters. "
                            "Return ONLY valid JSON of the form: "
                            "{"
                            '"summary": "<1-2 sentences about the table>", '
                            '"line_range": {"start": <int>, "end": <int>}, '
                            '"price_column": <int|null>, '
                            '"rows": ['
                            '  {"line_id": <int>, "price": "<text>", "price_col": <int>, '
                            '   "left": "<text to left or empty>", "right": "<text to right or empty>"}'
                            "], "
                            '"columns": ['
                            '  {"col": <int>, "role_hint": "text|price|qty|total|numeric|other", '
                            '   "x_center": <float>, "sample": "<optional short sample>"}'
                            "]"
                            "}. "
                            "Summary and rows are REQUIRED; do NOT return empty or placeholder text. "
                            "Keep it concise; no tool logs or extra prose."
                            + (
                                " Prior summaries so far: "
                                f"{prior_summary}. Update or append with any new "
                                "information for this line range."
                                if prior_summary
                                else ""
                            )
                        )
                    )
                ]
            }
        )
        col_result = None
        if result_msg and result_msg.get("messages"):
            # Prefer the final AI message (LLM-produced structured JSON)
            for m in reversed(result_msg["messages"]):
                if isinstance(m, AIMessage) and isinstance(m.content, dict):
                    col_result = m.content
                    break
            # Fall back to tool messages only if no AI JSON was found
            if not col_result:
                for m in result_msg["messages"]:
                    if isinstance(m, ToolMessage):
                        try:
                            if isinstance(m.content, dict):
                                col_result = m.content
                                break
                            if isinstance(m.content, str):
                                import json

                                col_result = json.loads(m.content)
                                break
                        except Exception:
                            continue
        if not col_result:
            # Final fallback: call analyze_block directly
            col_result = _analyze_line_block_columns_impl(
                line_id_start=line_id_start, line_id_end=line_id_end
            )
            if "error" in col_result:
                col_result = {"error": "table sub-agent returned no result"}

        # Normalize to dict for downstream use
        if not isinstance(col_result, dict):
            col_result = {"error": "table sub-agent returned invalid result"}
        col_dict: dict[str, Any] = cast(dict[str, Any], col_result)

        # Ensure line_range is present
        if "line_range" not in col_dict:
            col_dict["line_range"] = {
                "start": line_id_start,
                "end": line_id_end,
            }

        # Ensure summary exists
        summary = col_dict.get("summary")
        if not summary:
            summary = "Summary missing from sub-agent."
            col_dict["summary"] = summary
        line_range = col_dict.get("line_range", {})

        # Maintain history of summaries in state
        history_raw = state.get("column_analysis_history")
        history: list[dict] = (
            history_raw if isinstance(history_raw, list) else []
        )

        def _same_range(entry: dict) -> bool:
            lr = entry.get("line_range", {}) if isinstance(entry, dict) else {}
            return (
                lr.get("start") == line_id_start
                and lr.get("end") == line_id_end
            )

        # Replace any existing entry for the same line range
        history = [h for h in history if not _same_range(h)]
        history.append({"summary": summary, "line_range": line_range})

        aggregate_summary = "\n".join(
            f"Lines {h.get('line_range', {}).get('start', '?')}"
            f"-{h.get('line_range', {}).get('end', '?')}: "
            f"{h.get('summary', '')}"
            for h in history
            if isinstance(h, dict)
        )

        state["table_line_range"] = {
            "line_id_start": line_id_start,
            "line_id_end": line_id_end,
        }
        state["column_analysis"] = col_dict
        state["column_analysis_history"] = history
        state["column_analysis_summary"] = aggregate_summary

        # Return payload plus rollups
        col_dict["history"] = history
        col_dict["aggregate_summary"] = aggregate_summary

        return col_dict

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
        receipt = state.get("receipt")
        if not receipt or not isinstance(receipt, dict):
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

    # Collect all tools
    tools = [
        get_line_id_text_list,
        run_table_subagent,
        validate_financial_consistency,
        run_label_subagent,
    ]

    return tools, state

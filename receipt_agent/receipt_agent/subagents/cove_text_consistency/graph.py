"""
CoVe Text Consistency Sub-Agent Graph

Graph creation and execution functions for the CoVe text consistency workflow.
"""

import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.subagents.cove_text_consistency.state import (
    CoveTextConsistencyState,
)
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

# Import for exception handling in tools
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

logger = logging.getLogger(__name__)

# ======================================================================
# Module-level constants for batch processing
# ======================================================================

COVE_MAX_BATCH_SIZE = 10
COVE_MAX_TOTAL_CHARS = 15000  # ~3750 tokens, conservative limit

# ======================================================================
# System Prompt
# ======================================================================

COVE_TEXT_CONSISTENCY_PROMPT = """
You are a receipt text consistency verifier (CoVe). Your job is to verify that
all receipts sharing the same place_id actually contain text consistent with
being from the same place.

## Your Task

You are given:
- A place_id group of receipts
- Canonical metadata (merchant_name, address, phone) determined by the
  harmonizer
- Access to the actual receipt text for each receipt

Your job is to:
1. Examine the receipt text for each receipt in the group
2. Compare each receipt's text against the canonical metadata
3. Verify that merchant name, address, and phone appear in the receipt text
   (allowing for OCR errors)
4. Identify any receipts that appear to be from a different place (outliers)
5. Submit a consistency report with per-receipt verdicts

## Available Tools

### Receipt Text Tools
- `batch_check_receipts`: **RECOMMENDED** - Check multiple receipts' text in a
  single call (efficient for large groups). Use this for groups with 5+
  receipts to reduce tool calls.
- `get_receipt_text`: Get formatted receipt text (receipt-space grouping) for
  a specific receipt (use for individual checks or when
  batch_check_receipts fails)
- `get_receipt_content`: Get raw lines and labeled words for a specific receipt
  (use only if you need detailed word-level analysis)
- `get_group_summary`: See all receipts in this group with their metadata

## Strategy

1. **Start** by getting the group summary to see all receipts
   - Use `get_group_summary` to list all receipts with their image_id and
     receipt_id

2. **Examine receipt text** for each receipt:
   - **For groups with 5+ receipts**: Use `batch_check_receipts` to process
     multiple receipts at once (recommended batch size: 5-10 receipts per call)
     - If the batch tool returns `has_more: true`, call it again with the
       remaining receipts
     - Continue until all receipts are checked
   - **For individual checks**: Use `get_receipt_text` to get formatted text
     for a specific receipt
   - **For detailed analysis**: Use `get_receipt_content` for deeper inspection
     (raw lines, labeled words) only if needed
   - **Note**: Some receipts may not have receipt details (lines/words) in
     DynamoDB.
     If a receipt check returns an error or "not found", mark that receipt as
     UNSURE with evidence "Receipt details not available" and continue with
     other receipts.
   - Look for the canonical merchant name, address, and phone in the text

3. **Compare against canonical metadata**:
   - Does the merchant name appear in the receipt text? (case-insensitive,
     allow OCR errors)
   - Does the address appear? (street number + street name, allow formatting
     differences)
   - Does the phone number appear? (normalized digits, allow formatting
     differences)
   - **For receipts without text**: Mark as UNSURE with evidence
     "Cannot verify - receipt details not available"

4. **Identify outliers**:
   - Receipts where merchant name, address, AND phone all mismatch → MISMATCH
   - Receipts where 2+ fields mismatch → MISMATCH
   - Receipts where only 1 field mismatches → UNSURE (may be OCR error)
   - Receipts where all fields match → SAME_PLACE
   - Receipts without text/details → UNSURE (cannot verify)

5. **Submit your report** with `submit_text_consistency`:
   - For each receipt: status (SAME_PLACE / MISMATCH / UNSURE) and evidence
   - List of outliers (receipts marked MISMATCH or UNSURE)
   - Overall confidence in the consistency check
   - **Important**: Even if some receipts don't have text, you must still
     submit results for ALL receipts in the group. Mark missing receipts as
     UNSURE.

## Important Rules

1. ALWAYS start with `get_group_summary` to see all receipts
2. Check receipt text for EACH receipt in the group:
   - **Use `batch_check_receipts` for efficiency** when checking 5+ receipts
     (batch 5-10 at a time)
   - Use `get_receipt_text` for individual checks or when batch fails
   - Skip receipts if details not available (mark as UNSURE)
3. If a receipt doesn't have text/details, mark it as UNSURE and continue
4. Be lenient with OCR errors - minor typos are OK
5. Be strict with complete mismatches - if merchant/address/phone all differ,
   it's likely a different place
6. ALWAYS end with `submit_text_consistency` - never end without calling it
7. You MUST submit results for ALL receipts, even if some don't have text
8. **Efficiency**: Batch receipts when possible to reduce tool calls and stay
   within recursion limits

## Status Definitions

- **SAME_PLACE**: Receipt text matches canonical metadata (allowing OCR errors)
- **MISMATCH**: Receipt text clearly indicates a different place (2+ fields
  mismatch)
- **UNSURE**: Ambiguous case (1 field mismatch, unclear due to OCR quality, or
  receipt details not available)

Begin by getting the group summary, then systematically check each receipt's
text."""


# ======================================================================
# Text Consistency Submission Tool
# ======================================================================


def create_text_consistency_submission_tool(state_holder: dict):
    """Create a tool for submitting text consistency results."""

    class ReceiptConsistencyResult(BaseModel):
        """Result for a single receipt."""

        image_id: str = Field(description="Image ID")
        receipt_id: int = Field(description="Receipt ID")
        status: str = Field(
            description="Status: SAME_PLACE, MISMATCH, or UNSURE"
        )
        evidence: str = Field(
            description=(
                "Evidence for this status (what text was found/missing)"
            )
        )

    class SubmitTextConsistencyInput(BaseModel):
        """Input for submit_text_consistency tool."""

        receipt_results: list[ReceiptConsistencyResult] = Field(
            description="Consistency result for each receipt"
        )
        overall_confidence: float = Field(
            ge=0.0,
            le=1.0,
            description="Overall confidence in consistency check (0.0 to 1.0)",
        )
        reasoning: str = Field(
            description="Overall explanation of the consistency check results"
        )

    @tool(args_schema=SubmitTextConsistencyInput)
    def submit_text_consistency(
        receipt_results: list[ReceiptConsistencyResult],
        overall_confidence: float,
        reasoning: str,
    ) -> dict:
        """
        Submit text consistency results for all receipts in the group.

        Call this when you've checked all receipts and determined their
        consistency status.
        This ends the workflow.

        Args:
            receipt_results: List of consistency results, one per receipt
            overall_confidence: Overall confidence in the consistency check
                (0.0-1.0)
            reasoning: Explanation of the results
        """
        # Get expected receipts from state
        expected_receipts = state_holder.get("receipts", [])
        expected_count = len(expected_receipts)

        # Deduplicate receipt_results by (image_id, receipt_id) -
        # keep first occurrence
        seen = set()
        unique_results = []
        for r in receipt_results:
            key = (r.image_id, r.receipt_id)
            if key not in seen:
                seen.add(key)
                unique_results.append(r)

        # Warn if we got more/fewer results than expected
        if len(unique_results) != expected_count:
            logger.warning(
                "CoVe agent submitted %s receipt_results but expected %s receipts. "
                "Deduplicating...",
                len(unique_results),
                expected_count,
            )

        # Use deduplicated results
        receipt_results = unique_results

        outliers = [
            r for r in receipt_results if r.status in ["MISMATCH", "UNSURE"]
        ]

        result = {
            "place_id": state_holder.get("place_id"),
            "canonical_merchant_name": state_holder.get(
                "canonical_merchant_name"
            ),
            "canonical_address": state_holder.get("canonical_address"),
            "canonical_phone": state_holder.get("canonical_phone"),
            "receipt_results": [
                {
                    "image_id": r.image_id,
                    "receipt_id": r.receipt_id,
                    "status": r.status,
                    "evidence": r.evidence,
                }
                for r in receipt_results
            ],
            "outliers": [
                {
                    "image_id": r.image_id,
                    "receipt_id": r.receipt_id,
                    "status": r.status,
                    "evidence": r.evidence,
                }
                for r in outliers
            ],
            "outlier_count": len(outliers),
            "overall_confidence": overall_confidence,
            "reasoning": reasoning,
        }

        state_holder["consistency_result"] = result

        logger.info(
            "Text consistency submitted: %s/%s outliers (confidence=%.2f%%)",
            len(outliers),
            len(receipt_results),
            overall_confidence * 100.0,
        )

        return {
            "status": "submitted",
            "message": (
                f"Consistency check complete. {len(outliers)} outliers found."
            ),
            "result": result,
        }

    return submit_text_consistency


# ======================================================================
# Helper Functions
# ======================================================================

# Reuse shared fallback helper for all CoVe tools
_fetch_receipt_details_fallback = fetch_receipt_details_with_fallback


def _fetch_receipt_with_fallback(
    dynamo_client: "DynamoClient", image_id: str, receipt_id: int
) -> Optional[Any]:
    """
    Shared fetch logic for CoVe tools.

    Sanitizes image_id, tries multiple IDs, and falls back to alternative
    methods
    if primary fetch fails.

    Args:
        dynamo_client: DynamoDB client
        image_id: Image ID (may have trailing characters like '?')
        receipt_id: Receipt ID

    Returns:
        ReceiptDetails if found, None otherwise
    """
    # Sanitize image_id first (remove trailing characters like '?')
    sanitized_image_id = image_id.rstrip("? \t\n\r")

    # Try sanitized version first, then original if different
    receipt_details = None
    for img_id in [sanitized_image_id, image_id]:
        try:
            receipt_details = dynamo_client.get_receipt_details(
                image_id=img_id,
                receipt_id=receipt_id,
            )
            if receipt_details and receipt_details.receipt:
                break
        except EntityNotFoundError:
            # Receipt doesn't exist - continue to try fallback
            if img_id == sanitized_image_id and sanitized_image_id != image_id:
                logger.debug(
                    "Receipt not found for sanitized %s#%s, trying original image_id",
                    img_id,
                    receipt_id,
                )
            continue
        except Exception as e:
            if img_id == sanitized_image_id and sanitized_image_id != image_id:
                logger.debug(
                    "get_receipt_details failed for sanitized %s#%s, "
                    "trying original: %s",
                    img_id,
                    receipt_id,
                    e,
                )
            continue

    if not receipt_details or not receipt_details.receipt:
        # Try alternative methods to fetch receipt details
        logger.info(
            "Primary get_receipt_details failed for %s#%s, trying alternative methods...",
            image_id,
            receipt_id,
        )
        receipt_details = _fetch_receipt_details_fallback(
            dynamo_client, sanitized_image_id, receipt_id
        )

    return receipt_details


# ======================================================================
# Tool Factory for CoVe
# ======================================================================


def create_cove_tools(
    dynamo_client: "DynamoClient",
    place_id: str,
    canonical_merchant_name: str,
    canonical_address: Optional[str],
    canonical_phone: Optional[str],
    receipts: list[dict],
) -> tuple[list[Any], dict]:
    """
    Create tools for the CoVe text consistency agent.

    Args:
        dynamo_client: DynamoDB client
        place_id: Google Place ID
        canonical_merchant_name: Canonical merchant name
        canonical_address: Canonical address
        canonical_phone: Canonical phone
        receipts: List of receipts: [{'image_id': str, 'receipt_id': int}, ...]

    Returns:
        (tools, state_holder)
    """
    state_holder = {
        "place_id": place_id,
        "canonical_merchant_name": canonical_merchant_name,
        "canonical_address": canonical_address,
        "canonical_phone": canonical_phone,
        "receipts": receipts,
        "consistency_result": None,
    }

    # ========== GROUP SUMMARY TOOL ==========

    @tool
    def get_group_summary() -> dict:
        """
        Get a summary of all receipts in this place_id group.

        Returns:
        - place_id: The Google Place ID
        - canonical_merchant_name: Canonical merchant name
        - canonical_address: Canonical address
        - canonical_phone: Canonical phone
        - receipt_count: Number of receipts
        - receipts: List of receipts with their image_id and receipt_id
        """
        # Read from state_holder to support cached graph reuse
        current_place_id = state_holder.get("place_id", place_id)
        current_merchant_name = state_holder.get(
            "canonical_merchant_name", canonical_merchant_name
        )
        current_address = state_holder.get(
            "canonical_address", canonical_address
        )
        current_phone = state_holder.get("canonical_phone", canonical_phone)
        current_receipts = state_holder.get("receipts", receipts)

        return {
            "place_id": current_place_id,
            "canonical_merchant_name": current_merchant_name,
            "canonical_address": current_address,
            "canonical_phone": current_phone,
            "receipt_count": len(current_receipts),
            "receipts": [
                {
                    "image_id": r.get("image_id"),
                    "receipt_id": r.get("receipt_id"),
                }
                for r in current_receipts
            ],
        }

    # ========== RECEIPT TEXT TOOLS ==========

    class GetReceiptTextInput(BaseModel):
        """Input for get_receipt_text tool."""

        image_id: str = Field(description="Image ID of the receipt")
        receipt_id: int = Field(description="Receipt ID")

    @tool(args_schema=GetReceiptTextInput)
    def get_receipt_text(image_id: str, receipt_id: int) -> dict:
        """
        Get formatted receipt text (receipt-space grouping) for a
        specific receipt.

        This returns the receipt text formatted with visually contiguous lines
        grouped together.
        Use this to examine what text is actually on the receipt.

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - image_id: Image ID of the receipt
        - receipt_id: Receipt ID
        - found: Whether receipt details were found
        - formatted_text: Receipt text formatted in receipt-space
          (grouped rows)
        - line_count: Number of lines on the receipt
        - error: Error message if receipt not found or error occurred
        """
        try:
            # Sanitize image_id for fallback fetches
            sanitized_image_id = image_id.rstrip("? \t\n\r")

            # Use shared fetch logic
            receipt_details = _fetch_receipt_with_fallback(
                dynamo_client, image_id, receipt_id
            )

            # Handle case where we might have lines/words but no receipt entity
            lines = (
                receipt_details.lines
                if receipt_details and receipt_details.lines
                else []
            )

            # If receipt_details lacks lines, try direct fetch
            # with sanitized image_id
            if not lines:
                for img_id in [sanitized_image_id, image_id]:
                    try:
                        lines = dynamo_client.list_receipt_lines_from_receipt(
                            img_id, receipt_id
                        )
                        if lines:
                            logger.info(
                                "Fetched %s lines directly for %s#%s in get_receipt_text",
                                len(lines),
                                img_id,
                                receipt_id,
                            )
                            break
                    except EntityNotFoundError:
                        # Lines don't exist for this image_id - try next one
                        continue
                    except Exception as e:
                        logger.debug(
                            "Could not fetch lines for %s#%s: %s",
                            img_id,
                            receipt_id,
                            e,
                        )

            if not lines:
                # Check if we have receipt_details but no lines
                if receipt_details and receipt_details.receipt:
                    return {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "found": True,
                        "formatted_text": "(No lines found on receipt)",
                        "line_count": 0,
                    }
                else:
                    logger.warning(
                        "Receipt details not found for %s#%s after fallback. "
                        "Metadata exists but receipt lines/words are missing from "
                        "DynamoDB. Skipping text consistency check for this receipt.",
                        image_id,
                        receipt_id,
                    )
                    return {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "found": False,
                        "error": (
                            "Receipt details not found for "
                            f"{image_id}#{receipt_id}"
                        ),
                        "formatted_text": "(Receipt details not available)",
                        "line_count": 0,
                    }

            try:
                formatted_text = format_receipt_text_receipt_space(lines)
            except Exception as exc:
                logger.debug(
                    "Could not format receipt text (receipt-space): %s",
                    exc,
                )
                sorted_lines = sorted(lines, key=lambda line: line.line_id)
                formatted_text = "\n".join(
                    f"{line.line_id}: {line.text}" for line in sorted_lines
                )

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": True,
                "formatted_text": formatted_text,
                "line_count": len(lines),
            }

        except EntityNotFoundError as e:
            logger.warning(
                "Receipt not found for %s#%s: %s",
                image_id,
                receipt_id,
                e,
            )
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": False,
                "error": f"Receipt not found: {str(e)}",
                "formatted_text": "(Receipt not found)",
                "line_count": 0,
            }
        except Exception as e:
            error_str = str(e)
            # Check if this is a "receipt not found" type error
            if (
                "not found" in error_str.lower()
                or "receipt details" in error_str.lower()
            ):
                logger.warning(
                    "Receipt details not available for %s#%s: %s",
                    image_id,
                    receipt_id,
                    error_str,
                )
            else:
                logger.exception(
                    "Error getting receipt text for %s#%s",
                    image_id,
                    receipt_id,
                )
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": False,
                "error": str(e),
                "formatted_text": "(Error retrieving receipt text)",
                "line_count": 0,
            }

    class BatchCheckReceiptsInput(BaseModel):
        """Input for batch_check_receipts tool."""

        receipts: list[dict] = Field(
            description=(
                "List of receipts to check. Each receipt should be a dict "
                "with 'image_id' (str) and 'receipt_id' (int). "
                "Recommended batch size: 5-10 receipts to balance efficiency "
                "and context limits."
            )
        )

    @tool(args_schema=BatchCheckReceiptsInput)
    def batch_check_receipts(receipts: list[dict]) -> dict:
        """
        Check multiple receipts' text in a single call (efficient for large
        groups).

        This tool fetches receipt text for multiple receipts at once,
        reducing the number of tool calls needed. Use this instead of
        calling get_receipt_text individually for each receipt.

        Args:
            receipts: List of receipt dicts, each with 'image_id' (str) and
                'receipt_id' (int). Recommended batch size: 5-10 receipts.

        Returns:
            Dictionary with:
            - checked_count: Number of receipts successfully checked
                (with text found)
            - total_requested: Total number of receipts requested
            - total_processed: Number of receipts processed in this batch
                (may be less if batch size limit reached)
            - has_more: Whether there are more receipts to process (if batch
                size limit was reached)
            - results: List of results, one per receipt, each containing:
              - image_id: Image ID
              - receipt_id: Receipt ID
              - found: Whether receipt text was found
              - formatted_text: Receipt text (truncated if very long to manage
                context)
              - line_count: Number of lines
              - text_preview: First 200 characters of text (for quick scanning)
              - error: Error message if not found
            - message: Summary message indicating if more receipts need to be
                checked
        """
        # Limit batch size to prevent context overflow
        # Estimate: ~500-1000 chars per receipt text = ~125-250 tokens
        # per receipt
        # With 10 receipts = ~1250-2500 tokens, safe for context
        # We'll dynamically adjust based on actual text length
        if len(receipts) > COVE_MAX_BATCH_SIZE:
            logger.info(
                "Batch size %s exceeds max %s, processing first %s receipts",
                len(receipts),
                COVE_MAX_BATCH_SIZE,
                COVE_MAX_BATCH_SIZE,
            )
            receipts_to_process = receipts[:COVE_MAX_BATCH_SIZE]
        else:
            receipts_to_process = receipts

        results = []
        checked_count = 0
        total_chars_so_far = 0

        for receipt in receipts_to_process:
            image_id = receipt.get("image_id")
            receipt_id = receipt.get("receipt_id")

            if not image_id or receipt_id is None:
                results.append(
                    {
                        "image_id": image_id or "unknown",
                        "receipt_id": receipt_id or -1,
                        "found": False,
                        "error": "Missing image_id or receipt_id",
                        "formatted_text": "",
                        "line_count": 0,
                        "text_preview": "",
                    }
                )
                continue

            try:
                # Sanitize image_id
                sanitized_image_id = image_id.rstrip("? \t\n\r")

                # Try to get receipt text (reuse logic from get_receipt_text)
                receipt_details = None
                for img_id in [sanitized_image_id, image_id]:
                    try:
                        receipt_details = dynamo_client.get_receipt_details(
                            image_id=img_id,
                            receipt_id=receipt_id,
                        )
                        if receipt_details and receipt_details.receipt:
                            break
                    except EntityNotFoundError:
                        continue
                    except Exception:
                        logger.debug(
                            "get_receipt_details failed for %s#%s",
                            img_id,
                            receipt_id,
                        )
                        continue

                if not receipt_details:
                    receipt_details = _fetch_receipt_details_fallback(
                        dynamo_client, sanitized_image_id, receipt_id
                    )

                lines = receipt_details.lines or [] if receipt_details else []

                if not lines:
                    # Try direct fetch
                    for img_id in [sanitized_image_id, image_id]:
                        try:
                            lines = (
                                dynamo_client.list_receipt_lines_from_receipt(
                                    img_id, receipt_id
                                )
                            )
                            if lines:
                                break
                        except Exception:
                            logger.debug(
                                "get_receipt_details failed for %s#%s",
                                img_id,
                                receipt_id,
                            )
                            continue

                if not lines:
                    results.append(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "found": False,
                            "error": "Receipt details not available",
                            "formatted_text": "",
                            "line_count": 0,
                            "text_preview": "",
                        }
                    )
                    continue

                # Format text
                try:
                    formatted_text = format_receipt_text_receipt_space(lines)
                except Exception:
                    sorted_lines = sorted(lines, key=lambda line: line.line_id)
                    formatted_text = "\n".join(
                        f"{line.line_id}: {line.text}" for line in sorted_lines
                    )

                # Smart truncation: adjust based on total context used so far
                # Estimate remaining capacity and truncate if needed
                text_length = len(formatted_text)
                remaining_capacity = COVE_MAX_TOTAL_CHARS - total_chars_so_far

                # Reserve space for other receipts in batch
                # (estimate 500 chars each)
                remaining_receipts = (
                    len(receipts_to_process) - len(results) - 1
                )
                reserved_space = remaining_receipts * 500
                available_space = max(
                    1000, remaining_capacity - reserved_space
                )  # At least 1000 chars

                text_preview = formatted_text[:200] if formatted_text else ""

                # Truncate if text is too long or would exceed total capacity
                MAX_TEXT_LENGTH = min(2000, available_space)  # Dynamic limit
                if text_length > MAX_TEXT_LENGTH:
                    formatted_text = formatted_text[:MAX_TEXT_LENGTH] + (
                        f"\n... (truncated, "
                        f"{text_length - MAX_TEXT_LENGTH} chars remaining)"
                    )
                    text_length = MAX_TEXT_LENGTH

                total_chars_so_far += text_length

                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "found": True,
                        "formatted_text": formatted_text,
                        "line_count": len(lines),
                        "text_preview": text_preview,
                    }
                )
                checked_count += 1

            except EntityNotFoundError:
                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "found": False,
                        "error": "Receipt not found",
                        "formatted_text": "",
                        "line_count": 0,
                        "text_preview": "",
                    }
                )
            except Exception as e:
                error_str = str(e)
                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "found": False,
                        "error": error_str[:200],  # Truncate long errors
                        "formatted_text": "",
                        "line_count": 0,
                        "text_preview": "",
                    }
                )

        # Check if there are more receipts to process
        has_more = len(receipts) > len(receipts_to_process)

        status_msg = (
            "More receipts remain - call batch_check_receipts again "
            "with remaining receipts."
            if has_more
            else "All receipts processed."
        )
        return {
            "checked_count": checked_count,
            "total_requested": len(receipts),
            "total_processed": len(receipts_to_process),
            "has_more": has_more,
            "results": results,
            "message": (
                (
                    "Processed "
                    f"{len(receipts_to_process)}"
                    f" of {len(receipts)} receipts. "
                    f"{status_msg}"
                )
            ),
        }

    class GetReceiptContentInput(BaseModel):
        """Input for get_receipt_content tool."""

        image_id: str = Field(description="Image ID of the receipt")
        receipt_id: int = Field(description="Receipt ID")

    @tool(args_schema=GetReceiptContentInput)
    def get_receipt_content(image_id: str, receipt_id: int) -> dict:
        """
        Get raw lines and labeled words for a specific receipt.

        Use this for deeper inspection when formatted text isn't enough.

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - image_id: Image ID of the receipt
        - receipt_id: Receipt ID
        - found: Whether receipt details were found
        - lines: All text lines on the receipt
        - labeled_words: Words with labels (MERCHANT_NAME, ADDRESS, PHONE,
          etc.)
        - error: Error message if receipt not found or error occurred
        """
        try:
            # Sanitize image_id for fallback fetches
            sanitized_image_id = image_id.rstrip("? \t\n\r")

            # Use shared fetch logic
            receipt_details = _fetch_receipt_with_fallback(
                dynamo_client, image_id, receipt_id
            )

            # Handle case where we might have lines/words but no receipt entity
            lines_list = receipt_details.lines or [] if receipt_details else []
            words_list = receipt_details.words or [] if receipt_details else []

            # If we lack lines/words from receipt_details, try direct fetch
            # with sanitized image_id
            if not lines_list:
                for img_id in [sanitized_image_id, image_id]:
                    try:
                        lines_list = (
                            dynamo_client.list_receipt_lines_from_receipt(
                                img_id, receipt_id
                            )
                        )
                        if lines_list:
                            logger.info(
                                "Fetched %s lines directly for %s#%s",
                                len(lines_list),
                                img_id,
                                receipt_id,
                            )
                            break
                    except EntityNotFoundError:
                        # Lines don't exist for this image_id - try next one
                        continue
                    except Exception as e:
                        logger.debug(
                            "Could not fetch lines for %s#%s: %s",
                            img_id,
                            receipt_id,
                            e,
                        )

            if not words_list:
                for img_id in [sanitized_image_id, image_id]:
                    try:
                        words_list = (
                            dynamo_client.list_receipt_words_from_receipt(
                                img_id, receipt_id
                            )
                        )
                        if words_list:
                            logger.info(
                                "Fetched %s words directly for %s#%s",
                                len(words_list),
                                img_id,
                                receipt_id,
                            )
                            break
                    except EntityNotFoundError:
                        # Words don't exist for this image_id - try next one
                        continue
                    except Exception as e:
                        logger.debug(
                            "Could not fetch words for %s#%s: %s",
                            img_id,
                            receipt_id,
                            e,
                        )

            lines = [
                {"line_id": line.line_id, "text": line.text}
                for line in lines_list
            ]

            # Get labeled words
            labeled_words = [
                {
                    "text": word.text,
                    "label": getattr(word, "label", None),
                }
                for word in words_list
                if getattr(word, "label", None)
                in ["MERCHANT_NAME", "ADDRESS", "PHONE", "TOTAL"]
            ]

            if not lines and not labeled_words:
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "found": False,
                    "error": (
                        "Receipt details not found for "
                        f"{image_id}#{receipt_id}"
                    ),
                    "lines": [],
                    "labeled_words": [],
                }

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": True,
                "lines": lines[:20],  # Limit to first 20 lines
                "labeled_words": labeled_words,
            }

        except EntityNotFoundError as e:
            logger.warning(
                "Receipt not found for %s#%s: %s",
                image_id,
                receipt_id,
                e,
            )
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": False,
                "error": f"Receipt not found: {str(e)}",
                "lines": [],
                "labeled_words": [],
            }
        except Exception as e:
            error_str = str(e)
            # Check if this is a "receipt not found" type error
            if (
                "not found" in error_str.lower()
                or "receipt details" in error_str.lower()
            ):
                logger.warning(
                    "Receipt details not available for %s#%s: %s",
                    image_id,
                    receipt_id,
                    error_str,
                )
            else:
                logger.exception(
                    "Error getting receipt content for %s#%s",
                    image_id,
                    receipt_id,
                )
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": False,
                "error": str(e),
                "lines": [],
                "labeled_words": [],
            }

    # Add submission tool
    submit_tool = create_text_consistency_submission_tool(state_holder)

    # Return tools
    tools = [
        get_group_summary,
        batch_check_receipts,  # Add batch tool first (recommended)
        get_receipt_text,
        get_receipt_content,
        submit_tool,
    ]

    return tools, state_holder


# ======================================================================
# Workflow Builder
# ======================================================================


def create_cove_text_consistency_graph(
    dynamo_client: "DynamoClient",
    place_id: str,
    canonical_merchant_name: str,
    canonical_address: Optional[str],
    canonical_phone: Optional[str],
    receipts: list[dict],
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """
    Create the CoVe text consistency workflow.

    Args:
        dynamo_client: DynamoDB client
        place_id: Google Place ID
        canonical_merchant_name: Canonical merchant name from harmonizer
        canonical_address: Canonical address from harmonizer
        canonical_phone: Canonical phone from harmonizer
        receipts: List of receipts: [{'image_id': str, 'receipt_id': int}, ...]
        settings: Optional settings

    Returns:
        (compiled_graph, state_holder) - The graph and state dict
    """
    if settings is None:
        settings = get_settings()

    # Create tools
    tools, state_holder = create_cove_tools(
        dynamo_client=dynamo_client,
        place_id=place_id,
        canonical_merchant_name=canonical_merchant_name,
        canonical_address=canonical_address,
        canonical_phone=canonical_phone,
        receipts=receipts,
    )

    # Create LLM with tools bound using shared utility
    llm = create_ollama_llm(settings=settings, temperature=0.0)
    llm = llm.bind_tools(tools)

    # Create agent node with retry logic using shared utility
    agent_node = create_agent_node_with_retry(
        llm=llm,
        agent_name="cove",
    )

    # Define tool node
    tool_node = ToolNode(tools)

    # Define routing function
    def should_continue(state: CoveTextConsistencyState) -> str:
        """Check if we should continue or end."""
        # Check if consistency result was submitted
        if state_holder.get("consistency_result") is not None:
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    return "tools"
                # Give it another chance if no tool calls
                if len(state.messages) > 10:
                    logger.warning(
                        "CoVe agent has made many steps and "
                        "still has not submitted - may need reminder"
                    )
            if len(state.messages) > 20:
                logger.error("CoVe agent exceeded 20 steps - forcing end")
                return "end"
            return "agent"

        return "agent"

    # Build the graph
    workflow = StateGraph(CoveTextConsistencyState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add conditional edges
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "agent": "agent",  # Loop back if no tool calls
            "end": END,
        },
    )

    # After tools, go back to agent
    workflow.add_edge("tools", "agent")

    # Compile
    compiled = workflow.compile()

    return compiled, state_holder


# ======================================================================
# CoVe Runner
# ======================================================================


async def run_cove_text_consistency(
    graph: Any,
    state_holder: dict,
    place_id: str,
    canonical_merchant_name: str,
    canonical_address: Optional[str],
    canonical_phone: Optional[str],
    receipts: list[dict],
) -> dict:
    """
    Run the CoVe text consistency workflow.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        place_id: Google Place ID
        canonical_merchant_name: Canonical merchant name
        canonical_address: Canonical address
        canonical_phone: Canonical phone
        receipts: List of receipts: [{'image_id': str, 'receipt_id': int}, ...]

    Returns:
        Consistency result dict
    """
    # Clear any stale consistency result from previous runs
    # (important when graph is cached and reused)
    state_holder["consistency_result"] = None

    # Create initial state
    initial_state = CoveTextConsistencyState(
        place_id=place_id,
        canonical_merchant_name=canonical_merchant_name,
        canonical_address=canonical_address,
        canonical_phone=canonical_phone,
        receipts=receipts,
        messages=[
            SystemMessage(content=COVE_TEXT_CONSISTENCY_PROMPT),
            HumanMessage(
                content=(
                    f"Please verify text consistency for {len(receipts)} "
                    f"receipts with place_id {place_id}. Check that each "
                    f"receipt's text matches the canonical metadata: "
                    f"merchant_name='{canonical_merchant_name}', "
                    f"address='{canonical_address or 'N/A'}', "
                    f"phone='{canonical_phone or 'N/A'}'."
                ),
            ),
        ],
    )

    logger.info(
        "Starting CoVe text consistency check for place_id %s (%s receipts)",
        place_id,
        len(receipts),
    )

    # Store receipts in state_holder for validation in submit_text_consistency
    state_holder["receipts"] = receipts
    state_holder["place_id"] = place_id
    state_holder["canonical_merchant_name"] = canonical_merchant_name
    state_holder["canonical_address"] = canonical_address
    state_holder["canonical_phone"] = canonical_phone

    # Run the workflow
    try:
        config = {
            "recursion_limit": 50,
            "configurable": {
                "thread_id": f"cove_{place_id}",
            },
        }

        # Add LangSmith metadata if tracing is enabled
        if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
            config["metadata"] = {
                "place_id": place_id,
                "receipt_count": len(receipts),
                "workflow": "cove_text_consistency",
            }

        await graph.ainvoke(initial_state, config=config)

        # Get result from state holder
        result = state_holder.get("consistency_result")

        if result:
            # Get actual receipt count from result (after deduplication)
            receipt_results_count = len(result.get("receipt_results", []))
            outlier_count = result.get("outlier_count", 0)
            logger.info(
                "CoVe check complete: %s/%s outliers (expected %s receipts)",
                outlier_count,
                receipt_results_count,
                len(receipts),
            )
            return {
                "status": "success",
                "result": result,
            }
        else:
            # Agent ended without submitting result
            logger.warning(
                "CoVe agent ended without submitting result for place_id %s",
                place_id,
            )
            return {
                "status": "incomplete",
                "error": "Agent did not submit consistency results",
                "place_id": place_id,
                "receipt_count": len(receipts),
            }

    except Exception as e:
        logger.exception("Error in CoVe text consistency check")
        return {
            "status": "error",
            "error": f"{e!s}",
            "place_id": place_id,
            "receipt_count": len(receipts),
        }

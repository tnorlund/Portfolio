"""
CoVe (Consistency Verification) workflow for checking receipt text consistency.

This sub-agent verifies that all receipts sharing the same place_id actually
contain text consistent with being from the same place. It compares receipt
text against canonical metadata to identify outliers.
"""

import logging
import os
from typing import Annotated, Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space

logger = logging.getLogger(__name__)


# ==============================================================================
# Agent State
# ==============================================================================


class CoveTextConsistencyState(BaseModel):
    """State for the CoVe text consistency workflow."""

    place_id: str = Field(description="Google Place ID being verified")
    canonical_merchant_name: str = Field(
        description="Canonical merchant name from harmonizer"
    )
    canonical_address: Optional[str] = Field(
        default=None, description="Canonical address from harmonizer"
    )
    canonical_phone: Optional[str] = Field(
        default=None, description="Canonical phone from harmonizer"
    )
    receipts: list[dict] = Field(
        default_factory=list,
        description="List of receipts to verify: [{'image_id': str, 'receipt_id': int}, ...]",
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


# ==============================================================================
# System Prompt
# ==============================================================================

COVE_TEXT_CONSISTENCY_PROMPT = """You are a receipt text consistency verifier (CoVe). Your job is to verify that all receipts sharing the same place_id actually contain text consistent with being from the same place.

## Your Task

You are given:
- A place_id group of receipts
- Canonical metadata (merchant_name, address, phone) determined by the harmonizer
- Access to the actual receipt text for each receipt

Your job is to:
1. Examine the receipt text for each receipt in the group
2. Compare each receipt's text against the canonical metadata
3. Verify that merchant name, address, and phone appear in the receipt text (allowing for OCR errors)
4. Identify any receipts that appear to be from a different place (outliers)
5. Submit a consistency report with per-receipt verdicts

## Available Tools

### Receipt Text Tools
- `get_receipt_text`: Get formatted receipt text (receipt-space grouping) for a specific receipt
- `get_receipt_content`: Get raw lines and labeled words for a specific receipt
- `get_group_summary`: See all receipts in this group with their metadata

## Strategy

1. **Start** by getting the group summary to see all receipts
   - Use `get_group_summary` to list all receipts with their image_id and receipt_id

2. **Examine receipt text** for each receipt:
   - Use `get_receipt_text` to get formatted text for each receipt
   - Or use `get_receipt_content` for deeper inspection (raw lines, labeled words)
   - **Note**: Some receipts may not have receipt details (lines/words) in DynamoDB.
     If `get_receipt_text` returns an error or "not found", mark that receipt as UNSURE
     with evidence "Receipt details not available" and continue with other receipts.
   - Look for the canonical merchant name, address, and phone in the text

3. **Compare against canonical metadata**:
   - Does the merchant name appear in the receipt text? (case-insensitive, allow OCR errors)
   - Does the address appear? (street number + street name, allow formatting differences)
   - Does the phone number appear? (normalized digits, allow formatting differences)
   - **For receipts without text**: Mark as UNSURE with evidence "Cannot verify - receipt details not available"

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
   - **Important**: Even if some receipts don't have text, you must still submit results
     for ALL receipts in the group. Mark missing receipts as UNSURE.

## Important Rules

1. ALWAYS start with `get_group_summary` to see all receipts
2. Check receipt text for EACH receipt in the group (skip if details not available)
3. If a receipt doesn't have text/details, mark it as UNSURE and continue
4. Be lenient with OCR errors - minor typos are OK
5. Be strict with complete mismatches - if merchant/address/phone all differ, it's likely a different place
6. ALWAYS end with `submit_text_consistency` - never end without calling it
7. You MUST submit results for ALL receipts, even if some don't have text

## Status Definitions

- **SAME_PLACE**: Receipt text matches canonical metadata (allowing OCR errors)
- **MISMATCH**: Receipt text clearly indicates a different place (2+ fields mismatch)
- **UNSURE**: Ambiguous case (1 field mismatch, unclear due to OCR quality, or receipt details not available)

Begin by getting the group summary, then systematically check each receipt's text."""


# ==============================================================================
# Text Consistency Submission Tool
# ==============================================================================


def create_text_consistency_submission_tool(state_holder: dict):
    """Create a tool for submitting text consistency results."""
    from pydantic import BaseModel, Field

    class ReceiptConsistencyResult(BaseModel):
        """Result for a single receipt."""

        image_id: str = Field(description="Image ID")
        receipt_id: int = Field(description="Receipt ID")
        status: str = Field(
            description="Status: SAME_PLACE, MISMATCH, or UNSURE"
        )
        evidence: str = Field(
            description="Evidence for this status (what text was found/missing)"
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

        Call this when you've checked all receipts and determined their consistency status.
        This ends the workflow.

        Args:
            receipt_results: List of consistency results, one per receipt
            overall_confidence: Overall confidence in the consistency check (0.0-1.0)
            reasoning: Explanation of the results
        """
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
            f"Text consistency submitted: {len(outliers)}/{len(receipt_results)} outliers "
            f"(confidence={overall_confidence:.2%})"
        )

        return {
            "status": "submitted",
            "message": f"Consistency check complete. {len(outliers)} outliers found.",
            "result": result,
        }

    return submit_text_consistency


# ==============================================================================
# Helper Functions
# ==============================================================================


def _fetch_receipt_details_fallback(
    dynamo_client: Any, image_id: str, receipt_id: int
) -> Optional[Any]:
    """
    Fallback method to fetch receipt details using alternative queries.

    If get_receipt_details() fails, try to fetch receipt, lines, and words
    separately and construct ReceiptDetails.

    Args:
        dynamo_client: DynamoDB client
        image_id: Image ID (may have trailing characters like '?')
        receipt_id: Receipt ID

    Returns:
        ReceiptDetails if successful, None otherwise
    """
    try:
        from receipt_dynamo.entities.receipt_details import ReceiptDetails
        from receipt_dynamo.exceptions import EntityNotFoundError

        # Sanitize image_id - remove trailing whitespace and special characters
        # Some image_ids may have trailing '?' or other characters
        sanitized_image_id = image_id.rstrip("? \t\n\r")
        
        # Try sanitized version first, then original if different
        image_ids_to_try = [sanitized_image_id]
        if sanitized_image_id != image_id:
            image_ids_to_try.append(image_id)
            logger.debug(
                f"Sanitized image_id '{image_id}' to '{sanitized_image_id}'"
            )

        # Try to get receipt entity
        receipt = None
        for img_id in image_ids_to_try:
            try:
                receipt = dynamo_client.get_receipt(img_id, receipt_id)
                if receipt:
                    # Use the working image_id for subsequent queries
                    image_id = img_id
                    break
            except EntityNotFoundError:
                continue
            except Exception as e:
                logger.debug(
                    f"Error fetching receipt for {img_id}#{receipt_id}: {e}"
                )
                continue

        # If we found receipt with sanitized ID, use that for subsequent queries
        if receipt:
            image_id = sanitized_image_id

        # Try to fetch lines and words directly (they might exist even if receipt doesn't)
        lines = []
        words = []
        
        # Try both sanitized and original image_id for lines/words
        for img_id in image_ids_to_try:
            if lines and words:
                break  # Already found both
                
            if not lines:
                try:
                    lines = dynamo_client.list_receipt_lines_from_receipt(
                        img_id, receipt_id
                    )
                    if lines:
                        image_id = img_id  # Use working image_id
                        logger.debug(
                            f"Fetched {len(lines)} lines for {img_id}#{receipt_id} via fallback"
                        )
                except Exception as e:
                    logger.debug(
                        f"Could not fetch lines for {img_id}#{receipt_id}: {e}"
                    )
            
            if not words:
                try:
                    words = dynamo_client.list_receipt_words_from_receipt(
                        img_id, receipt_id
                    )
                    if words:
                        image_id = img_id  # Use working image_id
                        logger.debug(
                            f"Fetched {len(words)} words for {img_id}#{receipt_id} via fallback"
                        )
                except Exception as e:
                    logger.debug(
                        f"Could not fetch words for {img_id}#{receipt_id}: {e}"
                    )

        # If we have lines or words, we can still work with them
        if lines or words:
            # If we don't have receipt entity, we can't create full ReceiptDetails
            # But we can return a partial result
            if receipt:
                return ReceiptDetails(
                    receipt=receipt,
                    lines=lines,
                    words=words,
                    letters=[],
                    labels=[],
                )
            else:
                # We have lines/words but no receipt entity
                # Try to get receipt one more time using get_receipt (with sanitized ID)
                try:
                    receipt = dynamo_client.get_receipt(sanitized_image_id, receipt_id)
                    if receipt:
                        image_id = sanitized_image_id  # Use sanitized version
                        return ReceiptDetails(
                            receipt=receipt,
                            lines=lines,
                            words=words,
                            letters=[],
                            labels=[],
                        )
                except Exception as e:
                    logger.debug(
                        f"Could not fetch receipt entity via get_receipt: {e}"
                    )

                # If we still don't have receipt, we can't create ReceiptDetails
                # But tools can work with lines/words directly
                logger.info(
                    f"Found {len(lines)} lines and {len(words)} words for {image_id}#{receipt_id} "
                    f"but no receipt entity. Tools will work with lines/words only."
                )
                # Return None - tools will handle this case by fetching lines/words directly
                return None

        return None

    except Exception as e:
        logger.debug(f"Fallback fetch failed for {image_id}#{receipt_id}: {e}")
        return None


# ==============================================================================
# Tool Factory for CoVe
# ==============================================================================


def create_cove_tools(
    dynamo_client: Any,
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
        return {
            "place_id": place_id,
            "canonical_merchant_name": canonical_merchant_name,
            "canonical_address": canonical_address,
            "canonical_phone": canonical_phone,
            "receipt_count": len(receipts),
            "receipts": [
                {
                    "image_id": r.get("image_id"),
                    "receipt_id": r.get("receipt_id"),
                }
                for r in receipts
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
        Get formatted receipt text (receipt-space grouping) for a specific receipt.

        This returns the receipt text formatted with visually contiguous lines grouped together.
        Use this to examine what text is actually on the receipt.

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - formatted_text: Receipt text formatted in receipt-space (grouped rows)
        - line_count: Number of lines on the receipt
        """
        try:
            receipt_details = dynamo_client.get_receipt_details(
                image_id=image_id, receipt_id=receipt_id
            )

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    f"Primary get_receipt_details failed for {image_id}#{receipt_id}, "
                    f"trying alternative methods..."
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, image_id, receipt_id
                )

            if not receipt_details or not receipt_details.receipt:
                logger.warning(
                    f"Receipt details not found for {image_id}#{receipt_id} after fallback. "
                    f"Metadata exists but receipt lines/words are missing from DynamoDB. "
                    f"Skipping text consistency check for this receipt."
                )
                return {
                    "error": f"Receipt details not found for {image_id}#{receipt_id}",
                    "found": False,
                    "formatted_text": "(Receipt details not available)",
                    "line_count": 0,
                }

            # Handle case where we might have lines/words but no receipt entity
            lines = receipt_details.lines or [] if receipt_details else []

            # If we don't have lines from receipt_details, try direct fetch
            if not lines:
                try:
                    lines = dynamo_client.list_receipt_lines_from_receipt(
                        image_id, receipt_id
                    )
                    if lines:
                        logger.info(
                            f"Fetched {len(lines)} lines directly for {image_id}#{receipt_id} "
                            f"in get_receipt_text"
                        )
                except Exception as e:
                    logger.debug(f"Could not fetch lines directly: {e}")

            if not lines:
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "found": True,
                    "formatted_text": "(No lines found on receipt)",
                    "line_count": 0,
                }

            try:
                formatted_text = format_receipt_text_receipt_space(lines)
            except Exception as exc:
                logger.debug(
                    f"Could not format receipt text (receipt-space): {exc}"
                )
                sorted_lines = sorted(lines, key=lambda l: l.line_id)
                formatted_text = "\n".join(
                    f"{ln.line_id}: {ln.text}" for ln in sorted_lines
                )

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": True,
                "formatted_text": formatted_text,
                "line_count": len(lines),
            }

        except Exception as e:
            error_str = str(e)
            # Check if this is a "receipt not found" type error
            if (
                "not found" in error_str.lower()
                or "receipt details" in error_str.lower()
            ):
                logger.warning(
                    f"Receipt details not available for {image_id}#{receipt_id}: {error_str}"
                )
            else:
                logger.error(
                    f"Error getting receipt text for {image_id}#{receipt_id}: {e}"
                )
            return {
                "error": str(e),
                "found": False,
                "formatted_text": "(Error retrieving receipt text)",
                "line_count": 0,
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
        - lines: All text lines on the receipt
        - labeled_words: Words with labels (MERCHANT_NAME, ADDRESS, PHONE, etc.)
        """
        try:
            receipt_details = dynamo_client.get_receipt_details(
                image_id=image_id, receipt_id=receipt_id
            )

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    f"Primary get_receipt_details failed for {image_id}#{receipt_id}, "
                    f"trying alternative methods..."
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, image_id, receipt_id
                )

            # Handle case where we might have lines/words but no receipt entity
            lines_list = receipt_details.lines or [] if receipt_details else []
            words_list = receipt_details.words or [] if receipt_details else []

            # If we don't have lines/words from receipt_details, try direct fetch
            if not lines_list:
                try:
                    lines_list = dynamo_client.list_receipt_lines_from_receipt(
                        image_id, receipt_id
                    )
                    if lines_list:
                        logger.info(
                            f"Fetched {len(lines_list)} lines directly for {image_id}#{receipt_id}"
                        )
                except Exception as e:
                    logger.debug(f"Could not fetch lines directly: {e}")

            if not words_list:
                try:
                    words_list = dynamo_client.list_receipt_words_from_receipt(
                        image_id, receipt_id
                    )
                    if words_list:
                        logger.info(
                            f"Fetched {len(words_list)} words directly for {image_id}#{receipt_id}"
                        )
                except Exception as e:
                    logger.debug(f"Could not fetch words directly: {e}")

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
                    "error": f"Receipt details not found for {image_id}#{receipt_id}",
                    "lines": [],
                    "labeled_words": [],
                }

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "lines": lines[:20],  # Limit to first 20 lines
                "labeled_words": labeled_words,
            }

        except Exception as e:
            error_str = str(e)
            # Check if this is a "receipt not found" type error
            if (
                "not found" in error_str.lower()
                or "receipt details" in error_str.lower()
            ):
                logger.warning(
                    f"Receipt details not available for {image_id}#{receipt_id}: {error_str}"
                )
            else:
                logger.error(
                    f"Error getting receipt content for {image_id}#{receipt_id}: {e}"
                )
            return {
                "error": str(e),
                "lines": [],
                "labeled_words": [],
            }

    # Add submission tool
    submit_tool = create_text_consistency_submission_tool(state_holder)

    # Return tools
    tools = [
        get_group_summary,
        get_receipt_text,
        get_receipt_content,
        submit_tool,
    ]

    return tools, state_holder


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_cove_text_consistency_graph(
    dynamo_client: Any,
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

    # Create LLM with tools bound
    api_key = settings.ollama_api_key.get_secret_value()
    llm = ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": (
                {"Authorization": f"Bearer {api_key}"} if api_key else {}
            ),
            "timeout": 120,
        },
        temperature=0.0,
    ).bind_tools(tools)

    # Define the agent node
    def agent_node(state: CoveTextConsistencyState) -> dict:
        """Call the LLM to decide next action."""
        messages = state.messages
        response = llm.invoke(messages)

        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.debug(
                f"CoVe agent tool calls: {[tc['name'] for tc in response.tool_calls]}"
            )

        return {"messages": [response]}

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
                        "CoVe agent has made many steps without submitting - may need reminder"
                    )
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


# ==============================================================================
# CoVe Runner
# ==============================================================================


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
                content=f"Please verify text consistency for {len(receipts)} receipts "
                f"with place_id {place_id}. Check that each receipt's text matches "
                f"the canonical metadata: merchant_name='{canonical_merchant_name}', "
                f"address='{canonical_address or 'N/A'}', phone='{canonical_phone or 'N/A'}'."
            ),
        ],
    )

    logger.info(
        f"Starting CoVe text consistency check for place_id {place_id} "
        f"({len(receipts)} receipts)"
    )

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

        final_state = await graph.ainvoke(initial_state, config=config)

        # Get result from state holder
        result = state_holder.get("consistency_result")

        if result:
            logger.info(
                f"CoVe check complete: {result.get('outlier_count', 0)}/{len(receipts)} outliers"
            )
            return {
                "status": "success",
                "result": result,
            }
        else:
            # Agent ended without submitting result
            logger.warning(
                f"CoVe agent ended without submitting result for place_id {place_id}"
            )
            return {
                "status": "incomplete",
                "error": "Agent did not submit consistency results",
                "place_id": place_id,
                "receipt_count": len(receipts),
            }

    except Exception as e:
        logger.error(f"Error in CoVe text consistency check: {e}")
        return {
            "status": "error",
            "error": str(e),
            "place_id": place_id,
            "receipt_count": len(receipts),
        }

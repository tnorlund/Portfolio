"""
Agentic workflow for harmonizing receipt metadata within a place_id group.

This workflow uses an LLM agent to reason about receipts that share the same
place_id and determine the correct canonical metadata for the group.

Key Insight
-----------
Receipts with the same place_id MUST have consistent metadata. Any inconsistency
indicates a data quality issue. The agent:
1. Examines all receipts in the group
2. Validates against Google Places
3. Reasons about edge cases (OCR errors, address-like names)
4. Determines the correct canonical values

Why Agent-Based is Better
-------------------------
V2 uses simple majority voting for consensus. This fails for:
- Groups where the majority has OCR errors
- Edge cases like address-like merchant names
- Cases where Google Places data conflicts with receipt data

The agent can reason about these cases and make smarter decisions.
"""

import asyncio
import logging
import os
import random
import re
import time
from typing import Annotated, Any, Callable, Optional

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


class HarmonizerAgentState(BaseModel):
    """State for the harmonizer agent workflow."""

    # Target place_id group
    place_id: str = Field(description="Google Place ID being harmonized")
    receipts: list[dict] = Field(
        default_factory=list, description="Receipts in this group"
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


# ==============================================================================
# System Prompt
# ==============================================================================

HARMONIZER_PROMPT = """You are a receipt metadata harmonizer. Your job is to ensure all receipts sharing the same Google Place ID have consistent, correct metadata.

## Your Task

You're given a group of receipts that all share the same `place_id`. These receipts SHOULD have identical metadata, but they may differ due to:
- OCR errors (typos in merchant name, address, phone)
- Different formatting (e.g., "Vons" vs "VONS" vs "vons")
- Missing data on some receipts
- Wrong place_id assignment (rare)

Your job is to:
1. Examine all receipts in the group
2. Validate against Google Places API (source of truth)
3. Determine the correct canonical values for: merchant_name, address, phone
4. Identify which receipts need updates
5. Submit your harmonization decision

## Available Tools

### Group Analysis Tools
- `get_group_summary`: See all receipts in this group with their current metadata
- `get_receipt_content`: View the actual content (lines, words) of a specific receipt
- `display_receipt_text`: Display formatted receipt text (receipt-space grouping) for verification
- `get_field_variations`: See all variations of a field (merchant_name, address, phone) across the group

### Google Places Tools (Source of Truth)
- `verify_place_id`: Get official data from Google Places for this place_id
- `find_businesses_at_address`: If Google returns an address as name, find actual businesses there

### Metadata Correction Tools
- `find_correct_metadata`: Spin up a sub-agent to find the correct metadata for a receipt that appears to have incorrect metadata. Use this when a receipt's metadata doesn't match Google Places or other receipts in the group.

### Address Verification Tool
- `verify_address_on_receipt`: Verify that a specific address (from metadata or Google Places) actually appears on the receipt text. This is CRITICAL for catching wrong place_id assignments. If address doesn't match, use `find_correct_metadata` to fix it.

### Text Consistency Verification Tool
- `verify_text_consistency`: Verify that all receipts in the group actually contain text consistent with the canonical metadata. This uses CoVe (Consistency Verification) to check receipt text and identify outliers. Use this BEFORE submitting to ensure all receipts belong to the same place.

### Decision Tool (REQUIRED at the end)
- `submit_harmonization`: Submit your decision for canonical values and which receipts need updates

## Strategy

1. **Start** with `get_group_summary` to see all receipts and their metadata variations

2. **Check Google Places** with `verify_place_id` to get the official data
   - This is the source of truth for merchant_name, address, phone
   - If Google returns an address as the merchant name (e.g., "123 Main St"), use `find_businesses_at_address`

3. **Analyze variations** with `get_field_variations` to understand the inconsistencies
   - Which values are most common?
   - Are differences just formatting (case sensitivity)?
   - Are there OCR errors?

4. **CRITICAL: Verify addresses match receipt text** - This is essential to catch wrong place_id assignments
   - Use `display_receipt_text` or `verify_address_on_receipt` to check each receipt
   - Compare the address in metadata against what's actually printed on the receipt
   - If an address like "55 Fulton St, New York, NY 10038, USA" appears but the receipt shows a California address, this is a WRONG place_id assignment
   - **If address doesn't match receipt text, use `find_correct_metadata` to fix it immediately**
   - Do NOT proceed with harmonization if addresses don't match - fix them first

5. **Inspect receipt content** (if needed) with `get_receipt_content` or `display_receipt_text`
   - First call `get_group_summary` to see all receipts with their `image_id` and `receipt_id`
   - Then use `display_receipt_text(image_id, receipt_id)` to see formatted receipt text (receipt-space grouping) with verification prompt
   - Or use `get_receipt_content(image_id, receipt_id)` to see raw lines and labeled words
   - Use these to verify metadata matches what's actually on the receipt

6. **Find correct metadata** (if metadata appears incorrect) with `find_correct_metadata`
   - **USE THIS if address doesn't match receipt text** - this indicates wrong place_id
   - If a receipt's metadata doesn't match Google Places or seems wrong, use this tool
   - It spins up a sub-agent to find the correct place_id, merchant_name, address, and phone
   - The sub-agent examines receipt content, searches Google Places, and uses similarity search
   - Returns the correct metadata with confidence scores

7. **Verify text consistency** (RECOMMENDED before submitting) with `verify_text_consistency`
   - After determining canonical metadata, use this tool to verify all receipts actually belong to the same place
   - The CoVe sub-agent checks each receipt's text against canonical metadata
   - Identifies outliers (receipts that may be from a different place)
   - Use the results to adjust your harmonization decision if outliers are found

8. **Submit your decision** with `submit_harmonization`:
   - Canonical values from Google Places (preferred) or best-quality receipt data
   - List of receipts that need updates
   - Confidence in your decision

## Decision Guidelines

### Merchant Name
- **Prefer Google Places name** (official, correct spelling)
- If Google returns an address, find the actual business name
- Use proper case (Title Case preferred over ALL CAPS)
- Never accept an address as a merchant name

### Address
- **Prefer Google Places address** (properly formatted)
- If not available, use the most complete/common address from receipts

### Phone
- **Prefer Google Places phone** (correctly formatted)
- If not available, use the most common phone from receipts
- Normalize format (e.g., "(805) 555-1234" not "8055551234")

### Confidence Scoring
- High (0.8-1.0): Google Places confirms data, all/most receipts agree
- Medium (0.5-0.8): Some disagreement but clear best choice
- Low (0.0-0.5): Significant conflicts, may need manual review

## Important Rules

1. ALWAYS start with `get_group_summary` to understand the group
2. ALWAYS check Google Places with `verify_place_id` before deciding
3. **CRITICAL: ALWAYS verify addresses match receipt text** - Use `display_receipt_text` or `verify_address_on_receipt` to check
4. **If address doesn't match receipt text, use `find_correct_metadata` to fix it** - Don't proceed with wrong place_id
5. NEVER accept an address as a merchant name
6. RECOMMENDED: Use `verify_text_consistency` before submitting to check for outliers
7. ALWAYS end with `submit_harmonization`
8. Be thorough but efficient

## What Gets Updated

When you submit harmonization decisions, receipts with different values will have their:
- `merchant_name` → Canonical merchant name
- `address` → Canonical address
- `phone_number` → Canonical phone

Begin by getting the group summary, then validate with Google Places."""


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
            if receipt:
                # Full ReceiptDetails with receipt entity
                return ReceiptDetails(
                    receipt=receipt,
                    lines=lines,
                    words=words,
                    letters=[],
                    labels=[],
                )
            else:
                # We have lines/words but no receipt entity
                # Create a minimal receipt object from metadata if available
                # For now, we'll need the receipt entity, so try one more time
                # or create a minimal one
                logger.info(
                    f"Found {len(lines)} lines and {len(words)} words for {image_id}#{receipt_id} "
                    f"but no receipt entity. Attempting to create minimal receipt..."
                )
                # Try to get receipt one more time using get_receipt (with sanitized ID)
                try:
                    receipt = dynamo_client.get_receipt(
                        sanitized_image_id, receipt_id
                    )
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
                # But we can work with lines/words directly in the tools
                logger.warning(
                    f"Found lines/words for {image_id}#{receipt_id} but no receipt entity. "
                    f"Tools will work with lines/words only."
                )
                # Return None - tools will handle this case
                return None

        return None

    except Exception as e:
        logger.debug(f"Fallback fetch failed for {image_id}#{receipt_id}: {e}")
        return None


# ==============================================================================
# Tool Factory for Harmonizer
# ==============================================================================


def create_harmonizer_tools(
    dynamo_client: Any,
    places_api: Optional[Any] = None,
    group_data: Optional[dict] = None,
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[list[Any], dict]:
    """
    Create tools for the harmonizer agent.

    Args:
        dynamo_client: DynamoDB client
        places_api: Google Places API client
        group_data: Dict to hold current group context
        chroma_client: Optional pre-initialized ChromaDB client
        embed_fn: Optional pre-initialized embedding function
        chromadb_bucket: Optional S3 bucket name for lazy loading ChromaDB

    Returns:
        (tools, state_holder)
    """
    if group_data is None:
        group_data = {}

    state = {
        "group": group_data,
        "result": None,
        "chromadb_bucket": chromadb_bucket,  # Store for lazy loading
        "chroma_client": chroma_client,  # Cache if pre-initialized
        "embed_fn": embed_fn,  # Cache if pre-initialized
    }

    # ========== GROUP ANALYSIS TOOLS ==========

    @tool
    def get_group_summary() -> dict:
        """
        Get a summary of all receipts in this place_id group.

        Returns:
        - place_id: The Google Place ID
        - receipt_count: Number of receipts
        - receipts: List of receipts with their current metadata
        - field_summary: Quick view of variations in each field

        Use this first to understand the group.
        """
        group = state["group"]
        if not group:
            return {"error": "No group data loaded"}

        receipts = group.get("receipts", [])

        # Summarize field variations
        merchant_names = {}
        addresses = {}
        phones = {}

        for r in receipts:
            name = r.get("merchant_name", "")
            if name:
                merchant_names[name] = merchant_names.get(name, 0) + 1
            addr = r.get("address", "")
            if addr:
                addresses[addr] = addresses.get(addr, 0) + 1
            phone = r.get("phone", "")
            if phone:
                phones[phone] = phones.get(phone, 0) + 1

        return {
            "place_id": group.get("place_id"),
            "receipt_count": len(receipts),
            "receipts": [
                {
                    "image_id": r.get("image_id"),
                    "receipt_id": r.get("receipt_id"),
                    "merchant_name": r.get("merchant_name"),
                    "address": r.get("address"),
                    "phone": r.get("phone"),
                }
                for r in receipts
            ],
            "field_summary": {
                "merchant_names": dict(
                    sorted(merchant_names.items(), key=lambda x: -x[1])
                ),
                "addresses": dict(
                    sorted(addresses.items(), key=lambda x: -x[1])
                ),
                "phones": dict(sorted(phones.items(), key=lambda x: -x[1])),
            },
        }

    class GetReceiptContentInput(BaseModel):
        """Input for get_receipt_content tool."""

        image_id: str = Field(description="Image ID of the receipt")
        receipt_id: int = Field(description="Receipt ID")

    @tool(args_schema=GetReceiptContentInput)
    def get_receipt_content(image_id: str, receipt_id: int) -> dict:
        """
        Get the actual content (lines, words) of a specific receipt.

        Use this to inspect the raw OCR data when resolving ambiguous cases.

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - lines: All text lines on the receipt
        - labeled_words: Words with labels (MERCHANT_NAME, ADDRESS, PHONE, etc.)
        """
        try:
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
                except Exception as e:
                    if (
                        img_id == sanitized_image_id
                        and sanitized_image_id != image_id
                    ):
                        logger.debug(
                            f"get_receipt_details failed for sanitized {img_id}#{receipt_id}, "
                            f"trying original: {e}"
                        )
                    continue

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    f"Primary get_receipt_details failed for {image_id}#{receipt_id}, "
                    f"trying alternative methods..."
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, sanitized_image_id, receipt_id
                )

            # Handle case where we might have lines/words but no receipt entity
            lines = []
            words_list = []
            if receipt_details:
                lines = receipt_details.lines or []
                words_list = receipt_details.words or []
            elif not receipt_details:
                # Try to fetch lines/words directly even if receipt entity is missing
                # Use sanitized_image_id from above
                for img_id in [sanitized_image_id, image_id]:
                    try:
                        lines = dynamo_client.list_receipt_lines_from_receipt(
                            img_id, receipt_id
                        )
                        words_list = (
                            dynamo_client.list_receipt_words_from_receipt(
                                img_id, receipt_id
                            )
                        )
                        if lines or words_list:
                            logger.info(
                                f"Fetched {len(lines)} lines and {len(words_list)} words "
                                f"directly for {img_id}#{receipt_id}"
                            )
                            break
                    except Exception as e:
                        logger.debug(
                            f"Could not fetch lines/words for {img_id}#{receipt_id}: {e}"
                        )

            if not lines and not words_list:
                logger.warning(
                    f"Receipt details not found for {image_id}#{receipt_id} after fallback. "
                    f"Metadata exists but receipt lines/words are missing from DynamoDB."
                )
                return {
                    "error": f"Receipt details not found for {image_id}#{receipt_id}",
                    "lines": [],
                    "labeled_words": [],
                }

            lines_dict = [
                {"line_id": line.line_id, "text": line.text} for line in lines
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

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "lines": lines_dict[:20],  # Limit to first 20 lines
                "labeled_words": labeled_words,
            }

        except Exception as e:
            logger.error(f"Error getting receipt content: {e}")
            return {"error": str(e)}

    class VerifyAddressOnReceiptInput(BaseModel):
        """Input for verify_address_on_receipt tool."""

        image_id: str = Field(description="Image ID of the receipt")
        receipt_id: int = Field(description="Receipt ID")
        address_to_check: str = Field(
            description="The address to verify against the receipt text"
        )

    @tool(args_schema=VerifyAddressOnReceiptInput)
    def verify_address_on_receipt(
        image_id: str, receipt_id: int, address_to_check: str
    ) -> dict:
        """
        Verify that a specific address appears on the receipt text.

        This tool checks if the given address (from metadata or Google Places) actually
        appears on the receipt. This is critical for catching wrong place_id assignments
        (e.g., if metadata says "55 Fulton St, New York, NY" but receipt shows a California address).

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID
            address_to_check: The address to verify (e.g., from metadata or Google Places)

        Returns:
        - matches: Whether the address appears on the receipt (allowing for OCR errors)
        - evidence: What address text was found on the receipt (if any)
        - formatted_text: The formatted receipt text for inspection
        - recommendation: What to do if address doesn't match (use find_correct_metadata)
        """
        try:
            # Sanitize image_id first (remove trailing characters like '?')
            sanitized_image_id = image_id.rstrip("? \t\n\r")

            # Try sanitized version first, then original if different
            receipt_details = None
            for img_id in [sanitized_image_id, image_id]:
                try:
                    receipt_details = dynamo_client.get_receipt_details(
                        image_id=img_id, receipt_id=receipt_id
                    )
                    if receipt_details and receipt_details.receipt:
                        break
                except Exception as e:
                    if (
                        img_id == sanitized_image_id
                        and sanitized_image_id != image_id
                    ):
                        logger.debug(
                            f"get_receipt_details failed for sanitized {img_id}#{receipt_id}, "
                            f"trying original: {e}"
                        )
                    continue

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    f"Primary get_receipt_details failed for {image_id}#{receipt_id}, "
                    f"trying alternative methods..."
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, sanitized_image_id, receipt_id
                )

            if not receipt_details or not receipt_details.receipt:
                logger.warning(
                    f"Receipt details not found for {image_id}#{receipt_id} after fallback. "
                    f"Metadata exists but receipt lines/words are missing from DynamoDB. "
                    f"Skipping address verification for this receipt."
                )
                return {
                    "error": f"Receipt details not found for {image_id}#{receipt_id}",
                    "found": False,
                    "matches": False,
                    "evidence": "Receipt details (lines/words) not available in DynamoDB",
                    "recommendation": (
                        "Cannot verify address - receipt details missing. "
                        "This receipt has metadata but no OCR text. "
                        "Proceed with harmonization using metadata only."
                    ),
                }

            # Handle case where we might have lines/words but no receipt entity
            lines = receipt_details.lines or [] if receipt_details else []

            # If we don't have lines from receipt_details, try direct fetch
            if not lines and receipt_details:
                # Already tried fallback, but lines might be empty
                pass
            elif not lines:
                # Try direct fetch as last resort
                try:
                    lines = dynamo_client.list_receipt_lines_from_receipt(
                        image_id, receipt_id
                    )
                    if lines:
                        logger.info(
                            f"Fetched {len(lines)} lines directly for {image_id}#{receipt_id}"
                        )
                except Exception as e:
                    logger.debug(f"Could not fetch lines directly: {e}")

            if not lines:
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "address_to_check": address_to_check,
                    "matches": False,
                    "evidence": "No text lines found on receipt",
                    "formatted_text": "(No lines found)",
                    "recommendation": "Cannot verify - receipt has no text",
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

            # Extract key parts of address for matching
            address_lower = address_to_check.lower()
            # Get street number and street name
            address_parts = address_lower.split(",")[
                0
            ].strip()  # First part before comma
            # Extract city/state from address
            city_state = None
            if "," in address_to_check:
                parts = address_to_check.split(",")
                if len(parts) >= 2:
                    city_state = parts[-2].strip().lower()  # City
                    if len(parts) >= 3:
                        state_part = parts[-1].strip().lower()
                        # Extract state abbreviation or name
                        state_state = (
                            state_part.split()[0] if state_part else None
                        )
                        if state_state:
                            city_state = f"{city_state} {state_state}"

            # Check if address appears in receipt text
            receipt_text_lower = formatted_text.lower()
            matches = False
            evidence = []

            # Check for street address (number + street name)
            if address_parts:
                # Look for street number
                street_num_match = False
                street_name_match = False

                # Try to find street number (first digits)
                street_num = re.search(r"^\d+", address_parts)
                if street_num:
                    street_num_str = street_num.group()
                    if street_num_str in receipt_text_lower:
                        street_num_match = True
                        evidence.append(
                            f"Found street number '{street_num_str}' in receipt"
                        )

                # Check for street name (words after number)
                street_words = (
                    address_parts.split()[1:]
                    if street_num
                    else address_parts.split()
                )
                if street_words:
                    # Check if any street word appears
                    for word in street_words[:3]:  # First 3 words
                        if len(word) > 3 and word in receipt_text_lower:
                            street_name_match = True
                            evidence.append(
                                f"Found street name word '{word}' in receipt"
                            )
                            break

                if street_num_match and street_name_match:
                    matches = True

            # Check for city/state
            if city_state:
                city_state_words = city_state.split()
                city_state_found = any(
                    word in receipt_text_lower
                    for word in city_state_words
                    if len(word) > 2
                )
                if city_state_found:
                    evidence.append(
                        f"Found city/state '{city_state}' in receipt"
                    )
                    if not matches:
                        # If we found city/state but not street, it's a partial match
                        matches = False  # Still not a full match
                else:
                    evidence.append(
                        f"City/state '{city_state}' NOT found in receipt"
                    )

            # If we have full address match, mark as matches
            if matches:
                recommendation = "Address appears to match receipt text. Proceed with harmonization."
            else:
                recommendation = (
                    "WARNING: Address does NOT match receipt text. "
                    "This may indicate a wrong place_id assignment. "
                    "Use find_correct_metadata to find the correct place_id and metadata for this receipt."
                )

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "address_to_check": address_to_check,
                "matches": matches,
                "evidence": (
                    "; ".join(evidence)
                    if evidence
                    else "No address evidence found"
                ),
                "formatted_text": formatted_text[:500],  # Limit length
                "recommendation": recommendation,
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
                    f"Error verifying address on receipt {image_id}#{receipt_id}: {e}"
                )
            return {"error": str(e), "found": False, "matches": False}

    class DisplayReceiptTextInput(BaseModel):
        """Input for display_receipt_text tool."""

        image_id: str = Field(description="Image ID of the receipt")
        receipt_id: int = Field(description="Receipt ID")

    @tool(args_schema=DisplayReceiptTextInput)
    def display_receipt_text(image_id: str, receipt_id: int) -> dict:
        """
        Display the formatted receipt text for verification.

        This tool formats the receipt text using the same method as the combine agent,
        grouping visually contiguous lines and displaying them in image order.
        Use this to verify what text is actually on the receipt when checking metadata.

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - formatted_text: Receipt text formatted in image order (grouped by visual rows)
        - line_count: Number of lines on the receipt
        - verification_prompt: A prompt to help verify the metadata matches the receipt text
        """
        try:
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
                except Exception as e:
                    if (
                        img_id == sanitized_image_id
                        and sanitized_image_id != image_id
                    ):
                        logger.debug(
                            f"get_receipt_details failed for sanitized {img_id}#{receipt_id}, "
                            f"trying original: {e}"
                        )
                    continue

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    f"Primary get_receipt_details failed for {image_id}#{receipt_id}, "
                    f"trying alternative methods..."
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, sanitized_image_id, receipt_id
                )

            # Handle case where we might have lines/words but no receipt entity
            receipt = receipt_details.receipt if receipt_details else None
            lines = receipt_details.lines or [] if receipt_details else []

            # If we still don't have lines, try direct fetch with sanitized image_id
            if not lines:
                for img_id in [sanitized_image_id, image_id]:
                    try:
                        lines = dynamo_client.list_receipt_lines_from_receipt(
                            img_id, receipt_id
                        )
                        if lines:
                            logger.info(
                                f"Fetched {len(lines)} lines directly for {img_id}#{receipt_id} "
                                f"in display_receipt_text"
                            )
                            break
                    except Exception as e:
                        logger.debug(
                            f"Could not fetch lines for {img_id}#{receipt_id}: {e}"
                        )

            if not lines:
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "found": True,
                    "formatted_text": "(No lines found on receipt)",
                    "line_count": 0,
                    "verification_prompt": "Receipt has no text lines.",
                }

            # Get metadata from ReceiptMetadata (Receipt entity doesn't have these fields)
            current_metadata = {}
            try:
                metadata = dynamo_client.get_receipt_metadata(
                    image_id, receipt_id
                )
                if metadata:
                    current_metadata = {
                        "merchant_name": metadata.merchant_name or "(not set)",
                        "address": metadata.address or "(not set)",
                        "phone": metadata.phone_number or "(not set)",
                    }
                else:
                    current_metadata = {
                        "merchant_name": "(not available)",
                        "address": "(not available)",
                        "phone": "(not available)",
                    }
            except Exception as e:
                logger.debug(
                    f"Could not fetch metadata for {image_id}#{receipt_id}: {e}"
                )
                current_metadata = {
                    "merchant_name": "(not available)",
                    "address": "(not available)",
                    "phone": "(not available)",
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

            # Build verification prompt (metadata already set above)

            verification_prompt = f"""Please verify the metadata for this receipt matches what's actually on the receipt:

Current Metadata:
- Merchant Name: {current_metadata['merchant_name']}
- Address: {current_metadata['address']}
- Phone: {current_metadata['phone']}

Receipt Text (formatted in image order, grouped by visual rows):
{formatted_text}

Questions to verify:
1. Does the merchant name on the receipt match the metadata?
2. Does the address on the receipt match the metadata?
3. Does the phone number on the receipt match the metadata?
4. Are there any OCR errors or typos that need correction?

Use this information to make your harmonization decision."""

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "found": True,
                "formatted_text": formatted_text,
                "line_count": len(lines),
                "verification_prompt": verification_prompt,
                "current_metadata": current_metadata,
            }

        except Exception as e:
            logger.error(f"Error displaying receipt text: {e}")
            return {"error": str(e), "found": False}

    class GetFieldVariationsInput(BaseModel):
        """Input for get_field_variations tool."""

        field: str = Field(
            description="Field to analyze: merchant_name, address, or phone"
        )

    @tool(args_schema=GetFieldVariationsInput)
    def get_field_variations(field: str) -> dict:
        """
        Get detailed variations of a specific field across all receipts in the group.

        Args:
            field: One of: merchant_name, address, phone

        Returns:
        - variations: Each unique value and which receipts have it
        - analysis: Case-insensitive grouping to detect formatting differences
        """
        group = state["group"]
        if not group:
            return {"error": "No group data loaded"}

        field_map = {
            "merchant_name": "merchant_name",
            "address": "address",
            "phone": "phone",
        }

        if field not in field_map:
            return {
                "error": f"Invalid field: {field}. Use: merchant_name, address, or phone"
            }

        receipts = group.get("receipts", [])

        # Collect variations
        variations: dict[str, list[dict]] = {}
        normalized: dict[str, list[str]] = {}  # lowercase -> original values

        for r in receipts:
            value = r.get(field_map[field], "")
            if not value:
                continue

            if value not in variations:
                variations[value] = []

            variations[value].append(
                {
                    "image_id": r.get("image_id"),
                    "receipt_id": r.get("receipt_id"),
                }
            )

            # Track normalized versions
            norm = value.lower().strip()
            if norm not in normalized:
                normalized[norm] = []
            if value not in normalized[norm]:
                normalized[norm].append(value)

        # Analysis: are differences just case/formatting?
        case_groups = [
            {
                "normalized": k,
                "variants": v,
                "count": sum(len(variations.get(var, [])) for var in v),
            }
            for k, v in normalized.items()
        ]

        return {
            "field": field,
            "unique_values": len(variations),
            "variations": {
                v: {
                    "count": len(receipts),
                    "receipts": receipts[:5],  # Limit to 5 examples
                }
                for v, receipts in sorted(
                    variations.items(), key=lambda x: -len(x[1])
                )
            },
            "case_analysis": sorted(case_groups, key=lambda x: -x["count"]),
        }

    # ========== GOOGLE PLACES TOOLS ==========

    @tool
    def verify_place_id() -> dict:
        """
        Verify this place_id with Google Places API and get official data.

        This is the source of truth for merchant name, address, and phone.
        Always call this before making harmonization decisions.

        Returns:
        - valid: Whether the place_id is valid
        - place_name: Official business name from Google
        - place_address: Official formatted address
        - place_phone: Official phone number
        - is_address_like: Whether the name looks like an address (needs further investigation)
        """
        group = state["group"]
        if not group:
            return {"error": "No group data loaded"}

        place_id = group.get("place_id")
        if not place_id:
            return {"error": "No place_id in group"}

        if not places_api:
            return {
                "error": "Google Places API not configured",
                "valid": False,
            }

        # Skip invalid place_ids
        if place_id.startswith("compaction_") or place_id == "null":
            return {
                "place_id": place_id,
                "valid": False,
                "message": "Invalid place_id format",
            }

        try:
            details = places_api.get_place_details(place_id)

            if not details:
                return {
                    "place_id": place_id,
                    "valid": False,
                    "message": "Place not found in Google Places",
                }

            name = details.get("name", "")
            address = details.get("formatted_address", "")
            phone = details.get("formatted_phone_number") or details.get(
                "international_phone_number"
            )

            # Check if name looks like an address
            is_address_like = _is_address_like(name)

            return {
                "place_id": place_id,
                "valid": True,
                "place_name": name,
                "place_address": address,
                "place_phone": phone,
                "is_address_like": is_address_like,
                "warning": (
                    "Google returned an address as the name. Use find_businesses_at_address to find the actual business."
                    if is_address_like
                    else None
                ),
            }

        except Exception as e:
            logger.error(f"Error verifying place_id: {e}")
            return {"error": str(e), "valid": False}

    class FindBusinessesAtAddressInput(BaseModel):
        """Input for find_businesses_at_address tool."""

        address: str = Field(description="Address to search for businesses")

    @tool(args_schema=FindBusinessesAtAddressInput)
    def find_businesses_at_address(address: str) -> dict:
        """
        Find businesses at a specific address.

        Use this when Google Places returns an address as the merchant name
        (e.g., "123 Main St" instead of a business name).

        Args:
            address: The address to search

        Returns:
        - businesses: List of businesses found at this address
        - recommendation: Which business is most likely the correct one
        """
        if not places_api:
            return {"error": "Google Places API not configured"}

        try:
            # Geocode the address
            geocode_result = places_api.search_by_address(address)
            if not geocode_result:
                return {
                    "found": False,
                    "businesses": [],
                    "message": f"Could not geocode address: {address}",
                }

            geometry = geocode_result.get("geometry", {})
            location = geometry.get("location", {})
            lat = location.get("lat")
            lng = location.get("lng")

            if not lat or not lng:
                return {
                    "found": False,
                    "businesses": [],
                    "message": "Could not get coordinates",
                }

            # Search for nearby businesses
            nearby = places_api.search_nearby(lat=lat, lng=lng, radius=50)

            if not nearby:
                return {
                    "found": False,
                    "businesses": [],
                    "address_searched": address,
                    "message": "No businesses found at address",
                }

            # Filter out address-like names and localities
            businesses = []
            for biz in nearby[:10]:
                name = biz.get("name", "")
                types = biz.get("types", [])

                # Skip if it's just a locality or address-like name
                if any(
                    t in types
                    for t in [
                        "locality",
                        "political",
                        "administrative_area_level_1",
                    ]
                ):
                    continue
                if _is_address_like(name):
                    continue

                businesses.append(
                    {
                        "name": name,
                        "place_id": biz.get("place_id"),
                        "address": biz.get("formatted_address")
                        or biz.get("vicinity"),
                        "types": types[:5],
                    }
                )

            # Get receipt merchant names from group for matching
            group = state["group"]
            receipt_names = set()
            if group:
                for r in group.get("receipts", []):
                    if r.get("merchant_name"):
                        receipt_names.add(r.get("merchant_name").lower())

            # Find best match
            recommendation = None
            for biz in businesses:
                biz_name_lower = biz["name"].lower()
                for receipt_name in receipt_names:
                    if (
                        biz_name_lower in receipt_name
                        or receipt_name in biz_name_lower
                    ):
                        recommendation = {
                            "business": biz,
                            "reason": f"Name matches receipt merchant '{receipt_name}'",
                        }
                        break
                if recommendation:
                    break

            return {
                "found": True,
                "businesses": businesses,
                "address_searched": address,
                "count": len(businesses),
                "recommendation": recommendation,
            }

        except Exception as e:
            logger.error(f"Error finding businesses at address: {e}")
            return {"error": str(e)}

    # ========== METADATA FINDER TOOL ==========

    class FindCorrectMetadataInput(BaseModel):
        """Input for find_correct_metadata tool."""

        image_id: str = Field(
            description="Image ID of the receipt with incorrect metadata"
        )
        receipt_id: int = Field(description="Receipt ID")

    @tool(args_schema=FindCorrectMetadataInput)
    async def find_correct_metadata(image_id: str, receipt_id: int) -> dict:
        """
        Find the correct metadata for a receipt that appears to have incorrect metadata.

        This tool spins up a sub-agent (metadata finder) to:
        - Examine the receipt content (lines, words, labels)
        - Extract metadata from the receipt itself
        - Search Google Places API for the correct place_id and metadata
        - Use similarity search (if available) to find similar receipts
        - Return the correct metadata with confidence scores

        Use this when you suspect a receipt has incorrect metadata (wrong place_id, merchant_name, address, or phone).

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - found: Whether metadata was found
        - place_id: Correct Google Place ID (if found)
        - merchant_name: Correct merchant name (if found)
        - address: Correct address (if found)
        - phone_number: Correct phone number (if found)
        - confidence: Overall confidence (0-1)
        - reasoning: How the metadata was found
        - fields_found: List of fields that were found/updated
        """
        try:
            # Lazy-load ChromaDB if not already loaded and bucket is available
            chroma_client = state.get("chroma_client")
            embed_fn = state.get("embed_fn")
            chromadb_bucket = state.get("chromadb_bucket")

            if not chroma_client and chromadb_bucket:
                try:
                    logger.info(
                        "Lazy-loading ChromaDB for metadata finder sub-agent..."
                    )

                    # Download ChromaDB snapshot using receipt_chroma (atomic download)
                    from receipt_chroma.s3 import download_snapshot_atomic

                    # Download both lines and words collections (metadata finder uses both)
                    chroma_path = os.environ.get(
                        "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY",
                        "/tmp/chromadb",
                    )

                    # Check if already cached
                    chroma_db_file = os.path.join(
                        chroma_path, "chroma.sqlite3"
                    )
                    if not os.path.exists(chroma_db_file):
                        # Download lines collection first
                        logger.info(
                            f"Downloading ChromaDB lines snapshot from s3://{chromadb_bucket}/lines/"
                        )
                        lines_result = download_snapshot_atomic(
                            bucket=chromadb_bucket,
                            collection="lines",
                            local_path=chroma_path,
                            verify_integrity=False,  # Skip integrity check for faster startup
                        )

                        if lines_result.get("status") != "downloaded":
                            raise Exception(
                                f"Failed to download ChromaDB lines snapshot: {lines_result.get('error')}"
                            )

                        logger.info(
                            f"ChromaDB lines snapshot downloaded: version={lines_result.get('version_id')}"
                        )

                        # Download words collection (merges into same ChromaDB instance)
                        logger.info(
                            f"Downloading ChromaDB words snapshot from s3://{chromadb_bucket}/words/"
                        )
                        words_result = download_snapshot_atomic(
                            bucket=chromadb_bucket,
                            collection="words",
                            local_path=chroma_path,
                            verify_integrity=False,  # Skip integrity check for faster startup
                        )

                        if words_result.get("status") != "downloaded":
                            raise Exception(
                                f"Failed to download ChromaDB words snapshot: {words_result.get('error')}"
                            )

                        logger.info(
                            f"ChromaDB words snapshot downloaded: version={words_result.get('version_id')}"
                        )
                    else:
                        logger.info(
                            f"ChromaDB already cached at {chroma_path}"
                        )

                    # Update environment for receipt_agent to find ChromaDB
                    os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = (
                        chroma_path
                    )

                    # Create clients using the receipt_agent factory
                    from receipt_agent.clients.factory import (
                        create_chroma_client,
                        create_embed_fn,
                    )
                    from receipt_agent.config.settings import get_settings

                    settings = get_settings()

                    # Verify OpenAI API key is available for embeddings
                    if not settings.openai_api_key:
                        logger.warning(
                            "RECEIPT_AGENT_OPENAI_API_KEY not set - embeddings may fail"
                        )
                    else:
                        logger.info("OpenAI API key available for embeddings")

                    chroma_client = create_chroma_client(settings=settings)
                    embed_fn = create_embed_fn(settings=settings)

                    # Cache in state for subsequent calls
                    state["chroma_client"] = chroma_client
                    state["embed_fn"] = embed_fn

                    logger.info(
                        "ChromaDB and embeddings loaded and cached for metadata finder sub-agent"
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not lazy-load ChromaDB (metadata finder will use fallback): {e}"
                    )
                    # Continue without ChromaDB - metadata finder will use Google Places fallback
                    chroma_client = None
                    embed_fn = None

            # Check if we have the required dependencies for full metadata finder
            if chroma_client and embed_fn:
                # Use full metadata finder agent
                try:
                    from receipt_agent.graph.receipt_metadata_finder_workflow import (
                        create_receipt_metadata_finder_graph,
                        run_receipt_metadata_finder,
                    )

                    # Create graph if not already created (cache it in state)
                    if "metadata_finder_graph" not in state:
                        (
                            state["metadata_finder_graph"],
                            state["metadata_finder_state_holder"],
                        ) = create_receipt_metadata_finder_graph(
                            dynamo_client=dynamo_client,
                            chroma_client=chroma_client,
                            embed_fn=embed_fn,
                            places_api=places_api,
                            settings=None,
                        )

                    # Get receipt details to pass to metadata finder
                    receipt_details = dynamo_client.get_receipt_details(
                        image_id, receipt_id
                    )

                    # Run metadata finder agent
                    result = await run_receipt_metadata_finder(
                        graph=state["metadata_finder_graph"],
                        state_holder=state["metadata_finder_state_holder"],
                        image_id=image_id,
                        receipt_id=receipt_id,
                        receipt_lines=(
                            receipt_details.lines if receipt_details else None
                        ),
                        receipt_words=(
                            receipt_details.words if receipt_details else None
                        ),
                    )

                    if result.get("found"):
                        logger.info(
                            f"Metadata finder found {len(result.get('fields_found', []))} fields "
                            f"for {image_id}#{receipt_id}"
                        )
                        return {
                            "found": True,
                            "place_id": result.get("place_id"),
                            "merchant_name": result.get("merchant_name"),
                            "address": result.get("address"),
                            "phone_number": result.get("phone_number"),
                            "confidence": result.get("confidence", 0.0),
                            "reasoning": result.get("reasoning", ""),
                            "fields_found": result.get("fields_found", []),
                            "method": "metadata_finder_agent",
                        }
                    else:
                        return {
                            "found": False,
                            "reasoning": result.get(
                                "reasoning",
                                "Metadata finder could not find correct metadata",
                            ),
                            "method": "metadata_finder_agent",
                        }

                except Exception as e:
                    logger.warning(
                        f"Metadata finder agent failed: {e}, trying fallback"
                    )
                    # Fall through to fallback

            # Fallback: Use Google Places search directly
            if not places_api:
                return {
                    "found": False,
                    "error": "Google Places API not available for metadata search",
                    "method": "fallback",
                }

            # Get receipt details
            receipt_details = dynamo_client.get_receipt_details(
                image_id, receipt_id
            )
            if not receipt_details or not receipt_details.receipt:
                return {
                    "found": False,
                    "error": f"Receipt {image_id}#{receipt_id} not found",
                    "method": "fallback",
                }

            receipt = receipt_details.receipt

            # Try to find correct metadata using Google Places
            # Search by phone first (most reliable)
            place_data = None
            search_method = None

            if receipt.phone_number:
                try:
                    place_data = places_api.search_by_phone(
                        receipt.phone_number
                    )
                    if place_data:
                        search_method = "phone"
                except Exception:
                    pass

            # Try address if phone didn't work
            if not place_data and receipt.address:
                try:
                    place_data = places_api.search_by_address(receipt.address)
                    if place_data:
                        search_method = "address"
                except Exception:
                    pass

            # Try merchant name text search as last resort
            if not place_data and receipt.merchant_name:
                try:
                    place_data = places_api.search_by_text(
                        receipt.merchant_name
                    )
                    if place_data:
                        search_method = "merchant_name"
                except Exception:
                    pass

            if place_data:
                # Get full place details
                found_place_id = place_data.get("place_id")
                if found_place_id:
                    place_details = places_api.get_place_details(
                        found_place_id
                    )
                    if place_details:
                        return {
                            "found": True,
                            "place_id": found_place_id,
                            "merchant_name": place_details.get("name"),
                            "address": place_details.get("formatted_address"),
                            "phone_number": place_details.get(
                                "formatted_phone_number"
                            )
                            or place_details.get("international_phone_number"),
                            "confidence": 0.7,  # Lower confidence for fallback method
                            "reasoning": f"Found via Google Places {search_method} search (fallback method - full metadata finder not available)",
                            "fields_found": [
                                "place_id",
                                "merchant_name",
                                "address",
                                "phone_number",
                            ],
                            "method": "google_places_fallback",
                        }

            return {
                "found": False,
                "reasoning": "Could not find correct metadata using Google Places search",
                "method": "google_places_fallback",
            }

        except Exception as e:
            logger.error(f"Error finding correct metadata: {e}")
            return {
                "found": False,
                "error": str(e),
                "method": "unknown",
            }

    # ========== TEXT CONSISTENCY VERIFICATION TOOL ==========

    class VerifyTextConsistencyInput(BaseModel):
        """Input for verify_text_consistency tool."""

        canonical_merchant_name: str = Field(
            description="The canonical merchant name you plan to use"
        )
        canonical_address: Optional[str] = Field(
            default=None,
            description="The canonical address you plan to use (or None)",
        )
        canonical_phone: Optional[str] = Field(
            default=None,
            description="The canonical phone you plan to use (or None)",
        )

    @tool(args_schema=VerifyTextConsistencyInput)
    async def verify_text_consistency(
        canonical_merchant_name: str,
        canonical_address: Optional[str],
        canonical_phone: Optional[str],
    ) -> dict:
        """
        Verify text consistency for all receipts in this group using CoVe (Consistency Verification).

        This tool spins up a sub-agent that checks each receipt's text against the canonical
        metadata to identify outliers (receipts that may be from a different place).

        Use this BEFORE submitting your harmonization decision to ensure all receipts
        actually belong to the same place.

        Args:
            canonical_merchant_name: The canonical merchant name you plan to use
            canonical_address: The canonical address you plan to use (or None)
            canonical_phone: The canonical phone you plan to use (or None)

        Returns:
        - status: success, incomplete, or error
        - result: Consistency check results with per-receipt verdicts
        - outliers: List of receipts marked as MISMATCH or UNSURE
        - outlier_count: Number of outliers found
        """
        try:
            group = state["group"]
            if not group:
                return {"error": "No group data loaded"}

            place_id = group.get("place_id")
            receipts = group.get("receipts", [])

            if not place_id:
                return {"error": "No place_id in group data"}

            if not receipts:
                return {"error": "No receipts in group"}

            # Import CoVe workflow
            from receipt_agent.graph.cove_text_consistency_workflow import (
                create_cove_text_consistency_graph,
                run_cove_text_consistency,
            )

            # Create graph if not already cached
            if "cove_graph" not in state:
                (
                    state["cove_graph"],
                    state["cove_state_holder"],
                ) = create_cove_text_consistency_graph(
                    dynamo_client=dynamo_client,
                    place_id=place_id,
                    canonical_merchant_name=canonical_merchant_name,
                    canonical_address=canonical_address,
                    canonical_phone=canonical_phone,
                    receipts=[
                        {
                            "image_id": r.get("image_id"),
                            "receipt_id": r.get("receipt_id"),
                        }
                        for r in receipts
                    ],
                    settings=None,
                )

            # Run CoVe check
            result = await run_cove_text_consistency(
                graph=state["cove_graph"],
                state_holder=state["cove_state_holder"],
                place_id=place_id,
                canonical_merchant_name=canonical_merchant_name,
                canonical_address=canonical_address,
                canonical_phone=canonical_phone,
                receipts=[
                    {
                        "image_id": r.get("image_id"),
                        "receipt_id": r.get("receipt_id"),
                    }
                    for r in receipts
                ],
            )

            # Store result in state for potential use in harmonization decision
            state["cove_result"] = result.get("result")

            if result.get("status") == "success":
                cove_result = result.get("result", {})
                outlier_count = cove_result.get("outlier_count", 0)
                logger.info(
                    f"CoVe check complete: {outlier_count}/{len(receipts)} outliers found"
                )
                return {
                    "status": "success",
                    "message": f"Text consistency check complete. {outlier_count} outliers found.",
                    "result": cove_result,
                    "outliers": cove_result.get("outliers", []),
                    "outlier_count": outlier_count,
                    "receipt_results": cove_result.get("receipt_results", []),
                }
            else:
                return {
                    "status": result.get("status", "error"),
                    "error": result.get("error", "Unknown error"),
                    "message": "CoVe check did not complete successfully",
                }

        except Exception as e:
            logger.error(f"Error verifying text consistency: {e}")
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to run CoVe text consistency check",
            }

    # ========== DECISION TOOL ==========

    class SubmitHarmonizationInput(BaseModel):
        """Input for submit_harmonization tool."""

        canonical_merchant_name: str = Field(
            description="The correct merchant name for this place_id"
        )
        canonical_address: Optional[str] = Field(
            default=None,
            description="The correct address (or None if unknown)",
        )
        canonical_phone: Optional[str] = Field(
            default=None, description="The correct phone (or None if unknown)"
        )
        confidence: float = Field(
            ge=0.0, le=1.0, description="Confidence in this decision (0-1)"
        )
        reasoning: str = Field(
            description="Explanation of how you determined the canonical values"
        )
        source: str = Field(
            description="Source of truth: 'google_places', 'receipt_consensus', 'manual_selection'"
        )

    @tool(args_schema=SubmitHarmonizationInput)
    def submit_harmonization(
        canonical_merchant_name: str,
        canonical_address: Optional[str],
        canonical_phone: Optional[str],
        confidence: float,
        reasoning: str,
        source: str,
    ) -> dict:
        """
        Submit your harmonization decision for this place_id group.

        This determines the canonical values that all receipts in the group should have.

        Args:
            canonical_merchant_name: The correct merchant name (REQUIRED)
            canonical_address: The correct address (optional)
            canonical_phone: The correct phone (optional)
            confidence: How confident you are (0.0-1.0)
            reasoning: Why you chose these values
            source: Where the values came from ('google_places', 'receipt_consensus', 'manual_selection')
        """
        group = state["group"]
        if not group:
            return {"error": "No group data loaded"}

        # Determine which receipts need updates
        receipts = group.get("receipts", [])
        updates_needed = []

        for r in receipts:
            changes = []
            if r.get("merchant_name") != canonical_merchant_name:
                changes.append(
                    f"merchant_name: '{r.get('merchant_name')}' → '{canonical_merchant_name}'"
                )
            if canonical_address and r.get("address") != canonical_address:
                changes.append(
                    f"address: '{r.get('address')}' → '{canonical_address}'"
                )
            if canonical_phone and r.get("phone") != canonical_phone:
                changes.append(
                    f"phone: '{r.get('phone')}' → '{canonical_phone}'"
                )

            if changes:
                updates_needed.append(
                    {
                        "image_id": r.get("image_id"),
                        "receipt_id": r.get("receipt_id"),
                        "changes": changes,
                    }
                )

        result = {
            "place_id": group.get("place_id"),
            "canonical_merchant_name": canonical_merchant_name,
            "canonical_address": canonical_address,
            "canonical_phone": canonical_phone,
            "confidence": confidence,
            "reasoning": reasoning,
            "source": source,
            "total_receipts": len(receipts),
            "receipts_needing_update": len(updates_needed),
            "updates": updates_needed,
        }

        # Include CoVe results if available
        if "cove_result" in state and state["cove_result"]:
            result["cove_text_consistency"] = state["cove_result"]
            outlier_count = state["cove_result"].get("outlier_count", 0)
            if outlier_count > 0:
                logger.warning(
                    f"Harmonization submitted with {outlier_count} outliers identified by CoVe"
                )

        state["result"] = result

        logger.info(
            f"Harmonization submitted: {canonical_merchant_name} "
            f"({len(updates_needed)}/{len(receipts)} need updates, confidence={confidence:.2%})"
        )

        return {
            "success": True,
            "result": result,
            "message": f"Harmonization decision recorded. {len(updates_needed)} receipts will be updated.",
        }

    # Return tools
    tools = [
        get_group_summary,
        get_receipt_content,
        display_receipt_text,
        verify_address_on_receipt,
        get_field_variations,
        verify_place_id,
        find_correct_metadata,
        verify_text_consistency,
        submit_harmonization,
    ]

    if places_api:
        tools.append(find_businesses_at_address)

    return tools, state


def _is_address_like(name: Optional[str]) -> bool:
    """Check if a name looks like an address rather than a business name."""
    if not name:
        return False

    name_lower = name.lower().strip()

    # Check if it starts with a number
    if name_lower and name_lower[0].isdigit():
        street_indicators = [
            "st",
            "street",
            "ave",
            "avenue",
            "blvd",
            "boulevard",
            "rd",
            "road",
            "dr",
            "drive",
            "ln",
            "lane",
            "way",
            "ct",
            "court",
            "pl",
            "place",
            "cir",
            "circle",
        ]
        if any(indicator in name_lower for indicator in street_indicators):
            return True

    return False


# ==============================================================================
# Workflow Builder
# ==============================================================================


def create_harmonizer_graph(
    dynamo_client: Any,
    places_api: Optional[Any] = None,
    settings: Optional[Settings] = None,
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[Any, dict]:
    """
    Create the harmonizer agent workflow.

    Args:
        dynamo_client: DynamoDB client
        places_api: Google Places API client
        settings: Optional settings
        chroma_client: Optional ChromaDB client (for metadata finder sub-agent)
                      If None, will be lazy-loaded when find_correct_metadata is called
        embed_fn: Optional embedding function (for metadata finder sub-agent)
                  If None, will be lazy-loaded when find_correct_metadata is called
        chromadb_bucket: Optional S3 bucket name for ChromaDB snapshots (for lazy loading)

    Returns:
        (compiled_graph, state_holder)
    """
    if settings is None:
        settings = get_settings()

    # Create tools (pass chromadb_bucket for lazy loading)
    tools, state_holder = create_harmonizer_tools(
        dynamo_client=dynamo_client,
        places_api=places_api,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        chromadb_bucket=chromadb_bucket,
    )

    # Create LLM with tools
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

    # Define agent node with retry logic
    def agent_node(state: HarmonizerAgentState) -> dict:
        """Call the LLM to decide next action with retry logic for transient errors."""
        messages = state.messages

        # Retry logic following Ollama/LangGraph best practices
        max_retries = 3
        base_delay = 2.0  # Base delay in seconds (exponential backoff)
        last_error = None

        for attempt in range(max_retries):
            try:
                response = llm.invoke(messages)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    logger.debug(
                        f"Agent tool calls: {[tc['name'] for tc in response.tool_calls]}"
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
                )

                if is_rate_limit:
                    logger.warning(
                        f"Rate limit detected in harmonizer agent (attempt {attempt + 1}): {error_str[:200]}. "
                        f"Failing immediately to trigger circuit breaker."
                    )
                    # Re-raise rate limit errors immediately (don't retry)
                    raise RuntimeError(
                        f"Rate limit error in harmonizer agent: {error_str}"
                    ) from e

                # For other retryable errors, use exponential backoff
                # 500/502/503 are server errors that may be transient
                is_retryable = (
                    "500" in error_str
                    or "502" in error_str
                    or "503" in error_str
                    or "Internal Server Error" in error_str
                    or "internal server error" in error_str.lower()
                    or "service unavailable" in error_str.lower()
                    or "timeout" in error_str.lower()
                    or "timed out" in error_str.lower()
                )

                if is_retryable and attempt < max_retries - 1:
                    # Exponential backoff with jitter to prevent thundering herd
                    # attempt 0: 2-4s, attempt 1: 4-8s, attempt 2: 8-16s
                    jitter = random.uniform(0, base_delay)
                    wait_time = (base_delay * (2**attempt)) + jitter
                    logger.warning(
                        f"Ollama retryable error in harmonizer agent "
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
                        logger.error(
                            f"Ollama LLM call failed after {max_retries} attempts in harmonizer agent: {error_str}"
                        )
                    raise RuntimeError(
                        f"Failed to get LLM response in harmonizer agent: {error_str}"
                    ) from e

        # Should never reach here, but just in case
        raise RuntimeError(
            f"Unexpected error: Failed to get LLM response in harmonizer agent"
        ) from last_error

    # Tool node
    tool_node = ToolNode(tools)

    # Routing function
    def should_continue(state: HarmonizerAgentState) -> str:
        """Check if we should continue or end."""
        # Check if decision was submitted
        if state_holder.get("result") is not None:
            return "end"

        # Check last message for tool calls
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage):
                if last_message.tool_calls:
                    return "tools"

        return "end"

    # Build graph
    workflow = StateGraph(HarmonizerAgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("agent")

    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "end": END},
    )
    workflow.add_edge("tools", "agent")

    compiled = workflow.compile()

    return compiled, state_holder


# ==============================================================================
# Runner
# ==============================================================================


async def run_harmonizer_agent(
    graph: Any,
    state_holder: dict,
    place_id: str,
    receipts: list[dict],
    places_api: Optional[Any] = None,
) -> dict:
    """
    Run the harmonizer agent for a place_id group.

    Args:
        graph: Compiled workflow graph
        state_holder: State holder dict
        place_id: Google Place ID
        receipts: List of receipt dicts with metadata
        places_api: Optional Google Places API client to fetch source of truth data

    Returns:
        Harmonization result dict
    """
    # Set up context
    state_holder["group"] = {
        "place_id": place_id,
        "receipts": receipts,
    }
    state_holder["result"] = None

    # Fetch Google Places data to include in prompt (source of truth)
    google_places_info = None
    if places_api:
        try:
            # Skip invalid place_ids
            if not (place_id.startswith("compaction_") or place_id == "null"):
                place_details = places_api.get_place_details(place_id)
                if place_details:
                    google_places_info = {
                        "name": place_details.get("name", ""),
                        "formatted_address": place_details.get(
                            "formatted_address", ""
                        ),
                        "formatted_phone_number": place_details.get(
                            "formatted_phone_number"
                        )
                        or place_details.get("international_phone_number", ""),
                        "website": place_details.get("website", ""),
                        "rating": place_details.get("rating"),
                        "user_ratings_total": place_details.get(
                            "user_ratings_total"
                        ),
                        "types": place_details.get("types", [])[
                            :5
                        ],  # First 5 types
                        "business_status": place_details.get(
                            "business_status", ""
                        ),
                    }
                    logger.info(
                        f"Fetched Google Places data for {place_id}: {google_places_info.get('name')}"
                    )
        except Exception as e:
            logger.warning(
                f"Could not fetch Google Places data for {place_id}: {e}"
            )

    # Build initial prompt with Google Places data
    prompt_parts = [
        f"Please harmonize the metadata for place_id '{place_id}' which has {len(receipts)} receipts."
    ]

    if google_places_info:
        prompt_parts.append("\n## Google Places API Data (Source of Truth)")
        prompt_parts.append(f"**Place ID:** {place_id}")
        if google_places_info.get("name"):
            prompt_parts.append(
                f"**Official Name:** {google_places_info['name']}"
            )
        if google_places_info.get("formatted_address"):
            prompt_parts.append(
                f"**Official Address:** {google_places_info['formatted_address']}"
            )
        if google_places_info.get("formatted_phone_number"):
            prompt_parts.append(
                f"**Official Phone:** {google_places_info['formatted_phone_number']}"
            )
        if google_places_info.get("website"):
            prompt_parts.append(
                f"**Website:** {google_places_info['website']}"
            )
        if google_places_info.get("rating") is not None:
            prompt_parts.append(
                f"**Rating:** {google_places_info['rating']} ({google_places_info.get('user_ratings_total', 0)} reviews)"
            )
        if google_places_info.get("types"):
            prompt_parts.append(
                f"**Types:** {', '.join(google_places_info['types'])}"
            )
        if google_places_info.get("business_status"):
            prompt_parts.append(
                f"**Business Status:** {google_places_info['business_status']}"
            )
        prompt_parts.append(
            "\nUse this Google Places data as the source of truth when determining canonical values."
        )
    else:
        prompt_parts.append(
            "\nNote: Google Places data is not available. Use the verify_place_id tool to fetch it."
        )

    prompt_parts.append(
        "\nStart by getting the group summary, then proceed with harmonization."
    )

    # Create initial state
    initial_state = HarmonizerAgentState(
        place_id=place_id,
        receipts=receipts,
        messages=[
            SystemMessage(content=HARMONIZER_PROMPT),
            HumanMessage(content="\n".join(prompt_parts)),
        ],
    )

    logger.info(
        f"Starting harmonizer agent for place_id {place_id} ({len(receipts)} receipts)"
    )

    try:
        config = {
            "recursion_limit": 50,
            "configurable": {"thread_id": place_id},
        }

        # Add LangSmith metadata if tracing is enabled
        if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
            config["metadata"] = {
                "place_id": place_id,
                "receipt_count": len(receipts),
                "workflow": "harmonizer_v3",
            }

        await graph.ainvoke(initial_state, config=config)

        # Get result
        result = state_holder.get("result")

        if result:
            logger.info(
                f"Harmonization complete: {result['canonical_merchant_name']} "
                f"({result['receipts_needing_update']}/{result['total_receipts']} need updates)"
            )
            return result
        else:
            logger.warning(
                f"Agent ended without submitting harmonization for {place_id}"
            )
            return {
                "place_id": place_id,
                "error": "Agent did not submit harmonization decision",
                "total_receipts": len(receipts),
                "receipts_needing_update": 0,
            }

    except Exception as e:
        logger.error(f"Error in harmonizer agent: {e}")
        return {
            "place_id": place_id,
            "error": str(e),
            "total_receipts": len(receipts),
            "receipts_needing_update": 0,
        }

"""
Agentic workflow for harmonizing receipt metadata within a place_id group.

This workflow uses an LLM agent to reason about receipts that share the same
place_id and determine the correct canonical metadata for the group.

Key Insight
-----------
Receipts with the same place_id MUST have consistent metadata.
Any inconsistency indicates a data quality issue. The agent:
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
import re
from typing import TYPE_CHECKING, Annotated, Any, Callable, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from receipt_agent.agents.harmonizer.state import HarmonizerAgentState
from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.utils.address_validation import (
    is_address_like,
    is_clean_address,
)
from receipt_agent.utils.agent_common import (
    create_agent_node_with_retry,
    create_ollama_llm,
)
from receipt_agent.utils.chroma_helpers import load_dual_chroma_from_s3
from receipt_agent.utils.receipt_fetching import (
    fetch_receipt_details_with_fallback,
)
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


class HarmonizerStateDict(TypedDict):
    """Type definition for the state dictionary used in harmonizer tools."""

    group: dict[str, Any]
    result: Optional[dict[str, Any]]
    chromadb_bucket: Optional[str]
    chroma_client: Optional[Any]
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]]
    metadata_finder_graph: Any
    metadata_finder_state_holder: Any
    cove_graph: Any
    cove_state_holder: Any


# =============================================================================
# System Prompt
# =============================================================================

HARMONIZER_PROMPT = """You are a receipt metadata harmonizer.
Your job is to ensure all receipts sharing the same Google Place ID have
consistent, correct metadata.

## Your Task

You're given a group of receipts that all share the same `place_id`. These
receipts SHOULD have identical metadata, but they may differ due to:
- OCR errors (typos in merchant name, address, phone)
- Different formatting (e.g., "Vons" vs "VONS" vs "vons")
- Missing data on some receipts
- Wrong place_id assignment (rare)

Your job is to:
1. Examine all receipts in the group
2. Validate against Google Places API (source of truth)
3. Determine the correct canonical values for:
   merchant_name, address, phone
4. Identify which receipts need updates
5. Submit your harmonization decision

## Available Tools

### Group Analysis Tools
- `get_group_summary`: See all receipts in this group with their current
  metadata
- `get_receipt_content`: View the actual content (lines, words) of a specific
  receipt
- `display_receipt_text`: Display formatted receipt text
  (receipt-space grouping) for verification
- `get_field_variations`: See all variations of a field
  (merchant_name, address, phone) across the group

### Google Places Tools (Source of Truth)
- `verify_place_id`: Get official data from Google Places for this place_id
- `find_businesses_at_address`: If Google returns an address as a name, find
  actual businesses there

### Metadata Correction Tools
- `find_correct_metadata`: Spin up a sub-agent to find the correct metadata for
  a receipt whose metadata looks incorrect. Use this when a receipt's metadata
  doesn't match Google Places or other receipts in the group.

### Address Verification Tool
- `verify_address_on_receipt`: Verify that a specific address
  (from metadata or Google Places) actually appears on the receipt text. This
  is CRITICAL for catching wrong place_id assignments. If the address doesn't
  match, use `find_correct_metadata` to fix it.

### Text Consistency Verification Tool
- `verify_text_consistency`: Verify that all receipts in the group actually
  contain text consistent with the canonical metadata. This uses CoVe
  (Consistency Verification) to check receipt text and identify outliers. Use
  this BEFORE submitting to ensure all receipts belong to the same place.

### Decision Tool (REQUIRED at the end)
- `submit_harmonization`: Submit your decision for canonical values and which
  receipts need updates

## Strategy

1. **Start** with `get_group_summary` to see all receipts and their metadata
   variations

2. **Check Google Places** with `verify_place_id` to get the official data
   - This is the source of truth for merchant_name, address, phone
   - If Google returns an address as the merchant name (e.g., "123 Main St"),
     use `find_businesses_at_address`

3. **Analyze variations** with `get_field_variations` to understand the
   inconsistencies
   - Which values are most common?
   - Are differences just formatting (case sensitivity)?
   - Are there OCR errors?

4. **CRITICAL: Verify addresses match receipt text** - This is essential to
   catch wrong place_id assignments
   - Use `display_receipt_text` or `verify_address_on_receipt` to check each
     receipt
   - Compare the address in metadata against what's actually printed on the
     receipt
   - If an address like "55 Fulton St, New York, NY 10038, USA" appears but
     the receipt shows a California address, this is a WRONG place_id
     assignment
   - **If address doesn't match receipt text, use `find_correct_metadata` to
     fix it immediately**
   - Do NOT proceed with harmonization if addresses don't match - fix them
     first

5. **Inspect receipt content** (if needed) with `get_receipt_content` or
   `display_receipt_text`
   - First call `get_group_summary` to see all receipts with their `image_id`
     and `receipt_id`
   - Then use `display_receipt_text(image_id, receipt_id)` to see formatted
     receipt text (receipt-space grouping) with verification prompt
   - Or use `get_receipt_content(image_id, receipt_id)` to see raw lines and
     labeled words
   - Use these to verify metadata matches what's actually on the receipt

6. **Find correct metadata** (if metadata appears incorrect) with
   `find_correct_metadata`
   - **USE THIS if address doesn't match receipt text** - this indicates wrong
     place_id
   - If a receipt's metadata doesn't match Google Places or seems wrong, use
     this tool
   - It spins up a sub-agent to find the correct place_id, merchant_name,
     address, and phone
   - The sub-agent examines receipt content, searches Google Places, and uses
     similarity search
   - Returns the correct metadata with confidence scores

7. **Verify text consistency** (RECOMMENDED before submitting) with
   `verify_text_consistency`
   - After determining canonical metadata, use this tool to verify all receipts
     actually belong to the same place
   - The CoVe sub-agent checks each receipt's text against canonical metadata
   - Identifies outliers (receipts that may be from a different place)
   - Use the results to adjust your harmonization decision if outliers are
     found

8. **Submit your decision** with `submit_harmonization`:
   - Canonical values from Google Places (preferred) or best-quality receipt
     data
   - List of receipts that need updates
   - Confidence in your decision

## Decision Guidelines

### Merchant Name
- **Prefer Google Places name** (official, correct spelling)
- If Google returns an address, find the actual business name
- Use proper case (Title Case preferred over ALL CAPS)
- **CRITICAL: NEVER use an address as a merchant name**
  - Addresses contain street numbers, street names, city, state, ZIP codes
  - Examples of addresses (NOT merchant names): "55 Fulton St", "123 Main
    Street", "101 S Westlake Blvd"
  - If you see something like "55 Fulton Market" and it looks like it could be
    an address, verify it's actually a business name
  - If Google Places returns an address as the name, use
    `find_businesses_at_address` to find the actual business
  - Merchant names are business names like "Starbucks", "Target",
    "CVS Pharmacy", "Trader Joe's"
  - If you're unsure, use `display_receipt_text` to see what's actually printed
    on the receipt

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
3. **CRITICAL: ALWAYS verify addresses match receipt text** - Use
   `display_receipt_text` or `verify_address_on_receipt` to check
4. **If address doesn't match receipt text, use `find_correct_metadata` to fix
   it** - Don't proceed with wrong place_id
5. NEVER accept an address as a merchant name
6. RECOMMENDED: Use `verify_text_consistency` before submitting to check for
   outliers
7. ALWAYS end with `submit_harmonization`
8. Be thorough but efficient

## What Gets Updated

When you submit harmonization decisions, receipts with different values
will have their:
- `merchant_name` → Canonical merchant name
- `address` → Canonical address
- `phone_number` → Canonical phone

Begin by getting the group summary, then validate with Google Places."""


# =============================================================================
# Helper Functions
# =============================================================================


# Use shared utility instead of local function
_fetch_receipt_details_fallback = fetch_receipt_details_with_fallback


# =============================================================================
# Tool Factory for Harmonizer
# =============================================================================


def create_harmonizer_tools(
    dynamo_client: "DynamoClient",
    places_api: Optional[Any] = None,
    group_data: Optional[dict[str, Any]] = None,
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[list[Any], HarmonizerStateDict]:
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

    state: HarmonizerStateDict = {
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
        group = state.get("group")
        if not group or not isinstance(group, dict):
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
        - labeled_words: Words with labels
          (MERCHANT_NAME, ADDRESS, PHONE, etc.)
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
                            "get_receipt_details failed for sanitized "
                            f"{img_id}#{receipt_id}, "
                            f"trying original: {e}"
                        )
                    continue

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    "Primary get_receipt_details failed for "
                    f"{image_id}#{receipt_id}, trying alternative methods..."
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
                # Try to fetch lines/words directly even if the receipt entity
                # is missing
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
                                "Fetched "
                                f"{len(lines)} lines and {len(words_list)} "
                                f"words directly for {img_id}#{receipt_id}"
                            )
                            break
                    except Exception as e:
                        logger.debug(
                            "Could not fetch lines/words for "
                            f"{img_id}#{receipt_id}: {e}"
                        )

            if not lines and not words_list:
                logger.warning(
                    "Receipt details not found for "
                    f"{image_id}#{receipt_id} after fallback. "
                    "Metadata exists but "
                    "receipt lines/words are missing from DynamoDB."
                )
                return {
                    "error": (
                        f"Receipt details not found for "
                        f"{image_id}#{receipt_id}"
                    ),
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

        This tool checks if the given address (from metadata or Google Places)
        actually appears on the receipt. This is critical for catching wrong
        place_id assignments (e.g., if metadata says
        "55 Fulton St, New York, NY" but receipt shows a California address).

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID
            address_to_check: The address to verify (e.g., from metadata or
                Google Places)

        Returns:
        - matches: Whether the address appears on the receipt (allowing for OCR
          errors)
        - evidence: What address text was found on the receipt (if any)
        - formatted_text: The formatted receipt text for inspection
        - recommendation: What to do if address doesn't match (use
          find_correct_metadata)
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
                            "get_receipt_details failed for sanitized "
                            f"{img_id}#{receipt_id}, "
                            f"trying original: {e}"
                        )
                    continue

            if not receipt_details or not receipt_details.receipt:
                # Try alternative methods to fetch receipt details
                logger.info(
                    "Primary get_receipt_details failed for "
                    f"{image_id}#{receipt_id}, trying alternative methods..."
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, sanitized_image_id, receipt_id
                )

            if not receipt_details or not receipt_details.receipt:
                logger.warning(
                    "Receipt details not found for "
                    f"{image_id}#{receipt_id} after fallback. "
                    "Metadata exists but "
                    "receipt lines/words are missing from DynamoDB. "
                    "Skipping address verification for this receipt."
                )
                return {
                    "error": (
                        f"Receipt details not found for "
                        f"{image_id}#{receipt_id}"
                    ),
                    "found": False,
                    "matches": False,
                    "evidence": (
                        "Receipt details (lines/words) not available in "
                        "DynamoDB"
                    ),
                    "recommendation": (
                        "Cannot verify address - receipt details missing. "
                        "This receipt has metadata but no OCR text. "
                        "Proceed with harmonization using metadata only."
                    ),
                }

            # Handle case where we might have lines/words but no receipt entity
            lines = receipt_details.lines or [] if receipt_details else []

            # If we don't have lines, try direct fetch
            if not lines:
                try:
                    lines = dynamo_client.list_receipt_lines_from_receipt(
                        image_id, receipt_id
                    )
                    if lines:
                        logger.info(
                            "Fetched "
                            f"{len(lines)} lines directly for "
                            f"{image_id}#{receipt_id}"
                        )
                except Exception as e:
                    logger.debug(
                        "Could not fetch lines directly: "
                        f"{e}"
                    )

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
                sorted_lines = sorted(lines, key=lambda line: line.line_id)
                formatted_text = "\n".join(
                    f"{line.line_id}: {line.text}" for line in sorted_lines
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
                            "Found street number "
                            f"'{street_num_str}' in receipt"
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
                        # If we found city/state but not street, it's a partial
                        # match
                        matches = False  # Still not a full match
                else:
                    evidence.append(
                        f"City/state '{city_state}' NOT found in receipt"
                    )

            # If we have full address match, mark as matches
            if matches:
                recommendation = (
                    "Address appears to match receipt text. "
                    "Proceed with harmonization."
                )
            else:
                recommendation = (
                    "WARNING: Address does NOT match receipt text. "
                    "This may indicate a wrong place_id assignment. "
                    "Use find_correct_metadata to find the correct place_id "
                    "and metadata for this receipt."
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
                    "Receipt details not available for "
                    f"{image_id}#{receipt_id}: {error_str}"
                )
            else:
                logger.error(
                    "Error verifying address on receipt "
                    f"{image_id}#{receipt_id}: {e}"
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

        This tool formats the receipt text using the same method as the combine
        agent, grouping visually contiguous lines and displaying them in image
        order. Use this to verify what text is actually on the receipt when
        checking metadata.

        Args:
            image_id: Image ID of the receipt
            receipt_id: Receipt ID

        Returns:
        - formatted_text: Receipt text formatted in image order
          (grouped by visual rows)
        - line_count: Number of lines on the receipt
        - verification_prompt: A prompt to help verify the metadata matches the
          receipt text
        """
        try:
            # Sanitize image_id first (remove trailing characters like '?')
            sanitized_image_id = image_id.rstrip("? \t\n\r")

            # Log table name for debugging
            table_name = getattr(dynamo_client, "table_name", "unknown")
            logger.debug(
                "display_receipt_text: Using table "
                f"'{table_name}' for {image_id}#{receipt_id}"
            )

            # Simplified: Use list_receipt_lines_from_receipt directly
            # This is the most direct method and uses GSI3 for efficient
            # querying
            lines = []
            for img_id in [sanitized_image_id, image_id]:
                try:
                    lines = dynamo_client.list_receipt_lines_from_receipt(
                        img_id, receipt_id
                    )
                    if lines:
                        logger.info(
                            "Fetched "
                            f"{len(lines)} lines for {img_id}#{receipt_id} "
                            "using list_receipt_lines_from_receipt()"
                        )
                        break
                except Exception as e:
                    logger.debug(
                        "list_receipt_lines_from_receipt failed for "
                        f"{img_id}#{receipt_id}: {e}"
                    )

            # Fallback: Try get_receipt_details if direct query failed
            if not lines:
                logger.debug(
                    "Direct line query failed, trying get_receipt_details() "
                    f"for {image_id}#{receipt_id}"
                )
                try:
                    receipt_details = dynamo_client.get_receipt_details(
                        sanitized_image_id, receipt_id
                    )
                    if receipt_details and receipt_details.lines:
                        lines = receipt_details.lines
                        logger.info(
                            "Fetched "
                            f"{len(lines)} lines via get_receipt_details()"
                        )
                except Exception as e:
                    logger.debug(
                        "get_receipt_details also failed: "
                        f"{e}"
                    )

            # Final fallback: Try fetch_receipt_details_with_fallback
            if not lines:
                logger.debug(
                    "Trying fetch_receipt_details_with_fallback() for "
                    f"{image_id}#{receipt_id}"
                )
                receipt_details = _fetch_receipt_details_fallback(
                    dynamo_client, sanitized_image_id, receipt_id
                )
                if receipt_details and receipt_details.lines:
                    lines = receipt_details.lines
                    logger.info(
                        "Fetched "
                        f"{len(lines)} lines via "
                        "fetch_receipt_details_with_fallback()"
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

            # Try ReceiptPlace first (new entity), fallback to ReceiptMetadata
            current_metadata = {}
            try:
                place = dynamo_client.get_receipt_place(image_id, receipt_id)
                if place:
                    current_metadata = {
                        "merchant_name": place.merchant_name or "(not set)",
                        "address": place.formatted_address or "(not set)",
                        "phone": place.phone_number or "(not set)",
                    }
            except Exception as e:
                logger.debug(
                    f"Could not fetch receipt_place for {image_id}#{receipt_id}: {e}"
                )

            # Fallback to legacy ReceiptMetadata if no place found
            if not current_metadata:
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
                        "Could not fetch metadata for "
                        f"{image_id}#{receipt_id}: {e}"
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
                sorted_lines = sorted(lines, key=lambda line: line.line_id)
                formatted_text = "\n".join(
                    f"{line.line_id}: {line.text}" for line in sorted_lines
                )

            # Build verification prompt (metadata already set above)

            verification_prompt = (
                "Please verify the metadata for this receipt matches what's "
                "actually on the receipt.\n\n"
                "Current Metadata:\n"
                f"- Merchant Name: {current_metadata['merchant_name']}\n"
                f"- Address: {current_metadata['address']}\n"
                f"- Phone: {current_metadata['phone']}\n\n"
                "Receipt Text (formatted in image order, grouped by visual "
                "rows):\n"
                f"{formatted_text}\n\n"
                "Questions to verify:\n"
                "1. Does the merchant name on the receipt match\n"
                "the metadata?\n"
                "2. Does the address on the receipt match the metadata?\n"
                "3. Does the phone number on the receipt match the metadata?\n"
                "4. Are there any OCR errors or typos that need\n"
                "correction?\n\n"
                "Use this information to make your harmonization decision."
            )

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
        Get detailed variations of a specific field across all receipts
        in the group.

        Args:
            field: One of: merchant_name, address, phone

        Returns:
        - variations: Each unique value and which receipts have it
        - analysis: Case-insensitive grouping to detect formatting differences
        """
        group = state.get("group")
        if not group or not isinstance(group, dict):
            return {"error": "No group data loaded"}

        field_map = {
            "merchant_name": "merchant_name",
            "address": "address",
            "phone": "phone",
        }

        if field not in field_map:
            return {
                "error": (
                    "Invalid field: "
                    f"{field}. Use: merchant_name, address, or phone"
                )
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
        - is_address_like: Whether the name looks like an address
          (needs further investigation)
        """
        group = state.get("group")
        if not group or not isinstance(group, dict):
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
            is_address_like_result = is_address_like(name)

            return {
                "place_id": place_id,
                "valid": True,
                "place_name": name,
                "place_address": address,
                "place_phone": phone,
                "is_address_like": is_address_like_result,
                "warning": (
                    "Google returned an address as the name. Use "
                    "find_businesses_at_address to find the actual business."
                    if is_address_like_result
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
                if is_address_like(name):
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
                            "reason": (
                                "Name matches receipt merchant "
                                f"'{receipt_name}'"
                            ),
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
        Find the correct metadata for a receipt that appears to have
        incorrect metadata.

        This tool spins up a sub-agent (metadata finder) to:
        - Examine the receipt content (lines, words, labels)
        - Extract metadata from the receipt itself
        - Search Google Places API for the correct place_id and metadata
        - Use similarity search (if available) to find similar receipts
        - Return the correct metadata with confidence scores

        Use this when you suspect a receipt has incorrect metadata
        (wrong place_id, merchant_name, address, or phone).

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

            if (
                not chroma_client
                and chromadb_bucket
                and isinstance(chromadb_bucket, str)
            ):
                try:
                    logger.info(
                        "Lazy-loading ChromaDB for metadata finder "
                        "sub-agent..."
                    )

                    # Use shared helper to load dual-chroma setup
                    chroma_client, embed_fn = load_dual_chroma_from_s3(
                        chromadb_bucket=chromadb_bucket,
                        verify_integrity=False,
                        # Skip integrity check for faster startup
                    )

                    # Cache in state for subsequent calls
                    state["chroma_client"] = chroma_client
                    state["embed_fn"] = embed_fn

                    logger.info(
                        "ChromaDB and embeddings loaded and cached for "
                        "metadata finder sub-agent"
                    )
                except Exception as e:
                    logger.warning(
                        "Could not lazy-load ChromaDB (metadata finder will "
                        f"use fallback): {e}"
                    )
                    # Continue without ChromaDB - metadata finder will use
                    # Google Places fallback
                    chroma_client = None
                    embed_fn = None

            # Check if we have the required dependencies for full metadata
            # finder
            if chroma_client and embed_fn:
                # Use full metadata finder agent
                try:
                    from receipt_agent.subagents.metadata_finder import (
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
                            chromadb_bucket=chromadb_bucket,
                            # Pass bucket for lazy loading
                        )

                    # Get receipt details to pass to metadata finder
                    receipt_details = dynamo_client.get_receipt_details(
                        image_id, receipt_id
                    )

                    # Run metadata finder agent
                    result = await run_receipt_metadata_finder(
                        graph=state["metadata_finder_graph"],
                        state_holder=state.get("metadata_finder_state_holder")
                        or {},
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
                            f"Metadata finder found "
                            f"{len(result.get('fields_found', []))} fields "
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
                                "Metadata finder could not find "
                                "correct metadata",
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
                    "error": (
                        "Google Places API not available for metadata search"
                    ),
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
                except Exception as e:
                    logger.debug(f"Phone search failed: {e}")

            # Try address if phone didn't work
            if not place_data and receipt.address:
                try:
                    place_data = places_api.search_by_address(receipt.address)
                    if place_data:
                        search_method = "address"
                except Exception as e:
                    logger.debug(f"Address search failed: {e}")

            # Try merchant name text search as last resort
            if not place_data and receipt.merchant_name:
                try:
                    place_data = places_api.search_by_text(
                        receipt.merchant_name
                    )
                    if place_data:
                        search_method = "merchant_name"
                except Exception as e:
                    logger.debug(f"Text search failed: {e}")

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
                            "confidence": 0.7,
                            # Lower confidence for fallback method
                            "reasoning": (
                                "Found via Google Places "
                                f"{search_method} search "
                                "(fallback method - full metadata "
                                "finder not available)"
                            ),
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
                            "reasoning": (
                                "Could not find correct metadata using "
                                "Google Places search"
                            ),
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
        Verify text consistency for all receipts in this group using CoVe
        (Consistency Verification).

        This tool spins up a sub-agent that checks each receipt's text against
        the canonical metadata to identify outliers (receipts that may be from
        a different place).

        Use this BEFORE submitting your harmonization decision to ensure all
        receipts actually belong to the same place.

        Args:
            canonical_merchant_name: The canonical merchant name you
                plan to use
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
            from receipt_agent.subagents.cove_text_consistency import (
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
            cove_state = state.get("cove_state_holder")
            if not cove_state or not isinstance(cove_state, dict):
                return {"error": "CoVe state not initialized"}
            result = await run_cove_text_consistency(
                graph=state.get("cove_graph"),
                state_holder=cove_state,
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
                receipt_results_count = len(
                    cove_result.get("receipt_results", [])
                )
                logger.info(
                    f"CoVe check complete: {outlier_count}/"
                    f"{receipt_results_count} outliers found "
                    f"(expected {len(receipts)} receipts)"
                )
                return {
                    "status": "success",
                    "message": (
                        "Text consistency check complete. "
                        f"{outlier_count} outliers found."
                    ),
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
            description=(
                "Explanation of how you determined the canonical values"
            )
        )
        source: str = Field(
            description=(
                "Source of truth: 'google_places', 'receipt_consensus', "
                "'manual_selection'"
            )
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

        This determines the canonical values that all receipts
        in the group should have.

        Args:
            canonical_merchant_name: The correct merchant name (REQUIRED)
            canonical_address: The correct address (optional)
            canonical_phone: The correct phone (optional)
            confidence: How confident you are (0.0-1.0)
            reasoning: Why you chose these values
            source: Where the values came from (
                'google_places', 'receipt_consensus', 'manual_selection'
            )
        """

        if canonical_address and not is_clean_address(canonical_address):
            return {
                "error": (
                    "canonical_address must be a clean address "
                    "(no reasoning/commentary). "
                    "Please provide the address only, without questions "
                    "or extra text."
                )
            }

        group = state.get("group")
        if not group or not isinstance(group, dict):
            return {"error": "No group data loaded"}

        # Determine which receipts need updates
        receipts = group.get("receipts", [])
        updates_needed = []

        for r in receipts:
            changes = []
            if r.get("merchant_name") != canonical_merchant_name:
                changes.append(
                    f"merchant_name: '{r.get('merchant_name')}' "
                    f"→ '{canonical_merchant_name}'"
                )
            if canonical_address and r.get("address") != canonical_address:
                changes.append(
                    f"address: '{r.get('address')}' "
                    f"→ '{canonical_address}'"
                )
            if canonical_phone and r.get("phone") != canonical_phone:
                changes.append(
                    f"phone: '{r.get('phone')}' "
                    f"→ '{canonical_phone}'"
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
                    "Harmonization submitted with "
                    f"{outlier_count} outliers identified by CoVe"
                )

        state["result"] = result

        logger.info(
            f"Harmonization submitted: {canonical_merchant_name} "
            f"({len(updates_needed)}/{len(receipts)} need updates, "
            f"confidence={confidence:.2%})"
        )

        return {
            "success": True,
            "result": result,
            "message": (
                "Harmonization decision recorded. "
                f"{len(updates_needed)} receipts will be updated."
            ),
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


# Use shared utility instead of local function
# _is_address_like is now imported from receipt_agent.utils.address_validation


# =============================================================================
# Workflow Builder
# =============================================================================


def create_harmonizer_graph(
    dynamo_client: "DynamoClient",
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
        chromadb_bucket: Optional S3 bucket name for ChromaDB snapshots
            (for lazy loading)

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

    # Create LLM with tools using shared utility
    llm = create_ollama_llm(settings=settings, temperature=0.0)
    llm = llm.bind_tools(tools)

    # Create agent node with retry logic using shared utility
    agent_node = create_agent_node_with_retry(
        llm=llm,
        agent_name="harmonizer",
    )

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
                # If agent responded without tool calls and no result,
                # give it another chance to call submit_decision
                return "agent"

        # If no messages or no tool calls, go back to agent
        return "agent"

    # Build graph
    workflow = StateGraph(HarmonizerAgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("agent")

    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "agent": "agent",  # Loop back to agent if no tool calls
            "end": END,
        },
    )
    workflow.add_edge("tools", "agent")

    compiled = workflow.compile()

    return compiled, state_holder


# =============================================================================
# Runner
# =============================================================================


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
        places_api: Optional Google Places API client to fetch source of truth
            data

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
                        f"Fetched Google Places data for {place_id}: "
                        f"{google_places_info.get('name')}"
                    )
        except Exception as e:
            logger.warning(
                f"Could not fetch Google Places data for {place_id}: {e}"
            )

    # Build initial prompt with Google Places data
    prompt_parts = [
        f"Please harmonize the metadata for place_id '{place_id}' "
        f"which has {len(receipts)} receipts."
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
                f"**Official Address:** "
                f"{google_places_info['formatted_address']}"
            )
        if google_places_info.get("formatted_phone_number"):
            prompt_parts.append(
                f"**Official Phone:** "
                f"{google_places_info['formatted_phone_number']}"
            )
        if google_places_info.get("website"):
            prompt_parts.append(
                f"**Website:** {google_places_info['website']}"
            )
        if google_places_info.get("rating") is not None:
            prompt_parts.append(
                f"**Rating:** {google_places_info['rating']} "
                f"({google_places_info.get('user_ratings_total', 0)} reviews)"
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
            "\nUse this Google Places data as the source of truth when "
            "determining canonical values."
        )
    else:
        prompt_parts.append(
            "\nNote: Google Places data is not available. "
            "Use the verify_place_id tool to fetch it."
        )

    prompt_parts.append(
        "\nStart by getting the group summary, then proceed with "
        "harmonization."
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
        f"Starting harmonizer agent for place_id {place_id} "
        f"({len(receipts)} receipts)"
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
                f"({result['receipts_needing_update']}/"
                f"{result['total_receipts']} need updates)"
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

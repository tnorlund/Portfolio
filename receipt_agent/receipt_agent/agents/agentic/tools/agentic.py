"""
Guardrailed agentic tools for receipt metadata validation.

These tools are designed to be used by a ReAct agent with full autonomy
while enforcing constraints on how receipt data can be queried.

Guard Rails:
- Tools construct record IDs internally (agent can't make arbitrary queries)
- Result counts are capped
- Decision tool enforces valid status values
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from receipt_agent.tools.places import _format_place_result, _place_to_dict
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space

logger = logging.getLogger(__name__)


# ==============================================================================
# Receipt Context - Injected at runtime
# ==============================================================================


@dataclass
class ReceiptContext:
    """Context for the receipt being validated. Injected into tools at runtime."""

    image_id: str
    receipt_id: int

    # Cached data (loaded once)
    lines: Optional[list[dict]] = None
    words: Optional[list[dict]] = None
    metadata: Optional[dict] = None
    line_embeddings: Optional[dict[str, list[float]]] = None
    word_embeddings: Optional[dict[str, list[float]]] = None


def _build_line_id(image_id: str, receipt_id: int, line_id: int) -> str:
    """Build the embedding record ID for a line."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


# ==============================================================================
# Tool Input Schemas
# ==============================================================================


class GetMerchantConsensusInput(BaseModel):
    """Input for get_merchant_consensus tool."""

    merchant_name: str = Field(description="Merchant name to look up")


class GetPlaceIdInfoInput(BaseModel):
    """Input for get_place_id_info tool."""

    place_id: str = Field(description="Google Place ID to look up")


class CompareWithReceiptInput(BaseModel):
    """Input for compare_with_receipt tool."""

    other_image_id: str = Field(
        description="Image ID of receipt to compare with"
    )
    other_receipt_id: int = Field(description="Receipt ID to compare with")


class SubmitDecisionInput(BaseModel):
    """Input for submit_decision tool."""

    status: Literal["VALIDATED", "INVALID", "NEEDS_REVIEW"] = Field(
        description="Validation status: VALIDATED (correct), INVALID (wrong), NEEDS_REVIEW (uncertain)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0"
    )
    reasoning: str = Field(description="Brief explanation of the decision")
    evidence: list[str] = Field(
        default_factory=list,
        description="Key findings that support the decision",
    )


class VerifyWithGooglePlacesInput(BaseModel):
    """Input for verify_with_google_places tool."""

    merchant_name: str = Field(
        description="Merchant name to verify against Google Places"
    )
    address: Optional[str] = Field(
        default=None,
        description="Optional address to narrow the search",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Optional phone number to narrow the search",
    )


class FindBusinessesAtAddressInput(BaseModel):
    """Input for find_businesses_at_address_wrapper tool."""

    address: str = Field(description="Address to search for businesses")


# ==============================================================================
# Tool Factory - Creates tools with injected dependencies
# ==============================================================================


def create_agentic_tools(
    dynamo_client: Any,
    places_api: Optional[Any] = None,
) -> tuple[list[Any], dict]:
    """
    Create guardrailed tools for the agentic validator.

    Args:
        dynamo_client: DynamoDB client
        places_api: Optional Google Places API client

    Returns:
        (tools, state_holder) - tools list and a dict to hold runtime state
    """
    # State holder - will be populated before each validation
    state = {
        "context": None,
        "decision": None,
    }

    # ========== CONTEXT TOOLS ==========

    @tool
    def get_my_lines() -> list[dict]:
        """
        Get all lines from the receipt being validated.

        Returns a list of lines with:
        - line_id: Unique ID for this line
        - text: The text content of the line
        - has_embedding: Whether this line has a stored embedding

        Use this first to understand what content is on the receipt.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return [{"error": "No receipt context set"}]

        if ctx.lines is None:
            # Load lines from DynamoDB
            try:
                receipt_details = dynamo_client.get_receipt_details(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                ctx.lines = [
                    {
                        "line_id": line.line_id,
                        "text": line.text,
                        "has_embedding": _build_line_id(
                            ctx.image_id, ctx.receipt_id, line.line_id
                        )
                        in (ctx.line_embeddings or {}),
                    }
                    for line in (receipt_details.lines or [])
                ]
            except Exception as e:
                logger.exception("Error loading lines")
                return [{"error": str(e)}]

        return ctx.lines

    @tool
    def get_my_words() -> list[dict]:
        """
        Get all labeled words from the receipt being validated.

        Returns a list of words with:
        - line_id: Line containing this word
        - word_id: Unique ID for this word within the line
        - text: The word text
        - label: The assigned label (MERCHANT_NAME, PHONE, ADDRESS, TOTAL, etc.)

        Use this to see what entities were extracted from the receipt.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return [{"error": "No receipt context set"}]

        if ctx.words is None:
            # Load words from DynamoDB
            try:
                receipt_details = dynamo_client.get_receipt_details(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                ctx.words = [
                    {
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "text": word.text,
                        "label": getattr(word, "label", None),
                    }
                    for word in (receipt_details.words or [])
                ]
            except Exception as e:
                logger.exception("Error loading words")
                return [{"error": str(e)}]

        return ctx.words

    @tool
    def get_receipt_text() -> dict:
        """
        Get formatted receipt text in receipt space (no image warp).

        Groups visually contiguous lines (centroid overlap) into rows and
        returns merged text with line breaks.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        try:
            receipt_details = dynamo_client.get_receipt_details(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )
            lines = receipt_details.lines or []
        except Exception as exc:
            logger.exception("Error loading lines for receipt text")
            return {"error": str(exc)}

        formatted = format_receipt_text_receipt_space(lines)
        return {"formatted_text": formatted, "line_count": len(lines)}

    @tool
    def get_my_place() -> dict:
        """
        Get the current place data stored for this receipt.

        Returns:
        - merchant_name: Current merchant name
        - place_id: Google Place ID
        - address: Stored address
        - phone: Stored phone number
        - validation_status: Current validation status

        This is the place data you need to validate.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        if ctx.metadata is None:
            # Try receipt_place first (new entity)
            try:
                place = dynamo_client.get_receipt_place(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                if place:
                    ctx.metadata = {
                        "merchant_name": place.merchant_name,
                        "place_id": place.place_id,
                        "address": place.formatted_address,
                        "phone": place.phone_number,
                        "validation_status": place.validation_status,
                    }
                else:
                    ctx.metadata = {
                        "error": "No place found for this receipt",
                    }
            except Exception as e:
                logger.debug("No receipt_place found: %s", e)
                ctx.metadata = {"error": "No place found for this receipt"}

        return ctx.metadata

    # ========== AGGREGATION TOOLS ==========

    @tool(args_schema=GetMerchantConsensusInput)
    def get_merchant_consensus(merchant_name: str) -> dict:
        """
        Get the canonical data for a merchant based on all receipts.

        Looks up all receipts for this merchant and returns consensus data:
        - receipt_count: Total receipts found
        - most_common_place_id: The most frequently used Place ID
        - most_common_address: Most common address
        - most_common_phone: Most common phone
        - place_id_agreement: What % of receipts agree on place_id
        - address_agreement: What % agree on address
        - phone_agreement: What % agree on phone

        Use this to understand what the "correct" metadata should be.
        """
        try:
            places, _ = dynamo_client.get_receipt_places_by_merchant(
                merchant_name=merchant_name,
                limit=100,
            )

            if not places:
                return {
                    "error": f"No receipts found for merchant '{merchant_name}'",
                    "receipt_count": 0,
                }

            # Aggregate
            place_ids: dict[str, int] = {}
            addresses: dict[str, int] = {}
            phones: dict[str, int] = {}

            for place in places:
                if place.place_id:
                    place_ids[place.place_id] = (
                        place_ids.get(place.place_id, 0) + 1
                    )
                if place.formatted_address:
                    addresses[place.formatted_address] = (
                        addresses.get(place.formatted_address, 0) + 1
                    )
                if place.phone_number:
                    phones[place.phone_number] = (
                        phones.get(place.phone_number, 0) + 1
                    )

            total = len(places)

            # Find most common
            most_common_place_id = (
                max(place_ids.items(), key=lambda x: x[1])[0]
                if place_ids
                else None
            )
            most_common_address = (
                max(addresses.items(), key=lambda x: x[1])[0]
                if addresses
                else None
            )
            most_common_phone = (
                max(phones.items(), key=lambda x: x[1])[0] if phones else None
            )

            return {
                "merchant_name": merchant_name,
                "receipt_count": total,
                "most_common_place_id": most_common_place_id,
                "most_common_address": most_common_address,
                "most_common_phone": most_common_phone,
                "place_id_agreement": (
                    round(place_ids.get(most_common_place_id, 0) / total, 3)
                    if most_common_place_id
                    else 0
                ),
                "address_agreement": (
                    round(addresses.get(most_common_address, 0) / total, 3)
                    if most_common_address
                    else 0
                ),
                "phone_agreement": (
                    round(phones.get(most_common_phone, 0) / total, 3)
                    if most_common_phone
                    else 0
                ),
                "all_place_ids": dict(
                    sorted(place_ids.items(), key=lambda x: -x[1])[:5]
                ),
                "all_addresses": dict(
                    sorted(addresses.items(), key=lambda x: -x[1])[:3]
                ),
                "all_phones": dict(
                    sorted(phones.items(), key=lambda x: -x[1])[:3]
                ),
            }

        except Exception as e:
            logger.exception("Error in get_merchant_consensus")
            return {"error": str(e)}

    @tool(args_schema=GetPlaceIdInfoInput)
    def get_place_id_info(place_id: str) -> dict:
        """
        Get information about a Google Place ID.

        Returns all receipts associated with this Place ID and their metadata.
        Use this to verify a Place ID is legitimate and consistently used.
        """
        try:
            places, _ = dynamo_client.list_receipt_places_with_place_id(
                place_id=place_id,
                limit=100,
            )

            if not places:
                return {
                    "place_id": place_id,
                    "receipt_count": 0,
                    "message": "No receipts found with this Place ID",
                }

            receipt_set = {
                (place.image_id, int(place.receipt_id)) for place in places
            }
            receipt_count = len(receipt_set)

            return {
                "place_id": place_id,
                "receipt_count": receipt_count,
                "message": f"Found {receipt_count} receipt(s) with this Place ID",
            }

        except Exception as e:
            logger.exception("Error in get_place_id_info")
            return {
                "error": f"{e!s}",
                "place_id": place_id,
                "receipt_count": 0,
                "message": "Error querying DynamoDB",
            }

    # ========== COMPARISON TOOL ==========

    @tool(args_schema=CompareWithReceiptInput)
    def compare_with_receipt(
        other_image_id: str, other_receipt_id: int
    ) -> dict:
        """
        Compare your receipt with another specific receipt.

        Returns a detailed comparison:
        - same_merchant: Do they have the same merchant name?
        - same_place_id: Same Google Place ID?
        - same_address: Same address?
        - same_phone: Same phone?
        - differences: List of specific differences

        Use this for detailed comparison with a promising match.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        try:
            # Get my receipt's place data
            my_place = dynamo_client.get_receipt_place(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )

            if not my_place:
                return {
                    "error": f"Receipt {ctx.image_id}#{ctx.receipt_id} place not found"
                }

            # Get place for the other receipt
            other_place = dynamo_client.get_receipt_place(
                image_id=other_image_id, receipt_id=other_receipt_id
            )

            if not other_place:
                return {
                    "error": f"Receipt {other_image_id}#{other_receipt_id} not found"
                }

            # Compare
            differences = []
            same_merchant = my_place.merchant_name == other_place.merchant_name
            if not same_merchant:
                differences.append(
                    f"Merchant: '{my_place.merchant_name}' vs '{other_place.merchant_name}'"
                )

            same_place_id = my_place.place_id == other_place.place_id
            if not same_place_id:
                differences.append(
                    f"Place ID: '{my_place.place_id}' vs '{other_place.place_id}'"
                )

            same_address = (
                my_place.formatted_address == other_place.formatted_address
            )
            if not same_address:
                differences.append(
                    f"Address: '{my_place.formatted_address}' vs '{other_place.formatted_address}'"
                )

            same_phone = my_place.phone_number == other_place.phone_number
            if not same_phone:
                differences.append(
                    f"Phone: '{my_place.phone_number}' vs '{other_place.phone_number}'"
                )

            return {
                "same_merchant": same_merchant,
                "same_place_id": same_place_id,
                "same_address": same_address,
                "same_phone": same_phone,
                "differences": differences,
            }

        except Exception as e:
            logger.exception("Error in compare_with_receipt")
            return {"error": f"{e!s}"}

    # ========== GOOGLE PLACES TOOLS ==========

    @tool(args_schema=VerifyWithGooglePlacesInput)
    def verify_with_google_places(
        merchant_name: str,
        address: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> dict:
        """
        Verify merchant information against Google Places API.

        Returns:
        - found: Whether a matching business was found
        - place_id: Google Place ID if found
        - confidence: How confident the match is (0.0 to 1.0)
        - message: Human-readable result

        Use this to validate merchant metadata against Google's database.
        """
        if not places_api:
            return {
                "error": "Google Places API not configured",
                "found": False,
                "place_id": None,
                "confidence": 0.0,
            }

        try:
            # PlacesClient does not expose a generic search_places API; call the
            # concrete helpers it provides (phone -> address -> text search).
            result = None

            if phone and hasattr(places_api, "search_by_phone"):
                result = places_api.search_by_phone(phone)

            if (
                not result
                and address
                and hasattr(places_api, "search_by_address")
            ):
                result = places_api.search_by_address(address)

            if not result and hasattr(places_api, "search_by_text"):
                result = places_api.search_by_text(merchant_name)

            if not result:
                return {
                    "found": False,
                    "place_id": None,
                    "confidence": 0.0,
                    "message": "No matching business found in Google Places",
                }

            # PlacesClient returns Pydantic Place models; convert to dict
            # so downstream .get() calls work.
            top = _place_to_dict(result)
            return {
                "found": True,
                "place_id": top.get("place_id"),
                "confidence": (
                    top.get("rating", 0) / 5.0 if top.get("rating") else 0.5
                ),
                "message": f"Found {top.get('name', 'business')} via Google Places",
            }

        except Exception as e:
            logger.exception("Error in verify_with_google_places")
            return {
                "error": f"{e!s}",
                "found": False,
                "place_id": None,
                "confidence": 0.0,
            }

    # ========== DECISION TOOL (terminates agent loop) ==========

    @tool(args_schema=SubmitDecisionInput)
    def submit_decision(
        status: Literal["VALIDATED", "INVALID", "NEEDS_REVIEW"],
        reasoning: str,
        confidence: float = 0.0,
        evidence: list[str] | None = None,
    ) -> dict:
        """
        Submit your final decision about this receipt's metadata.

        This ends the validation workflow. Call this when you've made a decision.

        Args:
            status: VALIDATED (metadata is correct), INVALID (metadata is wrong),
                   or NEEDS_REVIEW (uncertain, needs human review)
            reasoning: Your explanation for this decision
            confidence: How confident you are (0.0 to 1.0)
            evidence: Key findings that support the decision

        Returns:
            Confirmation of your decision
        """
        if evidence is None:
            evidence = []
        state["decision"] = {
            "status": status,
            "reasoning": reasoning,
            "confidence": confidence,
            "evidence": evidence,
        }
        return {
            "status": "submitted",
            "message": f"Decision submitted: {status}",
        }

    # Add Google Places tools if API is available
    tools = [
        get_my_lines,
        get_my_words,
        get_receipt_text,
        get_my_place,
        get_merchant_consensus,
        get_place_id_info,
        compare_with_receipt,
        verify_with_google_places,
        submit_decision,
    ]

    if places_api:
        # Add find_businesses_at_address tool
        @tool(args_schema=FindBusinessesAtAddressInput)
        def find_businesses_at_address_wrapper(address: str) -> dict:
            """
            Find businesses at a specific address using Google Places.

            Returns businesses found at that address with their place_ids.
            Use this when you have an address but need to find the business.
            """
            try:
                # Geocode address to get coordinates
                geocode_result = _place_to_dict(
                    places_api.search_by_address(address)
                )
                if not geocode_result:
                    return {
                        "error": f"Could not geocode address: {address}",
                        "address": address,
                        "count": 0,
                    }

                geometry = geocode_result.get("geometry") or {}
                location = geometry.get("location") or {}
                lat = (
                    location["lat"]
                    if "lat" in location
                    else location.get("latitude")
                )
                lng = (
                    location["lng"]
                    if "lng" in location
                    else location.get("longitude")
                )

                if lat is None or lng is None:
                    return {
                        "error": f"Could not get coordinates for address: {address}",
                        "address": address,
                        "count": 0,
                    }

                # Search for businesses near those coordinates
                businesses = places_api.search_nearby(
                    lat=lat,
                    lng=lng,
                    radius=50,
                )

                if not businesses:
                    return {
                        "address": address,
                        "coordinates": {"lat": lat, "lng": lng},
                        "count": 0,
                        "businesses": [],
                        "place_ids": [],
                        "message": f"No businesses found within 50m of {address}",
                    }

                formatted = [
                    _format_place_result(_place_to_dict(b))
                    for b in businesses[:10]
                ]

                return {
                    "address": address,
                    "coordinates": {"lat": lat, "lng": lng},
                    "count": len(formatted),
                    "businesses": formatted,
                    "place_ids": [b.get("place_id") for b in formatted],
                    "message": f"Found {len(formatted)} business(es) at {address}",
                }

            except Exception as e:
                logger.exception("Error finding businesses at address")
                return {"error": f"{e!s}"}

        tools.append(find_businesses_at_address_wrapper)

    return tools, state

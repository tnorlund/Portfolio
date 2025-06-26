"""
Merchant validation agent for receipt processing.

This module provides a configurable agent that validates merchant information
from receipts using Google Places API lookups and GPT validation.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional, Tuple

from agents import Agent, Runner, function_tool

from receipt_dynamo.constants import ValidationMethod

logger = logging.getLogger(__name__)

# Configuration
MAX_AGENT_ATTEMPTS = int(os.environ.get("MAX_AGENT_ATTEMPTS", 2))
AGENT_TIMEOUT_SECONDS = int(os.environ.get("AGENT_TIMEOUT_SECONDS", 300))
MAX_AGENT_TURNS = int(os.environ.get("MAX_AGENT_TURNS", 10))
LOG_AGENT_FUNCTION_CALLS = (
    os.environ.get("LOG_AGENT_FUNCTION_CALLS", "true").lower() == "true"
)


class MerchantValidationAgent:
    """Encapsulates the merchant validation agent and its tools."""

    def __init__(
        self, google_places_api_key: str, model: str = "gpt-3.5-turbo"
    ):
        """
        Initialize the merchant validation agent.

        Args:
            google_places_api_key: API key for Google Places
            model: OpenAI model to use for the agent
        """
        self.google_places_api_key = google_places_api_key
        self.model = model
        self.agent = self._create_agent()

    def _create_agent(self) -> Agent:
        """Create and configure the validation agent with tools."""
        tools = [
            self._create_search_by_phone_tool(),
            self._create_search_by_address_tool(),
            self._create_search_nearby_tool(),
            self._create_search_by_text_tool(),
            self._create_return_metadata_tool(),
        ]

        return Agent(
            name="ReceiptMerchantAgent",
            instructions="""
You are ReceiptMerchantAgent. Your goal is to assign the correct merchant to this receipt.
You may call the following tools in any order:

1. **search_by_phone**: to look up a business by phone.
2. **search_by_address**: to geocode the receipt's address.
3. **search_nearby**: to find businesses near a lat/lng.
4. **search_by_text**: to text‐search for a business name, biased by location.

**Policy**:
- First try phone lookup. If you get a business (non‐empty name & place_id), validate on name/address/phone.
- If that fails, geocode the address and do a nearby search; validate again.
- If still no match, perform a text search with the extracted or inferred merchant name; validate the result.
- Only if text search yields no usable match, then infer the merchant details via GPT inference.
- After deciding on the final metadata, call the function `tool_return_metadata`
  with the exact values for each field (place_id, merchant_name, etc.) and then stop.
""",
            model=self.model,
            tools=tools,
        )

    def _create_search_by_phone_tool(self):
        """Create the phone search tool."""

        @function_tool
        def search_by_phone(phone: str) -> Dict[str, Any]:
            """Search Google Places by a phone number."""
            from receipt_label.data.places_api import PlacesAPI

            return PlacesAPI(self.google_places_api_key).search_by_phone(phone)

        return search_by_phone

    def _create_search_by_address_tool(self):
        """Create the address search tool."""

        @function_tool
        def search_by_address(address: str) -> Dict[str, Any]:
            """Search Google Places by address and return the full place details payload."""
            from receipt_label.data.places_api import PlacesAPI

            return PlacesAPI(self.google_places_api_key).search_by_address(
                address
            )

        return search_by_address

    def _create_search_nearby_tool(self):
        """Create the nearby search tool."""

        @function_tool
        def search_nearby(
            lat: float, lng: float, radius: float
        ) -> List[Dict[str, Any]]:
            """Find nearby businesses given latitude, longitude, and radius."""
            from receipt_label.data.places_api import PlacesAPI

            return PlacesAPI(self.google_places_api_key).search_nearby(
                lat=lat, lng=lng, radius=radius
            )

        return search_nearby

    def _create_search_by_text_tool(self):
        """Create the text search tool."""

        @function_tool
        def search_by_text(
            query: str,
            lat: Optional[float] = None,
            lng: Optional[float] = None,
        ) -> Dict[str, Any]:
            """Text‐search for a business name, with optional location bias."""
            from receipt_label.data.places_api import PlacesAPI

            return PlacesAPI(self.google_places_api_key).search_by_text(
                query, lat, lng
            )

        return search_by_text

    def _create_return_metadata_tool(self):
        """Create the metadata return tool."""
        # Import ValidatedBy type at runtime to avoid circular imports
        from typing import Literal

        ValidatedBy = Literal[tuple(m.value for m in ValidationMethod)]

        @function_tool
        def tool_return_metadata(
            place_id: str,
            merchant_name: str,
            address: str,
            phone_number: str,
            merchant_category: str,
            matched_fields: List[str],
            validated_by: ValidatedBy,
            reasoning: str,
        ) -> Dict[str, Any]:
            """
            Return the final merchant metadata as a structured object.

            Args:
                place_id: Google Places place_id for the matched business.
                merchant_name: The canonical merchant name.
                address: The merchant's address.
                phone_number: The merchant's phone number.
                merchant_category: The merchant's category.
                matched_fields: List of receipt fields that were used to validate the match.
                validated_by: The method used for validation.
                reasoning: Explanation of how the match was determined.
            """
            # Normalize validated_by to allowed enum value
            try:
                vb_enum = ValidationMethod(validated_by)
            except ValueError:
                valid_vals = [e.value for e in ValidationMethod]
                raise ValueError(
                    f"validated_by must be one of: {valid_vals}. Got: {validated_by!r}"
                )

            return {
                "place_id": place_id,
                "merchant_name": merchant_name,
                "address": address,
                "phone_number": phone_number,
                "merchant_category": merchant_category,
                "matched_fields": matched_fields,
                "validated_by": vb_enum.value,
                "reasoning": reasoning,
            }

        return tool_return_metadata

    def validate_receipt(
        self,
        user_input: Dict[str, Any],
        max_attempts: int = None,
        timeout_seconds: int = None,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Run the agent to validate receipt merchant data.

        Args:
            user_input: Dictionary containing receipt data
            max_attempts: Maximum retry attempts (defaults to MAX_AGENT_ATTEMPTS)
            timeout_seconds: Timeout per attempt (defaults to AGENT_TIMEOUT_SECONDS)

        Returns:
            Tuple of (metadata dict or None, list of partial results)
        """
        max_attempts = max_attempts or MAX_AGENT_ATTEMPTS
        timeout_seconds = timeout_seconds or AGENT_TIMEOUT_SECONDS

        metadata = None
        partial_results = []
        user_messages = [{"role": "user", "content": json.dumps(user_input)}]

        for attempt in range(1, max_attempts + 1):
            logger.info(
                f"Starting agent attempt {attempt}/{max_attempts} for receipt "
                f"{user_input.get('image_id')}#{user_input.get('receipt_id')} "
                f"with {timeout_seconds}s timeout"
            )

            try:
                # Run the agent with timeout using ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        Runner.run_sync,
                        self.agent,
                        user_messages,
                        max_turns=MAX_AGENT_TURNS,
                    )

                    try:
                        run_result = future.result(timeout=timeout_seconds)

                        # Process agent results
                        metadata, partial = self._extract_agent_results(
                            run_result, attempt
                        )
                        partial_results.extend(partial)

                        if metadata is not None:
                            break

                    except FutureTimeoutError:
                        logger.error(
                            "Agent attempt timed out",
                            extra={
                                "attempt": attempt,
                                "timeout_seconds": timeout_seconds,
                                "image_id": user_input.get("image_id"),
                                "receipt_id": user_input.get("receipt_id"),
                                "partial_results_so_far": len(partial_results),
                            },
                        )
                        future.cancel()

            except Exception as e:
                logger.error(
                    "Agent attempt failed with exception",
                    extra={
                        "attempt": attempt,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "image_id": user_input.get("image_id"),
                        "receipt_id": user_input.get("receipt_id"),
                    },
                )

            logger.warning(
                f"Agent attempt {attempt} did not call tool_return_metadata; retrying."
                + (
                    f" Partial results collected: {len(partial_results)}"
                    if partial_results
                    else ""
                )
            )

        return metadata, partial_results

    def _extract_agent_results(
        self, run_result, attempt: int
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract metadata and partial results from agent run result."""
        metadata = None
        partial_results = []

        # Log all function calls made by the agent
        if LOG_AGENT_FUNCTION_CALLS:
            function_calls = []
            for item in run_result.new_items:
                raw = getattr(item, "raw_item", None)
                if hasattr(raw, "name") and raw.name:
                    function_calls.append(raw.name)

            if function_calls:
                logger.info(
                    f"Agent made {len(function_calls)} function calls: {', '.join(function_calls)}"
                )

        # Extract results
        for item in run_result.new_items:
            raw = getattr(item, "raw_item", None)
            if hasattr(raw, "name") and raw.name == "tool_return_metadata":
                try:
                    metadata = json.loads(raw.arguments)
                    logger.info(
                        f"Successfully parsed metadata on attempt {attempt}"
                    )
                except json.JSONDecodeError as e:
                    logger.error(
                        f"Failed to parse metadata JSON on attempt {attempt}: {e}"
                    )
                    metadata = getattr(item, "output", None)
                except Exception as e:
                    logger.error(
                        f"Unexpected error parsing metadata on attempt {attempt}: {e}"
                    )
                    metadata = getattr(item, "output", None)
                break

            # Collect partial results
            elif hasattr(raw, "name") and raw.name in [
                "search_by_phone",
                "search_by_address",
                "search_by_text",
            ]:
                try:
                    partial_result = {
                        "function": raw.name,
                        "result": getattr(item, "output", None),
                    }
                    partial_results.append(partial_result)
                    logger.info("Captured partial result from %s", raw.name)
                except Exception as e:
                    logger.warning("Failed to capture partial result: %s", e)

        return metadata, partial_results

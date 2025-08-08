"""
Merchant validation agent for receipt processing.

This module provides a configurable agent that validates merchant information
from receipts using Google Places API lookups and GPT validation.
"""

import asyncio
from datetime import datetime
from pathlib import Path
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from agents import Agent, Runner, function_tool, OpenAIChatCompletionsModel
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
        self,
        google_places_api_key: str,
        model: str = "gpt-3.5-turbo",
        openai_client=None,
    ):
        """
        Initialize the merchant validation agent.

        Args:
            google_places_api_key: API key for Google Places
            model: OpenAI model to use for the agent
            openai_client: Optional OpenAI client for local LLMs (e.g., Ollama)
        """
        self.google_places_api_key = google_places_api_key
        self.model = model
        self.openai_client = openai_client
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

        # Use OpenAIChatCompletionsModel if client provided, otherwise use string model
        if self.openai_client:
            model_obj = OpenAIChatCompletionsModel(
                model=self.model, openai_client=self.openai_client
            )
        else:
            model_obj = self.model  # Backward compatibility with string model

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
            model=model_obj,
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
                validated_by: The method used for validation. MUST be one of: "PHONE_LOOKUP", "ADDRESS_LOOKUP", "NEARBY_LOOKUP", "TEXT_SEARCH", or "INFERENCE"
                reasoning: Explanation of how the match was determined.
            """
            # Normalize validated_by to allowed enum value
            try:
                vb_enum = ValidationMethod(validated_by)
            except ValueError:
                # Try to map common variations
                normalized = (
                    validated_by.upper().replace(" ", "_").replace("GPT_", "")
                )
                if "INFERENCE" in normalized or "INFER" in normalized:
                    vb_enum = ValidationMethod.INFERENCE
                elif "PHONE" in normalized:
                    vb_enum = ValidationMethod.PHONE_LOOKUP
                elif "ADDRESS" in normalized:
                    vb_enum = ValidationMethod.ADDRESS_LOOKUP
                elif "NEARBY" in normalized:
                    vb_enum = ValidationMethod.NEARBY_LOOKUP
                elif "TEXT" in normalized or "SEARCH" in normalized:
                    vb_enum = ValidationMethod.TEXT_SEARCH
                else:
                    valid_vals = [e.value for e in ValidationMethod]
                    logger.warning(
                        f"Could not map validated_by '{validated_by}' to valid enum. "
                        f"Valid values: {valid_vals}. Defaulting to INFERENCE."
                    )
                    vb_enum = ValidationMethod.INFERENCE

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
                # Run the agent with timeout
                # If we have an async client, we need to handle it differently
                if self.openai_client and hasattr(
                    self.openai_client, "__aenter__"
                ):
                    # AsyncOpenAI client - need to run in async context
                    import asyncio
                    import nest_asyncio

                    nest_asyncio.apply()  # Allow nested event loops

                    async def run_agent_async():
                        return await Runner.run(
                            self.agent,
                            user_messages,
                            max_turns=MAX_AGENT_TURNS,
                        )

                    # Run with timeout
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        run_result = loop.run_until_complete(
                            asyncio.wait_for(
                                run_agent_async(), timeout=timeout_seconds
                            )
                        )
                    finally:
                        loop.close()
                else:
                    # Regular sync client or string model
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            Runner.run_sync,
                            self.agent,
                            user_messages,
                            max_turns=MAX_AGENT_TURNS,
                        )
                        run_result = future.result(timeout=timeout_seconds)

                # Process agent results
                metadata, partial = self._extract_agent_results(
                    run_result, attempt
                )
                partial_results.extend(partial)

                if metadata is not None:
                    logger.info(
                        "✅ Agent successfully found merchant metadata on attempt %d/%d",
                        attempt,
                        max_attempts,
                    )
                    break

            except asyncio.TimeoutError:
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
            except Exception as e:
                logger.error(
                    "Agent attempt failed with exception: %s",
                    str(e),
                    extra={
                        "attempt": attempt,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "image_id": user_input.get("image_id"),
                        "receipt_id": user_input.get("receipt_id"),
                    },
                    exc_info=True,  # Add stack trace
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

        # Save trace data if environment variable is set
        trace_dir = os.environ.get("AGENT_TRACE_DIR")
        if trace_dir:
            try:

                trace_path = Path(trace_dir)
                trace_path.mkdir(exist_ok=True)

                # Create a trace file with timestamp and trace ID
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                trace_id = getattr(run_result, "trace_id", None) or f"local_{timestamp}"
                trace_file = (
                    trace_path
                    / f"trace_{timestamp}_{trace_id}.json"
                )

                # Extract trace data
                trace_data = {
                    "trace_id": trace_id,
                    "attempt": attempt,
                    "timestamp": timestamp,
                    "messages": [],
                    "function_calls": [],
                    "model_responses": [],
                }

                # Extract all items from the run
                for item in run_result.new_items:
                    raw = getattr(item, "raw_item", None)
                    if raw:
                        if hasattr(raw, "name"):  # Function call
                            trace_data["function_calls"].append(
                                {
                                    "name": raw.name,
                                    "arguments": getattr(
                                        raw, "arguments", None
                                    ),
                                    "output": getattr(item, "output", None),
                                }
                            )
                        elif hasattr(raw, "content"):  # Message
                            trace_data["messages"].append(
                                {
                                    "role": getattr(raw, "role", "unknown"),
                                    "content": getattr(raw, "content", None),
                                }
                            )

                # Save trace to file
                with open(trace_file, "w") as f:
                    json.dump(trace_data, f, indent=2, default=str)

                logger.info(f"💾 Saved trace to {trace_file.name}")
            except Exception as e:
                logger.error(f"Failed to save trace: {e}", exc_info=True)

        # Log all function calls made by the agent with details
        if LOG_AGENT_FUNCTION_CALLS:
            function_calls = []
            for item in run_result.new_items:
                raw = getattr(item, "raw_item", None)
                if hasattr(raw, "name") and raw.name:
                    # Get function arguments for more detail
                    args_str = ""
                    if hasattr(raw, "arguments"):
                        try:
                            args = json.loads(raw.arguments)
                            # Format key arguments based on function
                            if raw.name == "search_by_phone":
                                args_str = f"(phone='{args.get('phone', '')}')"
                            elif raw.name == "search_by_address":
                                args_str = f"(address='{args.get('address', '')[:30]}...')"
                            elif raw.name == "search_by_text":
                                args_str = f"(query='{args.get('query', '')[:30]}...')"
                            elif raw.name == "search_nearby":
                                args_str = f"(lat={args.get('lat')}, lng={args.get('lng')})"
                            elif raw.name == "tool_return_metadata":
                                args_str = f"(merchant='{args.get('merchant_name', '')[:30]}...', method={args.get('validated_by', '')})"
                        except:
                            pass

                    function_calls.append(f"{raw.name}{args_str}")

            if function_calls:
                logger.info(
                    f"🔧 Agent called {len(function_calls)} tools:\n  "
                    + "\n  ".join(f"→ {call}" for call in function_calls)
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

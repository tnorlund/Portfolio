"""
Fix Place Lambda Handler (Container Lambda)

Fixes incorrect ReceiptPlace records using a LangGraph agent that reasons
about receipt content, searches Google Places, and uses ChromaDB similarity
search to find the correct place.

Input:
    {
        "image_id": "uuid-string",
        "receipt_id": 1,
        "reason": "Merchant shows 'Hyatt Regency Westlake' "
                  "but receipt is from VONS"
    }

Output:
    {
        "success": true,
        "image_id": "...",
        "receipt_id": 1,
        "old_merchant": "Hyatt Regency Westlake",
        "new_merchant": "Vons",
        "new_place_id": "ChIJ...",
        "confidence": 0.95,
        "reasoning": "Receipt shows VONS branding and address..."
    }

Environment Variables:
    DYNAMODB_TABLE_NAME: DynamoDB table name
    OPENROUTER_API_KEY: OpenRouter API key (LLM)
    RECEIPT_AGENT_OPENAI_API_KEY: OpenAI API key (embeddings)
    GOOGLE_PLACES_API_KEY: Google Places API key
    LANGCHAIN_API_KEY: LangSmith API key (tracing)
    CHROMA_CLOUD_API_KEY: Chroma Cloud API key
    CHROMA_CLOUD_TENANT: Chroma Cloud tenant ID
    CHROMA_CLOUD_DATABASE: Chroma Cloud database name
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

# LangSmith tracing - ensure traces are flushed before Lambda exits
try:
    from langsmith.run_trees import get_cached_client as get_langsmith_client

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    get_langsmith_client = None  # type: ignore

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Module-level event loop – reused across warm Lambda invocations.
# asyncio.run() closes the loop after each call, which breaks cached
# async clients (httpx, LangSmith) on subsequent warm invocations.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def _propagate_env_vars() -> None:
    """Ensure receipt_agent settings find our Lambda env vars."""
    aliases = [
        ("OPENAI_API_KEY", "RECEIPT_AGENT_OPENAI_API_KEY"),
        (
            "GOOGLE_PLACES_API_KEY",
            "RECEIPT_AGENT_GOOGLE_PLACES_API_KEY",
        ),
        ("DYNAMODB_TABLE_NAME", "RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
    ]
    for src, dest in aliases:
        if dest not in os.environ and src in os.environ:
            os.environ[dest] = os.environ[src]


def flush_langsmith_traces() -> None:
    """Flush all pending LangSmith traces to the API."""
    if HAS_LANGSMITH and get_langsmith_client is not None:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed successfully")
        except Exception as e:  # pylint: disable=broad-exception-caught
            error_str = str(e)
            if "multipart" in error_str.lower():
                logger.debug(
                    "LangSmith multipart upload error (non-fatal): %s",
                    error_str[:200],
                )
            else:
                logger.warning(
                    "Failed to flush LangSmith traces: %s",
                    error_str,
                )


async def _run_place_finder(
    image_id: str,
    receipt_id: int,
    reason: str,
) -> tuple[dict[str, Any], Any]:
    """Run the LangGraph place finder agent for a single receipt."""
    # pylint: disable=import-outside-toplevel
    from receipt_agent.clients.factory import (
        create_embed_fn,
        create_places_client,
    )
    from receipt_agent.subagents.place_finder import (
        create_receipt_place_finder_graph,
        run_receipt_place_finder,
    )
    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_dynamo import DynamoClient

    table_name = os.environ["DYNAMODB_TABLE_NAME"]
    dynamo_client = DynamoClient(table_name=table_name)

    # Create Chroma Cloud client
    cloud_api_key = os.environ.get("CHROMA_CLOUD_API_KEY", "")
    cloud_tenant = os.environ.get("CHROMA_CLOUD_TENANT")
    cloud_database = os.environ.get("CHROMA_CLOUD_DATABASE")

    chroma_client = ChromaClient(
        cloud_api_key=cloud_api_key or None,
        cloud_tenant=cloud_tenant,
        cloud_database=cloud_database,
        mode="read",
    )

    embed_fn = create_embed_fn()
    places_client = create_places_client()

    graph, state_holder = create_receipt_place_finder_graph(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places_client,
    )

    # Get receipt details for pre-loading
    details = dynamo_client.get_receipt_details(image_id, receipt_id)

    result = await run_receipt_place_finder(
        graph=graph,
        state_holder=state_holder,
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_lines=details.lines,
        receipt_words=details.words,
        reason=reason,
    )

    return result, details


def handler(  # pylint: disable=unused-argument
    event: dict[str, Any], context: Any
) -> dict[str, Any]:
    """
    Lambda handler to fix an incorrect ReceiptPlace record.

    Uses a LangGraph agent to:
    1. Read receipt content (lines, words with labels)
    2. Search ChromaDB for similar receipts
    3. Search Google Places for correct match
    4. Submit place data with confidence scoring
    5. Update ReceiptPlace with corrected data
    """
    try:
        # Validate input
        image_id = event.get("image_id")
        receipt_id = event.get("receipt_id")
        reason = event.get("reason", "User reported incorrect merchant")

        if not image_id or receipt_id is None:
            return {
                "success": False,
                "error": ("Missing required fields: image_id and receipt_id"),
            }

        logger.info(
            "Fixing place for image_id=%s, receipt_id=%s, reason=%s",
            image_id,
            receipt_id,
            reason[:100],
        )

        _propagate_env_vars()

        # Run the LangGraph agent on the persistent event loop
        agent_result, details = _loop.run_until_complete(
            _run_place_finder(image_id, receipt_id, reason)
        )

        current_place = details.place
        old_merchant = current_place.merchant_name if current_place else None

        if not agent_result or not agent_result.get("found"):
            return {
                "success": False,
                "error": "Agent could not find correct place",
                "image_id": image_id,
                "receipt_id": receipt_id,
                "old_merchant": old_merchant,
                "reasoning": (
                    agent_result.get("reasoning", "") if agent_result else ""
                ),
            }

        # Guard: merchant_name is required by ReceiptPlace
        if not agent_result.get("merchant_name"):
            return {
                "success": False,
                "error": (
                    "Agent found a place but merchant_name " "is missing"
                ),
                "image_id": image_id,
                "receipt_id": receipt_id,
                "old_merchant": old_merchant,
                "reasoning": agent_result.get("reasoning", ""),
                "fields_found": agent_result.get("fields_found", []),
            }

        # Update ReceiptPlace with agent results
        new_place = _update_receipt_place(
            image_id=image_id,
            receipt_id=receipt_id,
            current_place=current_place,
            agent_result=agent_result,
            reason=reason,
        )

        response = {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "old_merchant": old_merchant,
            "new_merchant": new_place.merchant_name,
            "new_place_id": new_place.place_id,
            "confidence": agent_result.get("confidence", 0.0),
            "reasoning": agent_result.get("reasoning", ""),
            "fields_found": agent_result.get("fields_found", []),
            "sources": agent_result.get("sources", {}),
        }

        logger.info("Successfully fixed place: %s", response)
        return response

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error fixing place")
        return {
            "success": False,
            "error": str(e),
            "image_id": event.get("image_id"),
            "receipt_id": event.get("receipt_id"),
        }

    finally:
        flush_langsmith_traces()


def _update_receipt_place(
    image_id: str,
    receipt_id: int,
    current_place: Any,
    agent_result: dict[str, Any],
    reason: str,
) -> Any:
    """Update or create ReceiptPlace with agent results."""
    # pylint: disable=import-outside-toplevel
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.entities import ReceiptPlace

    table_name = os.environ["DYNAMODB_TABLE_NAME"]
    dynamo_client = DynamoClient(table_name=table_name)
    now = datetime.now(timezone.utc)

    confidence = agent_result.get("confidence", 0.0)
    reasoning = f"Fixed: {reason}. {agent_result.get('reasoning', '')}"

    if current_place:
        # Update existing place
        if agent_result.get("place_id"):
            current_place.place_id = agent_result["place_id"]
        if agent_result.get("merchant_name"):
            current_place.merchant_name = agent_result["merchant_name"]
        if agent_result.get("address"):
            current_place.formatted_address = agent_result["address"]
        if agent_result.get("phone_number"):
            current_place.phone_number = agent_result["phone_number"]

        current_place.confidence = confidence
        current_place.reasoning = reasoning
        current_place.validated_by = "INFERENCE"
        current_place.validation_status = (
            "MATCHED" if confidence >= 0.8 else "UNSURE"
        )
        current_place.timestamp = now

        dynamo_client.update_receipt_place(current_place)
        return current_place

    # Create new place
    new_place = ReceiptPlace(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=agent_result.get("place_id", ""),
        merchant_name=agent_result.get("merchant_name", ""),
        formatted_address=agent_result.get("address", ""),
        phone_number=agent_result.get("phone_number", ""),
        merchant_types=[],
        confidence=confidence,
        reasoning=reasoning,
        validated_by="INFERENCE",
        validation_status=("MATCHED" if confidence >= 0.8 else "UNSURE"),
        timestamp=now,
    )

    dynamo_client.add_receipt_place(new_place)
    return new_place

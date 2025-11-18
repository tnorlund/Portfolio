"""Container Lambda handler for validating labels using CoVe templates."""

import asyncio
import json
import logging
import os
from typing import Any, Dict

from langchain_ollama import ChatOllama
from receipt_dynamo import DynamoClient
from receipt_label.langchain.utils.batch_validation_cove import (
    validate_labels_using_cove_templates,
)
from receipt_label.langchain.utils.retry import retry_with_backoff

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
OLLAMA_API_KEY = os.environ["OLLAMA_API_KEY"]


def _get_str(d: Dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    return str(v) if v is not None else None


def _chunk(iterable: list, n: int):
    """Split an iterable into chunks of size n."""
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def create_llm(api_key: str) -> ChatOllama:
    """Create Ollama LLM instance."""
    return ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {api_key}"},
            "timeout": 120,
        },
        format="json",
        temperature=0.3,
    )


async def validate_receipt_async(
    image_id: str,
    receipt_id: int,
    dynamo_client: DynamoClient,
    llm: ChatOllama,
) -> Dict[str, Any]:
    """
    Validate PENDING/NEEDS_REVIEW labels for a receipt using CoVe templates.

    Returns:
        {
            "image_id": "...",
            "receipt_id": 1,
            "validated_count": 5,
            "invalidated_count": 2,
            "still_pending_count": 1,
        }
    """
    # Load receipt data
    receipt_lines = dynamo_client.list_receipt_lines_from_receipt(
        image_id=image_id,
        receipt_id=receipt_id,
    )
    receipt_words = dynamo_client.list_receipt_words_from_receipt(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    # Build receipt text
    receipt_text = "\n".join(
        f"Line {line.line_id}: {line.text}"
        for line in receipt_lines
    )

    # Build word text lookup
    word_text_lookup = {
        (word.line_id, word.word_id): word.text
        for word in receipt_words
    }

    # Get merchant name if available
    merchant_name = None
    try:
        receipt_metadata = dynamo_client.get_receipt_metadata(
            image_id=image_id,
            receipt_id=receipt_id,
        )
        if receipt_metadata and receipt_metadata.merchant_name:
            merchant_name = receipt_metadata.merchant_name
    except Exception:
        pass

    # Query all labels for this receipt, then filter by status
    all_labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    # Filter to PENDING and NEEDS_REVIEW labels
    pending_labels = [
        l for l in all_labels
        if l.validation_status in ["PENDING", "NEEDS_REVIEW"]
    ]

    if not pending_labels:
        logger.info("No PENDING/NEEDS_REVIEW labels found for receipt %s/%s", image_id, receipt_id)
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "validated_count": 0,
            "invalidated_count": 0,
            "still_pending_count": 0,
        }

    logger.info(
        "Validating %d labels for receipt %s/%s using CoVe templates",
        len(pending_labels),
        image_id,
        receipt_id,
    )

    # Validate using CoVe templates with retry logic for rate limiting
    # Wrap in retry_with_backoff to handle Ollama API rate limits (429 errors)
    validated_labels = await retry_with_backoff(
        validate_labels_using_cove_templates,
        pending_labels=pending_labels,
        receipt_text=receipt_text,
        llm=llm,
        dynamo_client=dynamo_client,
        word_text_lookup=word_text_lookup,
        merchant_name=merchant_name,
        max_retries=5,  # More retries for rate limiting
        initial_delay=2.0,  # Start with 2 second delay
        max_delay=30.0,  # Cap at 30 seconds between retries
        backoff_factor=2.0,  # Exponential backoff
    )

    # Count results
    validated_count = sum(1 for l in validated_labels if l.validation_status == "VALID")
    invalidated_count = sum(1 for l in validated_labels if l.validation_status == "INVALID")
    still_pending_count = sum(
        1 for l in validated_labels
        if l.validation_status in ["PENDING", "NEEDS_REVIEW"]
    )

    # Update labels in DynamoDB
    if validated_labels:
        # Chunk into batches of 25 (DynamoDB BatchWriteItem limit)
        for chunk in _chunk(validated_labels, 25):
            dynamo_client.update_receipt_word_labels(chunk)
        logger.info(
            "Updated %d labels: %d VALID, %d INVALID, %d PENDING",
            len(validated_labels),
            validated_count,
            invalidated_count,
            still_pending_count,
        )

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "validated_count": validated_count,
        "invalidated_count": invalidated_count,
        "still_pending_count": still_pending_count,
    }


def handler(event: Dict[str, Any], _) -> Dict[str, Any]:
    """
    Validate labels for a single receipt using CoVe templates.

    Expected event keys:
    - receipt_id (int or str)
    - image_id (str)
    """
    logger.info("Validate receipt CoVe templates event: %s", json.dumps(event))

    try:
        image_id = _get_str(event, "image_id")
        receipt_id_raw = _get_str(event, "receipt_id")
        if image_id is None or receipt_id_raw is None:
            return {
                "statusCode": 400,
                "error": "Missing image_id or receipt_id",
            }
        receipt_id = int(str(receipt_id_raw))

        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)
        llm = create_llm(OLLAMA_API_KEY)

        # Execute async validation
        result = asyncio.run(
            validate_receipt_async(
                image_id=image_id,
                receipt_id=receipt_id,
                dynamo_client=dynamo_client,
                llm=llm,
            )
        )

        return {
            "statusCode": 200,
            **result,
        }
    except Exception as e:
        logger.exception("Validate receipt CoVe templates failed")
        return {"statusCode": 500, "error": str(e)}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Container Lambda handler wrapper."""
    return handler(event, context)


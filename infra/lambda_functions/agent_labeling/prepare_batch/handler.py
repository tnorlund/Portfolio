"""
Lambda handler for preparing batch data for GPT processing.

This handler prepares receipt data for batch processing via OpenAI's
Batch API, optimizing for token efficiency and cost.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")
table_name = os.environ["DYNAMO_TABLE_NAME"]
table = dynamodb.Table(table_name)

# Batch configuration
MAX_TOKENS_PER_BATCH = 50000  # OpenAI batch limit
ESTIMATED_TOKENS_PER_WORD = 2  # Conservative estimate


def estimate_tokens(text: str) -> int:
    """Estimate token count for text (rough approximation)."""
    # Simple estimation: ~1 token per 4 characters
    return len(text) // 4 + 1


def create_batch_prompt(receipt_data: Dict[str, Any]) -> str:
    """Create optimized prompt for batch processing."""
    words = receipt_data["words"]
    lines = receipt_data["lines"]
    metadata = receipt_data.get("metadata", {})
    pattern_labels = receipt_data.get("pattern_labels", {})

    # Build context
    context_parts = []

    if metadata.get("merchant_name"):
        context_parts.append(f"Merchant: {metadata['merchant_name']}")

    if metadata.get("location"):
        context_parts.append(f"Location: {metadata['location']}")

    # Add pattern-detected labels as context
    if pattern_labels:
        label_summary = {}
        for word_id, label_info in pattern_labels.items():
            label = label_info["label"]
            label_summary[label] = label_summary.get(label, 0) + 1
        context_parts.append(f"Already detected: {json.dumps(label_summary)}")

    context = (
        " | ".join(context_parts) if context_parts else "No context available"
    )

    # Build receipt text representation
    receipt_text = "\n".join([line["text"] for line in lines])

    # Create prompt
    prompt = f"""Label the following receipt words with appropriate categories.
Context: {context}

Receipt text:
{receipt_text}

Words to label:
"""

    # Add unlabeled words
    for word in words:
        if word["word_id"] not in pattern_labels:
            prompt += f"\n- Word ID {word['word_id']}: '{word['text']}' (line {word['line_number']}, pos {word['position']})"

    prompt += '\n\nProvide labels in JSON format: {"word_id": "label_name"}'

    return prompt


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Prepare batch data for GPT processing.

    Args:
        event: Contains receipt_id, labeling results, and metadata
        context: Lambda context

    Returns:
        Dictionary with:
        - batch_id: Unique batch identifier
        - batch_size: Number of receipts in batch
        - estimated_tokens: Estimated token count
        - batch_data: Prepared data for batch processing
    """
    try:
        receipt_id = event["receipt_id"]
        labeling_results = event.get("labeling", {})
        metadata = event.get("metadata", {})

        logger.info("Preparing batch for receipt: %s", receipt_id)

        # Generate batch ID
        batch_id = f"batch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # Get receipt words and lines
        words_response = table.query(
            KeyConditionExpression=Key("PK").eq(f"RECEIPT#{receipt_id}")
            & Key("SK").begins_with("WORD#")
        )
        words = words_response.get("Items", [])

        lines_response = table.query(
            KeyConditionExpression=Key("PK").eq(f"RECEIPT#{receipt_id}")
            & Key("SK").begins_with("LINE#")
        )
        lines = lines_response.get("Items", [])

        # Convert to format for batch processing
        word_data = []
        for word_item in words:
            word_data.append(
                {
                    "word_id": word_item["SK"].split("#")[1],
                    "text": word_item.get("text", ""),
                    "line_number": word_item.get("line_number", 0),
                    "position": word_item.get("position", 0),
                }
            )

        line_data = []
        for line_item in lines:
            line_data.append(
                {
                    "line_id": line_item["SK"].split("#")[1],
                    "text": line_item.get("text", ""),
                    "line_number": line_item.get("line_number", 0),
                }
            )

        # Prepare batch data
        receipt_batch_data = {
            "receipt_id": receipt_id,
            "words": word_data,
            "lines": line_data,
            "metadata": metadata,
            "pattern_labels": labeling_results.get("pattern_labels", {}),
        }

        # Create prompt and estimate tokens
        prompt = create_batch_prompt(receipt_batch_data)
        estimated_tokens = estimate_tokens(prompt)

        # Store batch metadata in DynamoDB
        batch_item = {
            "PK": f"BATCH#{batch_id}",
            "SK": f"RECEIPT#{receipt_id}",
            "batch_id": batch_id,
            "receipt_id": receipt_id,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "estimated_tokens": estimated_tokens,
            "prompt": prompt,
            "metadata": metadata,
            "pattern_labels": labeling_results.get("pattern_labels", {}),
            "missing_essential_labels": labeling_results.get(
                "missing_essential_labels", []
            ),
        }

        table.put_item(Item=batch_item)

        result = {
            "batch_id": batch_id,
            "batch_size": 1,  # Single receipt for now
            "estimated_tokens": estimated_tokens,
            "batch_data": {
                "batch_id": batch_id,
                "receipts": [receipt_batch_data],
                "total_tokens": estimated_tokens,
            },
        }

        logger.info(
            "Batch prepared: %s with %d estimated tokens",
            batch_id,
            estimated_tokens,
        )
        return result

    except Exception as e:
        logger.error("Error preparing batch: %s", str(e))
        raise

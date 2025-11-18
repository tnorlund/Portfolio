import json
import logging
import os
from typing import Any, Dict, Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_label.langchain.currency_validation import analyze_receipt_simple

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
OLLAMA_API_KEY = os.environ["OLLAMA_API_KEY"]
LANGCHAIN_API_KEY = os.environ.get("LANGCHAIN_API_KEY")


def _get_str(d: Dict[str, Any], key: str) -> Optional[str]:
    v = d.get(key)
    return str(v) if v is not None else None


def handler(event: Dict[str, Any], _):
    """Create/update labels for a single receipt using LangGraph flow.

    This handler:
    1. Runs LangGraph workflow to get suggested labels
    2. Sets all labels to PENDING validation status
    3. Uses comparison logic to determine adds vs updates
    4. Saves labels to DynamoDB

    Expected event keys:
    - receipt_id (int or str)
    - image_id (str)
    """
    logger.info("Create labels event: %s", json.dumps(event))

    try:
        image_id = _get_str(event, "image_id")
        receipt_id_raw = _get_str(event, "receipt_id")
        if image_id is None or receipt_id_raw is None:
            return {
                "statusCode": 400,
                "error": "Missing image_id or receipt_id",
            }
        receipt_id = int(str(receipt_id_raw))

        client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Execute async analyzer from sync Lambda runtime
        import asyncio

        # Run LangGraph workflow but don't save labels yet
        # We'll set them to PENDING and save manually
        result = asyncio.run(
            analyze_receipt_simple(
                client,
                image_id,
                receipt_id,
                ollama_api_key=OLLAMA_API_KEY,
                langsmith_api_key=LANGCHAIN_API_KEY,
                save_labels=False,  # Don't save yet - we'll set PENDING status first
                dry_run=False,
                save_dev_state=False,
            )
        )

        # Get labels to add and update from the result
        labels_to_add = getattr(result, "receipt_word_labels_to_add", None) or []
        labels_to_update = getattr(result, "receipt_word_labels_to_update", None) or []

        # Set all labels to PENDING validation status
        for label in labels_to_add:
            label.validation_status = ValidationStatus.PENDING.value

        for label in labels_to_update:
            label.validation_status = ValidationStatus.PENDING.value

        # Now save the labels with PENDING status
        if labels_to_add:
            logger.info("Adding %d labels with PENDING status", len(labels_to_add))
            client.add_receipt_word_labels(labels_to_add)

        if labels_to_update:
            logger.info("Updating %d labels with PENDING status", len(labels_to_update))
            client.update_receipt_word_labels(labels_to_update)

        return {
            "statusCode": 200,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "confidence": result.confidence_score,
            "labels_added": len(labels_to_add),
            "labels_updated": len(labels_to_update),
            "processing_time": result.processing_time,
        }
    except Exception as e:
        logger.exception("Create labels failed")
        return {"statusCode": 500, "error": str(e)}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Container Lambda handler wrapper."""
    return handler(event, context)


import json
import logging
import os
from typing import Any, Dict, Optional

from receipt_dynamo import DynamoClient
from receipt_label.langchain.currency_validation import analyze_receipt_simple

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def _get_str(d: Dict[str, Any], key: str) -> Optional[str]:
    v = d.get(key)
    return str(v) if v is not None else None


def handler(event: Dict[str, Any], _):
    """Run currency validation for a single receipt using LangGraph flow.

    Expected event keys:
    - receipt_id (int or str)
    - image_id (str)
    - ollama_api_key (str)               [RECOMMENDED]
    - langsmith_api_key (str, optional)  [OPTIONAL]
    - save_labels (bool, default False)
    - dry_run (bool, default False)
    - save_dev_state (bool, default False)
    """
    logger.info(f"Currency validation event: {json.dumps(event)}")

    try:
        image_id = _get_str(event, "image_id")
        receipt_id_raw = _get_str(event, "receipt_id")
        if image_id is None or receipt_id_raw is None:
            return {
                "statusCode": 400,
                "error": "Missing image_id or receipt_id",
            }
        receipt_id = int(str(receipt_id_raw))

        ollama_api_key = _get_str(event, "ollama_api_key") or os.environ.get(
            "OLLAMA_API_KEY"
        )
        langsmith_api_key = _get_str(event, "langsmith_api_key") or os.environ.get(
            "LANGCHAIN_API_KEY"
        )

        if not ollama_api_key:
            return {"statusCode": 400, "error": "OLLAMA_API_KEY is required"}

        save_labels = bool(event.get("save_labels", False))
        dry_run = bool(event.get("dry_run", False))
        save_dev_state = bool(event.get("save_dev_state", False))

        client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Execute async analyzer from sync Lambda runtime
        import asyncio

        result = asyncio.run(
            analyze_receipt_simple(
                client,
                image_id,
                receipt_id,
                ollama_api_key=ollama_api_key,
                langsmith_api_key=langsmith_api_key,
                save_labels=save_labels,
                dry_run=dry_run,
                save_dev_state=save_dev_state,
            )
        )

        return {
            "statusCode": 200,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "confidence": result.confidence_score,
            "labels": len(result.discovered_labels or []),
            "processing_time": result.processing_time,
        }
    except Exception as e:
        logger.exception("Currency validation failed")
        return {"statusCode": 500, "error": str(e)}



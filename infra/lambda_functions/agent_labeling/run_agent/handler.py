"""
Lambda handler for running the agent-based labeling system.

This is the core handler that orchestrates pattern detection,
makes smart GPT decisions, and applies labels to receipt words.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Import agent components from receipt_label package
from receipt_label.agent import (
    BatchProcessor,
    LabelApplicator,
    PatternDetector,
    SmartDecisionEngine,
)
from receipt_label.client_manager import ClientManager
from receipt_label.constants import CORE_LABELS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
table_name = os.environ["DYNAMO_TABLE_NAME"]
table = dynamodb.Table(table_name)

# Initialize client manager
client_config = {
    "environment": os.environ.get("ENVIRONMENT", "production"),
    "openai_api_key": os.environ.get("OPENAI_API_KEY"),
    "pinecone_api_key": os.environ.get("PINECONE_API_KEY"),
    "pinecone_index_name": os.environ.get("PINECONE_INDEX_NAME"),
    "pinecone_host": os.environ.get("PINECONE_HOST"),
}
client_manager = ClientManager(client_config)


def get_receipt_words(receipt_id: str) -> List[Dict[str, Any]]:
    """Get all words for a receipt from DynamoDB."""
    response = table.query(
        KeyConditionExpression=Key("PK").eq(f"RECEIPT#{receipt_id}")
        & Key("SK").begins_with("WORD#")
    )
    return response.get("Items", [])


def get_receipt_lines(receipt_id: str) -> List[Dict[str, Any]]:
    """Get all lines for a receipt from DynamoDB."""
    response = table.query(
        KeyConditionExpression=Key("PK").eq(f"RECEIPT#{receipt_id}")
        & Key("SK").begins_with("LINE#")
    )
    return response.get("Items", [])


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Run agent-based labeling on a receipt.

    Args:
        event: Contains receipt_id, metadata, and embeddings info
        context: Lambda context

    Returns:
        Dictionary with:
        - pattern_labels: Labels applied by pattern detection
        - gpt_required: Whether GPT is needed for remaining labels
        - essential_labels_found: List of essential labels detected
        - missing_essential_labels: List of missing essential labels
        - unlabeled_count: Number of words still needing labels
        - processing_time_ms: Time taken to process
    """
    try:
        start_time = time.time()
        receipt_id = event["receipt_id"]
        metadata = event.get("metadata", {})
        embeddings_info = event.get("embeddings", {})

        logger.info(f"Running agent labeling for receipt: {receipt_id}")

        # Initialize agent components
        pattern_detector = PatternDetector(client_manager)
        decision_engine = SmartDecisionEngine()
        label_applicator = LabelApplicator()

        # Get receipt data
        words = get_receipt_words(receipt_id)
        lines = get_receipt_lines(receipt_id)

        if not words:
            logger.warning(f"No words found for receipt: {receipt_id}")
            return {
                "pattern_labels": {},
                "gpt_required": False,
                "essential_labels_found": [],
                "missing_essential_labels": list(
                    decision_engine.essential_labels
                ),
                "unlabeled_count": 0,
                "processing_time_ms": 0,
            }

        # Convert DynamoDB items to format expected by agent
        word_data = []
        for word_item in words:
            word_data.append(
                {
                    "text": word_item.get("text", ""),
                    "line_number": word_item.get("line_number", 0),
                    "position": word_item.get("position", 0),
                    "word_id": word_item["SK"].split("#")[
                        1
                    ],  # Extract word ID
                    "bounding_box": word_item.get("bounding_box", {}),
                }
            )

        line_data = []
        for line_item in lines:
            line_data.append(
                {
                    "text": line_item.get("text", ""),
                    "line_number": line_item.get("line_number", 0),
                    "line_id": line_item["SK"].split("#")[
                        1
                    ],  # Extract line ID
                    "bounding_box": line_item.get("bounding_box", {}),
                }
            )

        # Run pattern detection
        logger.info("Running pattern detection...")
        pattern_matches = pattern_detector.detect_patterns(
            receipt_id=receipt_id,
            words=word_data,
            lines=line_data,
            metadata=metadata,
        )

        # Apply pattern-based labels
        pattern_labels = label_applicator.apply_pattern_labels(
            words=word_data, pattern_matches=pattern_matches
        )

        # Count pattern labels by type
        pattern_label_counts = {}
        for word_id, label_info in pattern_labels.items():
            label = label_info["label"]
            pattern_label_counts[label] = (
                pattern_label_counts.get(label, 0) + 1
            )

        logger.info(f"Pattern detection found: {pattern_label_counts}")

        # Check which essential labels were found
        essential_labels_found = []
        missing_essential_labels = []

        for label_key, label_info in decision_engine.essential_labels.items():
            if label_info["label"] in pattern_label_counts:
                essential_labels_found.append(label_key)
            else:
                missing_essential_labels.append(label_key)

        # Make GPT decision
        gpt_decision = decision_engine.should_use_gpt(
            pattern_matches=pattern_matches, confidence_threshold=0.8
        )

        # Count unlabeled words
        labeled_word_ids = set(pattern_labels.keys())
        all_word_ids = {w["word_id"] for w in word_data}
        unlabeled_count = len(all_word_ids - labeled_word_ids)

        processing_time_ms = int((time.time() - start_time) * 1000)

        result = {
            "pattern_labels": pattern_labels,
            "gpt_required": gpt_decision["use_gpt"],
            "gpt_reason": gpt_decision.get("reason", ""),
            "essential_labels_found": essential_labels_found,
            "missing_essential_labels": missing_essential_labels,
            "unlabeled_count": unlabeled_count,
            "total_words": len(word_data),
            "processing_time_ms": processing_time_ms,
        }

        logger.info(
            f"Agent labeling result: {json.dumps(result, default=str)}"
        )
        return result

    except Exception as e:
        logger.error(f"Error in agent labeling: {str(e)}")
        raise

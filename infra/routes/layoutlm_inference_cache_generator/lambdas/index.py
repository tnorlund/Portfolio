"""Lambda handler for generating LayoutLM inference cache."""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_layoutlm import LayoutLMInference

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_CACHE_BUCKET = os.environ["S3_CACHE_BUCKET"]
LAYOUTLM_TRAINING_BUCKET = os.environ.get("LAYOUTLM_TRAINING_BUCKET")
MODEL_S3_URI = os.environ.get("MODEL_S3_URI")  # Optional override
CACHE_KEY = "layoutlm-inference-cache/latest.json"
MODEL_DIR = "/tmp/layoutlm-model"  # Persists across warm invocations

# Initialize clients
s3_client = boto3.client("s3")


def _normalize_label_for_4label_setup(raw_label: str) -> str:
    """Normalize ground truth labels to match 4-label training setup.

    This matches the training normalization logic exactly:
    - Removes BIO prefix (training adds it later per-line)
    - Merges labels: LINE_TOTAL/SUBTOTAL/TAX/GRAND_TOTAL → AMOUNT
    - Merges labels: TIME → DATE
    - Merges labels: PHONE_NUMBER/ADDRESS_LINE → ADDRESS
    - Filters to allowed labels: MERCHANT_NAME, DATE, ADDRESS, AMOUNT

    Args:
        raw_label: Raw label from DynamoDB (e.g., "LINE_TOTAL", "B-DATE", "PHONE_NUMBER")

    Returns:
        Normalized base label WITHOUT BIO prefix (e.g., "AMOUNT", "DATE", "ADDRESS", "O")
        BIO prefix is added per-line in the matching logic, matching training behavior.
    """
    if not raw_label:
        return "O"

    # Remove BIO prefix if present (training normalizes before adding BIO)
    label = raw_label.upper()
    if label.startswith("B-") or label.startswith("I-"):
        label = label[2:]

    # Merge amounts (matches training: merge_amounts flag)
    if label in {"LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"}:
        label = "AMOUNT"

    # Merge DATE and TIME (matches training: merge_date_time flag)
    if label == "TIME":
        label = "DATE"

    # Merge ADDRESS_LINE and PHONE_NUMBER (matches training: merge_address_phone flag)
    if label in {"PHONE_NUMBER", "ADDRESS_LINE"}:
        label = "ADDRESS"

    # Only keep the 4 allowed labels, map others to O (matches training: allowed_labels)
    allowed = {"MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"}
    if label not in allowed:
        return "O"

    # Return base label WITHOUT BIO prefix (training adds BIO per-line)
    return label


def calculate_metrics(
    predictions: List[Dict[str, Any]], ground_truth: Dict[str, str]
) -> Dict[str, Any]:
    """Calculate accuracy metrics comparing predictions to ground truth.

    Args:
        predictions: List of prediction dicts with word_id, predicted_label, confidence
        ground_truth: Dict mapping word_id to ground truth label

    Returns:
        Dict with overall_accuracy and per-label metrics
    """
    if not predictions or not ground_truth:
        return {
            "overall_accuracy": 0.0,
            "per_label_f1": {},
            "per_label_precision": {},
            "per_label_recall": {},
        }

    # Count correct predictions
    correct = 0
    total = 0

    # Per-label counts for F1 calculation
    label_true_positives: Dict[str, int] = {}
    label_false_positives: Dict[str, int] = {}
    label_false_negatives: Dict[str, int] = {}

    for pred in predictions:
        word_id = pred.get("word_id")
        line_id = pred.get("line_id")
        predicted = pred.get("predicted_label", "O")
        # Get ground truth - use (line_id, word_id) as key since word_id is only unique within a line
        if word_id is not None and line_id is not None:
            actual = ground_truth.get((line_id, word_id), "O")
        else:
            actual = "O"

        total += 1
        if predicted == actual:
            correct += 1
            # True positive
            if predicted != "O":
                label_true_positives[predicted] = label_true_positives.get(predicted, 0) + 1
        else:
            # False positive for predicted label
            if predicted != "O":
                label_false_positives[predicted] = label_false_positives.get(predicted, 0) + 1
            # False negative for actual label
            if actual != "O":
                label_false_negatives[actual] = label_false_negatives.get(actual, 0) + 1

    overall_accuracy = (correct / total) if total > 0 else 0.0

    # Calculate per-label F1, precision, recall
    per_label_f1: Dict[str, float] = {}
    per_label_precision: Dict[str, float] = {}
    per_label_recall: Dict[str, float] = {}

    all_labels = set(label_true_positives.keys()) | set(label_false_positives.keys()) | set(label_false_negatives.keys())

    for label in all_labels:
        tp = label_true_positives.get(label, 0)
        fp = label_false_positives.get(label, 0)
        fn = label_false_negatives.get(label, 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        per_label_precision[label] = precision
        per_label_recall[label] = recall
        per_label_f1[label] = f1

    return {
        "overall_accuracy": overall_accuracy,
        "per_label_f1": per_label_f1,
        "per_label_precision": per_label_precision,
        "per_label_recall": per_label_recall,
        "total_words": total,
        "correct_predictions": correct,
    }


def handler(_event, _context):
    """Handle EventBridge scheduled event to generate LayoutLM inference cache.

    Args:
        _event: EventBridge event (unused)
        _context: Lambda context (unused)

    Returns:
        dict: Status of cache generation
    """
    logger.info("Starting LayoutLM inference cache generation")

    try:
        # Initialize clients
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Load model (cached in /tmp after first load)
        logger.info("Loading LayoutLM model from S3")
        infer = LayoutLMInference(
            model_dir=MODEL_DIR,
            model_s3_uri=MODEL_S3_URI,
            auto_from_bucket_env="LAYOUTLM_TRAINING_BUCKET" if LAYOUTLM_TRAINING_BUCKET else None,
        )
        logger.info("Model loaded successfully. Device: %s", infer._device)

        # Step 1: Select random receipt with VALID labels
        # Get all VALID labels (any label type)
        logger.info("Fetching receipts with VALID labels")

        # Query for receipts that have at least one VALID label
        # We'll get a random receipt by querying for labels and picking one
        all_valid_labels, _ = dynamo_client.list_receipt_word_labels_with_status(
            ValidationStatus.VALID, limit=100
        )

        if not all_valid_labels:
            logger.warning("No VALID labels found")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No VALID labels found"}),
            }

        # Group labels by receipt
        receipts_with_labels: Dict[tuple[str, int], List] = {}
        for label in all_valid_labels:
            key = (label.image_id, label.receipt_id)
            receipts_with_labels.setdefault(key, []).append(label)

        # Select random receipt
        selected_key = random.choice(list(receipts_with_labels.keys()))
        image_id, receipt_id = selected_key
        selected_labels = receipts_with_labels[selected_key]

        logger.info(
            "Selected receipt: image_id=%s, receipt_id=%s, label_count=%d",
            image_id,
            receipt_id,
            len(selected_labels),
        )

        # Step 2: Get receipt details
        logger.info("Loading receipt details")
        receipt_details = dynamo_client.get_receipt_details(image_id, receipt_id)

        # Step 3: Run inference
        logger.info("Running LayoutLM inference")
        inference_result = infer.predict_receipt_from_dynamo(
            dynamo_client, image_id, receipt_id
        )

        # Step 4: Build ground truth mapping ((line_id, word_id) -> base label)
        # Only use VALID labels, and normalize them to match 4-label training setup
        # Note: We store base labels (AMOUNT, DATE, etc.) without BIO prefix
        # BIO prefix will be added per-line when matching to predictions (matching training)
        # IMPORTANT: word_id is only unique within a line, so we need (line_id, word_id) as key
        ground_truth_base: Dict[tuple[int, int], str] = {}
        for label in receipt_details.labels:
            if label.validation_status == ValidationStatus.VALID:
                # Normalize label to base form (matches training normalization)
                normalized = _normalize_label_for_4label_setup(label.label)
                ground_truth_base[(label.line_id, label.word_id)] = normalized

        # Step 5: Build predictions array
        predictions: List[Dict[str, Any]] = []

        # Build mapping from (line_id, text) -> word_id for efficient lookup
        # Also build mapping from (line_id, word_id) -> word for position lookup
        word_lookup: Dict[tuple[int, str], int] = {}
        words_by_line: Dict[int, List[Any]] = {}
        for word in receipt_details.words:
            # Store by exact text
            word_lookup[(word.line_id, word.text)] = word.word_id
            # Also store with stripped text for fuzzy matching
            word_lookup[(word.line_id, word.text.strip())] = word.word_id
            # Group words by line
            words_by_line.setdefault(word.line_id, []).append(word)

        # Sort words within each line by word_id to maintain order
        for line_id in words_by_line:
            words_by_line[line_id].sort(key=lambda w: w.word_id)

        # Build predictions from line predictions
        # line_pred.tokens contains the original word texts in order
        # line_pred.labels contains predictions per word (same order, in BIO format)
        for line_pred in inference_result.lines:
            line_id = line_pred.line_id
            line_words = words_by_line.get(line_id, [])

            # First, collect all word_ids and their base labels for this line
            # Then convert to BIO format per-line (matching training logic)
            line_word_ids: List[int] = []
            line_base_labels: List[str] = []

            # Match tokens to words and collect base labels
            for token_idx, token in enumerate(line_pred.tokens):
                word_id = None

                # Try to match by position first (most common case)
                if token_idx < len(line_words):
                    word = line_words[token_idx]
                    if token == word.text or token.strip() == word.text.strip():
                        word_id = word.word_id

                # If position match failed, try text lookup
                if word_id is None:
                    word_id = word_lookup.get((line_id, token)) or word_lookup.get((line_id, token.strip()))

                # If still no match, try to find by text in the line
                if word_id is None:
                    for word in line_words:
                        if token == word.text or token.strip() == word.text.strip():
                            word_id = word.word_id
                            break

                line_word_ids.append(word_id)
                # Get base label (without BIO prefix)
                # Use (line_id, word_id) as key since word_id is only unique within a line
                if word_id is not None:
                    base_label = ground_truth_base.get((line_id, word_id), "O")
                else:
                    base_label = "O"
                line_base_labels.append(base_label)

            # Convert base labels to BIO format per-line (matching training logic)
            # Training does: B- for first occurrence, I- for contiguous same label
            line_bio_labels: List[str] = []
            prev_base = "O"
            for base_label in line_base_labels:
                if base_label == "O":
                    line_bio_labels.append("O")
                    prev_base = "O"
                else:
                    # B- for first occurrence, I- for contiguous same label
                    bio_label = "B-" + base_label if prev_base != base_label else "I-" + base_label
                    line_bio_labels.append(bio_label)
                    prev_base = base_label

            # Now build predictions with BIO-formatted ground truth
            for token_idx, token in enumerate(line_pred.tokens):
                word_id = line_word_ids[token_idx]
                ground_truth_bio = line_bio_labels[token_idx]

                # Get prediction for this token
                if token_idx < len(line_pred.labels) and token_idx < len(line_pred.confidences):
                    pred_label = line_pred.labels[token_idx]
                    confidence = line_pred.confidences[token_idx]
                else:
                    pred_label = "O"
                    confidence = 0.0

                # Calculate correctness
                is_correct = pred_label == ground_truth_bio

                predictions.append(
                    {
                        "word_id": word_id,
                        "line_id": line_id,
                        "text": token,
                        "predicted_label": pred_label,
                        "predicted_confidence": float(confidence),
                        "ground_truth_label": ground_truth_bio if ground_truth_bio != "O" else None,
                        "is_correct": is_correct,
                    }
                )

        # Step 6: Calculate metrics
        # Build ground_truth dict with BIO labels for metrics calculation
        # Use (line_id, word_id) as key since word_id is only unique within a line
        ground_truth_bio: Dict[tuple[int, int], str] = {}
        for pred in predictions:
            word_id = pred.get("word_id")
            line_id = pred.get("line_id")
            if word_id is not None and line_id is not None:
                gt_label = pred.get("ground_truth_label")
                if gt_label:
                    ground_truth_bio[(line_id, word_id)] = gt_label
                else:
                    ground_truth_bio[(line_id, word_id)] = "O"

        logger.info("Calculating metrics")
        metrics = calculate_metrics(predictions, ground_truth_bio)

        # Step 7: Build line predictions structure
        line_predictions = []
        for line_pred in inference_result.lines:
            # Get ground truth labels for this line (in BIO format)
            line_ground_truth = []
            line_words_for_gt = [w for w in receipt_details.words if w.line_id == line_pred.line_id]
            line_words_for_gt.sort(key=lambda w: w.word_id)

            # Convert to BIO format per-line (matching training)
            line_base_labels_gt = []
            for word in line_words_for_gt:
                # Use (line_id, word_id) as key since word_id is only unique within a line
                base_label = ground_truth_base.get((line_pred.line_id, word.word_id), "O")
                line_base_labels_gt.append(base_label)

            # Convert to BIO
            prev_base_gt = "O"
            for base_label in line_base_labels_gt:
                if base_label == "O":
                    line_ground_truth.append("O")
                    prev_base_gt = "O"
                else:
                    bio_label = "B-" + base_label if prev_base_gt != base_label else "I-" + base_label
                    line_ground_truth.append(bio_label)
                    prev_base_gt = base_label

            line_predictions.append(
                {
                    "line_id": line_pred.line_id,
                    "tokens": line_pred.tokens,
                    "predicted_labels": line_pred.labels,
                    "confidences": [float(c) for c in line_pred.confidences],
                    "ground_truth_labels": line_ground_truth if line_ground_truth else None,
                }
            )

        # Step 8: Build response
        response_data = {
            "original": {
                "receipt": dict(receipt_details.receipt),
                "lines": [dict(line) for line in receipt_details.lines],
                "words": [dict(word) for word in receipt_details.words],
                "ground_truth_labels": [
                    dict(label)
                    for label in receipt_details.labels
                    if label.validation_status == ValidationStatus.VALID
                ],
                "predictions": predictions,
                "line_predictions": line_predictions,
            },
            "metrics": metrics,
            "model_info": {
                "model_name": "microsoft/layoutlm-base-uncased",  # Base model name
                "device": infer._device,
                "s3_uri": infer._s3_uri_used,
                "model_dir": infer._model_dir,
            },
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

        # Step 9: Upload to S3
        logger.info("Uploading cache to S3: %s/%s", S3_CACHE_BUCKET, CACHE_KEY)
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=CACHE_KEY,
            Body=json.dumps(response_data, default=str),
            ContentType="application/json",
        )

        logger.info(
            "Cache generation complete: receipt_id=%s, predictions=%d, accuracy=%.2f%%",
            receipt_id,
            len(predictions),
            metrics["overall_accuracy"] * 100,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cache generated successfully",
                    "receipt_id": receipt_id,
                    "predictions_count": len(predictions),
                    "accuracy": metrics["overall_accuracy"],
                }
            ),
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error generating cache: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


"""Lambda handler for generating LayoutLM inference cache.

Supports two modes:
1. EventBridge scheduled mode (legacy): Picks random receipt, stores to latest.json
2. Step Function batch mode: Processes batch of receipts, stores each to unique key
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from receipt_layoutlm import LayoutLMInference

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_CACHE_BUCKET = os.environ["S3_CACHE_BUCKET"]
LAYOUTLM_TRAINING_BUCKET = os.environ.get("LAYOUTLM_TRAINING_BUCKET")
MODEL_S3_URI = os.environ.get("MODEL_S3_URI")  # Optional override
CACHE_KEY = "layoutlm-inference-cache/latest.json"
CACHE_PREFIX = "layoutlm-inference-cache/receipts/"  # For batch mode
MODEL_DIR = "/tmp/layoutlm-model"  # Persists across warm invocations

# Initialize clients
s3_client = boto3.client("s3")

# Global model instance for warm starts
_model_instance: Optional[LayoutLMInference] = None


def _get_model() -> LayoutLMInference:
    """Get or create LayoutLM model instance (cached for warm starts)."""
    global _model_instance
    if _model_instance is None:
        logger.info("Loading LayoutLM model from S3")
        _model_instance = LayoutLMInference(
            model_dir=MODEL_DIR,
            model_s3_uri=MODEL_S3_URI,
            auto_from_bucket_env=(
                "LAYOUTLM_TRAINING_BUCKET" if LAYOUTLM_TRAINING_BUCKET else None
            ),
        )
        logger.info("Model loaded successfully. Device: %s", _model_instance._device)
    return _model_instance


def _get_base_label(bio_label: str) -> str:
    """Extract base label from BIO label.

    Args:
        bio_label: Label in BIO format (e.g., "B-MERCHANT_NAME", "I-DATE", "O")

    Returns:
        Base label without BIO prefix (e.g., "MERCHANT_NAME", "DATE", "O")
    """
    if not bio_label or bio_label == "O":
        return "O"
    if bio_label.startswith("B-") or bio_label.startswith("I-"):
        return bio_label[2:]
    return bio_label


def _combine_bio_probabilities(
    all_probs: Dict[str, float],
) -> Dict[str, float]:
    """Combine B- and I- probabilities into base label probabilities.

    Sums B-X and I-X probabilities for each base label X.
    This represents the total probability that a word belongs to category X,
    regardless of whether it's at the beginning or inside of an entity.

    Since the original 9 classes sum to 1.0, the combined 5 base labels
    will also sum to 1.0, preserving the probability distribution.

    Args:
        all_probs: Dictionary mapping BIO labels to probabilities
                  (e.g., {"B-MERCHANT_NAME": 0.8, "I-MERCHANT_NAME": 0.1, "O": 0.05})

    Returns:
        Dictionary mapping base labels to probabilities
        (e.g., {"MERCHANT_NAME": 0.9, "O": 0.05})
        Note: All probabilities will sum to 1.0
    """
    base_probs: Dict[str, float] = {}

    for bio_label, prob in all_probs.items():
        base_label = _get_base_label(bio_label)
        if base_label not in base_probs:
            base_probs[base_label] = prob
        else:
            # Sum B and I probabilities
            base_probs[base_label] += prob

    return base_probs


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
                label_true_positives[predicted] = (
                    label_true_positives.get(predicted, 0) + 1
                )
        else:
            # False positive for predicted label
            if predicted != "O":
                label_false_positives[predicted] = (
                    label_false_positives.get(predicted, 0) + 1
                )
            # False negative for actual label
            if actual != "O":
                label_false_negatives[actual] = (
                    label_false_negatives.get(actual, 0) + 1
                )

    overall_accuracy = (correct / total) if total > 0 else 0.0

    # Calculate per-label F1, precision, recall
    per_label_f1: Dict[str, float] = {}
    per_label_precision: Dict[str, float] = {}
    per_label_recall: Dict[str, float] = {}

    all_labels = (
        set(label_true_positives.keys())
        | set(label_false_positives.keys())
        | set(label_false_negatives.keys())
    )

    for label in all_labels:
        tp = label_true_positives.get(label, 0)
        fp = label_false_positives.get(label, 0)
        fn = label_false_negatives.get(label, 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * (precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

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


def handler(event, context):
    """Handle Lambda invocation - routes to appropriate handler.

    Supports two modes:
    1. EventBridge scheduled mode: Picks random receipt, stores to latest.json
    2. Step Function batch mode: Processes batch of receipts from event

    Args:
        event: Lambda event (EventBridge or Step Function)
        context: Lambda context

    Returns:
        dict: Status of cache generation
    """
    # Check if this is a batch request from Step Function
    if "receipts" in event:
        logger.info("Detected batch request, routing to batch_handler")
        return batch_handler(event, context)

    # Legacy EventBridge handler
    logger.info("Starting LayoutLM inference cache generation (legacy mode)")

    try:
        # Initialize clients
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Load model (cached in /tmp after first load)
        logger.info("Loading LayoutLM model from S3")
        infer = LayoutLMInference(
            model_dir=MODEL_DIR,
            model_s3_uri=MODEL_S3_URI,
            auto_from_bucket_env=(
                "LAYOUTLM_TRAINING_BUCKET"
                if LAYOUTLM_TRAINING_BUCKET
                else None
            ),
        )
        logger.info("Model loaded successfully. Device: %s", infer._device)

        # Step 1: Select random receipt with VALID labels
        # Get all VALID labels (any label type)
        logger.info("Fetching receipts with VALID labels")

        # Query for receipts that have at least one VALID label
        # We'll get a random receipt by querying for labels and picking one
        all_valid_labels, _ = (
            dynamo_client.list_receipt_word_labels_with_status(
                ValidationStatus.VALID, limit=100
            )
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
        receipt_details = dynamo_client.get_receipt_details(
            image_id, receipt_id
        )

        # Step 3: Run inference
        logger.info("Running LayoutLM inference")
        inference_result = infer.predict_receipt_from_dynamo(
            dynamo_client, image_id, receipt_id
        )

        # Step 4: Build ground truth mapping ((line_id, word_id) -> original and normalized labels)
        # Only use VALID labels, and normalize them to match 4-label training setup
        # Note: We store base labels (AMOUNT, DATE, etc.) without BIO prefix
        # BIO prefix will be added per-line when matching to predictions (matching training)
        # IMPORTANT: word_id is only unique within a line, so we need (line_id, word_id) as key
        ground_truth_base: Dict[tuple[int, int], str] = {}
        ground_truth_original: Dict[tuple[int, int], str] = (
            {}
        )  # Store original CORE_LABELS
        for label in receipt_details.labels:
            if label.validation_status == ValidationStatus.VALID:
                # Store original label from database (e.g., PHONE_NUMBER, TIME, LINE_TOTAL)
                ground_truth_original[(label.line_id, label.word_id)] = (
                    label.label
                )
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
                    if (
                        token == word.text
                        or token.strip() == word.text.strip()
                    ):
                        word_id = word.word_id

                # If position match failed, try text lookup
                if word_id is None:
                    word_id = word_lookup.get(
                        (line_id, token)
                    ) or word_lookup.get((line_id, token.strip()))

                # If still no match, try to find by text in the line
                if word_id is None:
                    for word in line_words:
                        if (
                            token == word.text
                            or token.strip() == word.text.strip()
                        ):
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
                    bio_label = (
                        "B-" + base_label
                        if prev_base != base_label
                        else "I-" + base_label
                    )
                    line_bio_labels.append(bio_label)
                    prev_base = base_label

            # Now build predictions with BIO-formatted ground truth
            for token_idx, token in enumerate(line_pred.tokens):
                word_id = line_word_ids[token_idx]
                ground_truth_bio = line_bio_labels[token_idx]

                # Get prediction for this token
                if token_idx < len(line_pred.labels) and token_idx < len(
                    line_pred.confidences
                ):
                    pred_label = line_pred.labels[token_idx]
                    confidence = line_pred.confidences[token_idx]
                else:
                    pred_label = "O"
                    confidence = 0.0

                # Get all class probabilities for this word
                all_probs = {}
                if line_pred.all_probabilities and token_idx < len(
                    line_pred.all_probabilities
                ):
                    all_probs = line_pred.all_probabilities[token_idx]

                # Combine B- and I- probabilities into base labels
                base_probs = _combine_bio_probabilities(all_probs)

                # Get base labels for display
                predicted_label_base = _get_base_label(pred_label)
                ground_truth_label_base = (
                    _get_base_label(ground_truth_bio)
                    if ground_truth_bio != "O"
                    else None
                )

                # Get original ground truth label (before normalization)
                # This is the original CORE_LABEL from the database (e.g., PHONE_NUMBER, TIME, LINE_TOTAL)
                ground_truth_label_original = (
                    ground_truth_original.get((line_id, word_id))
                    if word_id is not None
                    else None
                )

                # Calculate correctness (using BIO labels for accuracy)
                is_correct = pred_label == ground_truth_bio

                predictions.append(
                    {
                        "word_id": word_id,
                        "line_id": line_id,
                        "text": token,
                        # BIO labels (for correctness checking)
                        "predicted_label": pred_label,
                        "ground_truth_label": (
                            ground_truth_bio
                            if ground_truth_bio != "O"
                            else None
                        ),
                        # Base labels (for display - normalized to 4-label system)
                        "predicted_label_base": predicted_label_base,
                        "ground_truth_label_base": ground_truth_label_base,
                        # Original ground truth label (from CORE_LABELS before normalization)
                        # This shows what was actually labeled in the database
                        # Examples: PHONE_NUMBER, TIME, LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL
                        "ground_truth_label_original": ground_truth_label_original,
                        "predicted_confidence": float(confidence),
                        "is_correct": is_correct,
                        # All probabilities (BIO format - for detailed analysis)
                        "all_class_probabilities": all_probs,
                        # Base probabilities (for clean display - 5 classes instead of 9)
                        "all_class_probabilities_base": base_probs,
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
            line_words_for_gt = [
                w
                for w in receipt_details.words
                if w.line_id == line_pred.line_id
            ]
            line_words_for_gt.sort(key=lambda w: w.word_id)

            # Convert to BIO format per-line (matching training)
            line_base_labels_gt = []
            for word in line_words_for_gt:
                # Use (line_id, word_id) as key since word_id is only unique within a line
                base_label = ground_truth_base.get(
                    (line_pred.line_id, word.word_id), "O"
                )
                line_base_labels_gt.append(base_label)

            # Convert to BIO
            prev_base_gt = "O"
            for base_label in line_base_labels_gt:
                if base_label == "O":
                    line_ground_truth.append("O")
                    prev_base_gt = "O"
                else:
                    bio_label = (
                        "B-" + base_label
                        if prev_base_gt != base_label
                        else "I-" + base_label
                    )
                    line_ground_truth.append(bio_label)
                    prev_base_gt = base_label

            line_predictions.append(
                {
                    "line_id": line_pred.line_id,
                    "tokens": line_pred.tokens,
                    "predicted_labels": line_pred.labels,
                    "confidences": [float(c) for c in line_pred.confidences],
                    "ground_truth_labels": (
                        line_ground_truth if line_ground_truth else None
                    ),
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


def _extract_entities_summary(
    predictions: List[Dict[str, Any]],
) -> Dict[str, Optional[str]]:
    """Extract consolidated entity values from predictions.

    Groups consecutive tokens by label and joins their text.

    Args:
        predictions: List of prediction dicts with predicted_label_base and text

    Returns:
        Dict with merchant_name, date, address, amount (each may be None)
    """
    # Group consecutive tokens by label
    entities: Dict[str, List[str]] = {
        "MERCHANT_NAME": [],
        "DATE": [],
        "ADDRESS": [],
        "AMOUNT": [],
    }

    current_label = None
    current_tokens: List[str] = []

    for pred in predictions:
        label = pred.get("predicted_label_base", "O")

        if label == "O":
            # Flush current tokens if any
            if current_label and current_tokens:
                entities[current_label].append(" ".join(current_tokens))
            current_label = None
            current_tokens = []
        elif label == current_label:
            # Continue building current entity
            current_tokens.append(pred.get("text", ""))
        else:
            # New label - flush previous and start new
            if current_label and current_tokens:
                entities[current_label].append(" ".join(current_tokens))
            current_label = label
            current_tokens = [pred.get("text", "")]

    # Flush final tokens
    if current_label and current_tokens:
        entities[current_label].append(" ".join(current_tokens))

    # Take the longest/best entity for each type
    def best_entity(values: List[str]) -> Optional[str]:
        if not values:
            return None
        # Return the longest entity (usually most complete)
        return max(values, key=len)

    return {
        "merchant_name": best_entity(entities["MERCHANT_NAME"]),
        "date": best_entity(entities["DATE"]),
        "address": best_entity(entities["ADDRESS"]),
        "amount": best_entity(entities["AMOUNT"]),
    }


def _process_single_receipt(
    dynamo_client: DynamoClient,
    infer: LayoutLMInference,
    image_id: str,
    receipt_id: int,
) -> Dict[str, Any]:
    """Process a single receipt and return inference results.

    Args:
        dynamo_client: DynamoDB client instance
        infer: LayoutLM inference model
        image_id: Image ID
        receipt_id: Receipt ID

    Returns:
        Dict containing receipt inference data ready for S3 storage
    """
    logger.info("Processing receipt: image_id=%s, receipt_id=%s", image_id, receipt_id)

    # Get receipt details
    receipt_details = dynamo_client.get_receipt_details(image_id, receipt_id)

    # Time the inference
    start_time = time.perf_counter()
    inference_result = infer.predict_receipt_from_dynamo(
        dynamo_client, image_id, receipt_id
    )
    inference_time_ms = (time.perf_counter() - start_time) * 1000

    # Build ground truth mapping
    ground_truth_base: Dict[tuple[int, int], str] = {}
    ground_truth_original: Dict[tuple[int, int], str] = {}
    for label in receipt_details.labels:
        if label.validation_status == ValidationStatus.VALID:
            ground_truth_original[(label.line_id, label.word_id)] = label.label
            normalized = _normalize_label_for_4label_setup(label.label)
            ground_truth_base[(label.line_id, label.word_id)] = normalized

    # Build word lookups
    word_lookup: Dict[tuple[int, str], int] = {}
    words_by_line: Dict[int, List[Any]] = {}
    for word in receipt_details.words:
        word_lookup[(word.line_id, word.text)] = word.word_id
        word_lookup[(word.line_id, word.text.strip())] = word.word_id
        words_by_line.setdefault(word.line_id, []).append(word)

    for line_id in words_by_line:
        words_by_line[line_id].sort(key=lambda w: w.word_id)

    # Build predictions
    predictions: List[Dict[str, Any]] = []

    for line_pred in inference_result.lines:
        line_id = line_pred.line_id
        line_words = words_by_line.get(line_id, [])

        line_word_ids: List[int] = []
        line_base_labels: List[str] = []

        for token_idx, token in enumerate(line_pred.tokens):
            word_id = None
            if token_idx < len(line_words):
                word = line_words[token_idx]
                if token == word.text or token.strip() == word.text.strip():
                    word_id = word.word_id

            if word_id is None:
                word_id = word_lookup.get(
                    (line_id, token)
                ) or word_lookup.get((line_id, token.strip()))

            if word_id is None:
                for word in line_words:
                    if token == word.text or token.strip() == word.text.strip():
                        word_id = word.word_id
                        break

            line_word_ids.append(word_id)
            base_label = (
                ground_truth_base.get((line_id, word_id), "O")
                if word_id is not None
                else "O"
            )
            line_base_labels.append(base_label)

        # Convert to BIO format
        line_bio_labels: List[str] = []
        prev_base = "O"
        for base_label in line_base_labels:
            if base_label == "O":
                line_bio_labels.append("O")
                prev_base = "O"
            else:
                bio_label = (
                    "B-" + base_label if prev_base != base_label else "I-" + base_label
                )
                line_bio_labels.append(bio_label)
                prev_base = base_label

        # Build prediction entries
        for token_idx, token in enumerate(line_pred.tokens):
            word_id = line_word_ids[token_idx]
            ground_truth_bio = line_bio_labels[token_idx]

            pred_label = (
                line_pred.labels[token_idx]
                if token_idx < len(line_pred.labels)
                else "O"
            )
            confidence = (
                line_pred.confidences[token_idx]
                if token_idx < len(line_pred.confidences)
                else 0.0
            )

            all_probs = {}
            if line_pred.all_probabilities and token_idx < len(
                line_pred.all_probabilities
            ):
                all_probs = line_pred.all_probabilities[token_idx]

            base_probs = _combine_bio_probabilities(all_probs)
            predicted_label_base = _get_base_label(pred_label)
            ground_truth_label_base = (
                _get_base_label(ground_truth_bio)
                if ground_truth_bio != "O"
                else None
            )
            ground_truth_label_original = (
                ground_truth_original.get((line_id, word_id))
                if word_id is not None
                else None
            )
            is_correct = pred_label == ground_truth_bio

            predictions.append(
                {
                    "word_id": word_id,
                    "line_id": line_id,
                    "text": token,
                    "predicted_label": pred_label,
                    "ground_truth_label": (
                        ground_truth_bio if ground_truth_bio != "O" else None
                    ),
                    "predicted_label_base": predicted_label_base,
                    "ground_truth_label_base": ground_truth_label_base,
                    "ground_truth_label_original": ground_truth_label_original,
                    "predicted_confidence": float(confidence),
                    "is_correct": is_correct,
                    "all_class_probabilities": all_probs,
                    "all_class_probabilities_base": base_probs,
                }
            )

    # Calculate metrics
    ground_truth_bio_dict: Dict[tuple[int, int], str] = {}
    for pred in predictions:
        word_id = pred.get("word_id")
        line_id = pred.get("line_id")
        if word_id is not None and line_id is not None:
            gt_label = pred.get("ground_truth_label")
            ground_truth_bio_dict[(line_id, word_id)] = gt_label if gt_label else "O"

    metrics = calculate_metrics(predictions, ground_truth_bio_dict)

    # Extract entity summary
    entities_summary = _extract_entities_summary(predictions)

    # Build response
    return {
        "receipt_id": f"{image_id}_{receipt_id}",
        "original": {
            "receipt": dict(receipt_details.receipt),
            "words": [dict(word) for word in receipt_details.words],
            "predictions": predictions,
        },
        "metrics": metrics,
        "model_info": {
            "model_name": "microsoft/layoutlm-base-uncased",
            "device": infer._device,
            "s3_uri": infer._s3_uri_used,
        },
        "entities_summary": entities_summary,
        "inference_time_ms": round(inference_time_ms, 2),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }


def batch_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Handle Step Function batch inference request.

    Processes a batch of receipts from the Step Function Map state.

    Args:
        event: Step Function input containing:
            - receipts: List of {image_id, receipt_id} dicts
        _context: Lambda context (unused)

    Returns:
        dict: Results of batch processing
    """
    logger.info("Starting batch inference")
    logger.info("Event: %s", json.dumps(event))

    receipts = event.get("receipts", [])
    if not receipts:
        logger.warning("No receipts provided in batch")
        return {
            "processed": 0,
            "failed": 0,
            "results": [],
        }

    try:
        # Initialize clients
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)
        infer = _get_model()

        results = []
        processed = 0
        failed = 0

        for receipt_info in receipts:
            image_id = receipt_info.get("image_id")
            receipt_id = receipt_info.get("receipt_id")

            if not image_id or receipt_id is None:
                logger.warning("Invalid receipt info: %s", receipt_info)
                failed += 1
                continue

            try:
                # Process the receipt
                result = _process_single_receipt(
                    dynamo_client, infer, image_id, receipt_id
                )

                # Store to S3 with unique key
                cache_key = f"{CACHE_PREFIX}receipt-{image_id}-{receipt_id}.json"
                s3_client.put_object(
                    Bucket=S3_CACHE_BUCKET,
                    Key=cache_key,
                    Body=json.dumps(result, default=str),
                    ContentType="application/json",
                )

                logger.info(
                    "Cached receipt %s_%s: accuracy=%.2f%%, time=%.0fms",
                    image_id,
                    receipt_id,
                    result["metrics"]["overall_accuracy"] * 100,
                    result["inference_time_ms"],
                )

                results.append(
                    {
                        "receipt_id": f"{image_id}_{receipt_id}",
                        "cache_key": cache_key,
                        "accuracy": result["metrics"]["overall_accuracy"],
                        "inference_time_ms": result["inference_time_ms"],
                        "status": "success",
                    }
                )
                processed += 1

            except Exception as e:
                logger.error(
                    "Error processing receipt %s_%s: %s",
                    image_id,
                    receipt_id,
                    e,
                    exc_info=True,
                )
                results.append(
                    {
                        "receipt_id": f"{image_id}_{receipt_id}",
                        "status": "failed",
                        "error": str(e),
                    }
                )
                failed += 1

        logger.info("Batch complete: processed=%d, failed=%d", processed, failed)

        return {
            "processed": processed,
            "failed": failed,
            "results": results,
        }

    except Exception as e:
        logger.error("Error in batch handler: %s", e, exc_info=True)
        return {
            "processed": 0,
            "failed": len(receipts),
            "error": str(e),
        }

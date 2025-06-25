"""Metrics utilities for NER evaluation.

This module provides functions for calculating NER-specific metrics, including:
- Entity-level F1, precision, and recall
- Entity-specific class accuracy
- Span-based metrics for multi-token entities
"""

from typing import Dict, List, Set, Tuple, Any, Optional
import numpy as np
from collections import defaultdict
from sklearn.metrics import (
    classification_report,
    f1_score,
    precision_score,
    recall_score,
)


def extract_entities(labels: List[str]) -> List[Tuple[str, int, int]]:
    """Extract entity spans from a sequence of IOB tags.

    Args:
        labels: List of IOB tags (e.g. ['O', 'B-address', 'I-address', 'O'])

    Returns:
        List of (entity_type, start_idx, end_idx) tuples
    """
    entities = []
    current_entity = None
    current_start = None

    for i, label in enumerate(labels):
        if label == "O":
            # If we were tracking an entity and now hit 'O', close the entity
            if current_entity:
                entities.append((current_entity, current_start, i))
                current_entity = None
                current_start = None
        elif label.startswith("B-"):
            # If we were tracking an entity, close it
            if current_entity:
                entities.append((current_entity, current_start, i))

            # Start a new entity
            current_entity = label[2:]  # Remove 'B-' prefix
            current_start = i
        elif label.startswith("I-"):
            entity_type = label[2:]  # Remove 'I-' prefix

            # If this I- tag doesn't match current entity, handle as error
            if not current_entity:
                # I- tag without preceding B-tag; treat as B-
                current_entity = entity_type
                current_start = i
            elif entity_type != current_entity:
                # I- tag for different entity; close current and start new
                entities.append((current_entity, current_start, i))
                current_entity = entity_type
                current_start = i

    # Handle entity at the end of the sequence
    if current_entity:
        entities.append((current_entity, current_start, len(labels)))

    return entities


def entity_level_metrics(
    true_labels: List[str], pred_labels: List[str]
) -> Dict[str, Dict[str, float]]:
    """Calculate entity-level metrics for NER evaluation.

    Args:
        true_labels: Gold standard labels in IOB format
        pred_labels: Predicted labels in IOB format

    Returns:
        Dictionary with entity-level metrics by entity type
    """
    # Extract entity spans
    true_entities = extract_entities(true_labels)
    pred_entities = extract_entities(pred_labels)

    # Group entities by type
    true_entities_by_type = defaultdict(list)
    pred_entities_by_type = defaultdict(list)

    for entity_type, start, end in true_entities:
        true_entities_by_type[entity_type].append((start, end))

    for entity_type, start, end in pred_entities:
        pred_entities_by_type[entity_type].append((start, end))

    # Combine all entity types
    all_entity_types = set(true_entities_by_type.keys()) | set(
        pred_entities_by_type.keys()
    )

    # Calculate metrics for each entity type
    metrics = {}
    overall_tp = 0
    overall_fp = 0
    overall_fn = 0

    for entity_type in all_entity_types:
        true_spans = set(true_entities_by_type[entity_type])
        pred_spans = set(pred_entities_by_type[entity_type])

        # Calculate true positives, false positives, false negatives
        tp = len(true_spans & pred_spans)
        fp = len(pred_spans - true_spans)
        fn = len(true_spans - pred_spans)

        # Update overall counts
        overall_tp += tp
        overall_fp += fp
        overall_fn += fn

        # Calculate precision, recall, F1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        metrics[entity_type] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": len(true_spans),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
        }

    # Calculate overall micro-avg metrics across all entity types
    precision = (
        overall_tp / (overall_tp + overall_fp) if (overall_tp + overall_fp) > 0 else 0.0
    )
    recall = (
        overall_tp / (overall_tp + overall_fn) if (overall_tp + overall_fn) > 0 else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    metrics["overall"] = {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "support": sum(len(spans) for spans in true_entities_by_type.values()),
        "true_positives": overall_tp,
        "false_positives": overall_fp,
        "false_negatives": overall_fn,
    }

    return metrics


def entity_class_accuracy(
    true_labels: List[str], pred_labels: List[str]
) -> Dict[str, float]:
    """Calculate per-class accuracy at the entity level.

    This measures how many entities of each type were correctly identified
    with the exact same boundaries.

    Args:
        true_labels: Gold standard labels in IOB format
        pred_labels: Predicted labels in IOB format

    Returns:
        Dictionary with accuracy per entity type
    """
    # Extract entity spans
    true_entities = extract_entities(true_labels)
    pred_entities = extract_entities(pred_labels)

    # Group true entities by type for counting
    true_entities_by_type = defaultdict(list)
    for entity_type, start, end in true_entities:
        true_entities_by_type[entity_type].append((start, end))

    # Count exact matches by entity type
    correct_by_type = defaultdict(int)
    pred_entities_set = {(t, s, e) for t, s, e in pred_entities}

    for entity_type, start, end in true_entities:
        if (entity_type, start, end) in pred_entities_set:
            correct_by_type[entity_type] += 1

    # Calculate accuracy for each entity type
    accuracy_by_type = {}
    for entity_type, spans in true_entities_by_type.items():
        total = len(spans)
        correct = correct_by_type[entity_type]
        accuracy_by_type[entity_type] = correct / total if total > 0 else 0.0

    # Overall accuracy
    total_entities = len(true_entities)
    total_correct = sum(correct_by_type.values())
    accuracy_by_type["overall"] = (
        total_correct / total_entities if total_entities > 0 else 0.0
    )

    return accuracy_by_type


def token_to_entity_metrics(
    token_true: List[str], token_pred: List[str]
) -> Dict[str, Dict[str, float]]:
    """Convert token classification results to entity-level metrics.

    This function takes token-level predictions and gold standards,
    and computes both token-level and entity-level metrics.

    Args:
        token_true: Gold standard token labels
        token_pred: Predicted token labels

    Returns:
        Dictionary containing both token-level and entity-level metrics
    """
    # Calculate token-level metrics using sklearn
    token_report = classification_report(
        token_true, token_pred, output_dict=True, zero_division=0
    )

    # Calculate entity-level metrics
    entity_metrics = entity_level_metrics(token_true, token_pred)
    entity_accuracy = entity_class_accuracy(token_true, token_pred)

    # Combine metrics
    combined_metrics = {
        "token_level": token_report,
        "entity_level": {
            "metrics": entity_metrics,
            "accuracy": entity_accuracy,
        },
    }

    return combined_metrics


def confusion_matrix_entities(
    true_labels: List[str], pred_labels: List[str]
) -> Dict[str, Dict[str, int]]:
    """Create a confusion matrix for entity types.

    Args:
        true_labels: Gold standard labels in IOB format
        pred_labels: Predicted labels in IOB format

    Returns:
        Dictionary representation of the confusion matrix
    """
    # Extract entity spans
    true_entities = extract_entities(true_labels)
    pred_entities = extract_entities(pred_labels)

    # Create dictionary mapping spans to types in true and pred
    true_span_to_type = {
        (start, end): entity_type for entity_type, start, end in true_entities
    }
    pred_span_to_type = {
        (start, end): entity_type for entity_type, start, end in pred_entities
    }

    # All unique spans (true and predicted)
    all_spans = set(true_span_to_type.keys()) | set(pred_span_to_type.keys())

    # All unique entity types
    all_types = set(true_span_to_type.values()) | set(pred_span_to_type.values())
    all_types = sorted(list(all_types))

    # Initialize confusion matrix
    confusion = defaultdict(lambda: defaultdict(int))

    # Fill confusion matrix
    for span in all_spans:
        true_type = true_span_to_type.get(span, "O")
        pred_type = pred_span_to_type.get(span, "O")
        confusion[true_type][pred_type] += 1

    # Convert to regular dictionary for serialization
    return {t: dict(confusion[t]) for t in confusion}


def field_extraction_accuracy(
    true_labels: List[str],
    pred_labels: List[str],
    tokens: List[str],
    receipt_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """Calculate accuracy for specific receipt fields.

    Args:
        true_labels: Gold standard labels in IOB format
        pred_labels: Predicted labels in IOB format
        tokens: The actual token text
        receipt_fields: Optional dictionary with known correct values

    Returns:
        Dictionary with accuracy for key receipt fields
    """
    # Get entity spans
    true_entities = extract_entities(true_labels)
    pred_entities = extract_entities(pred_labels)

    # Extract text for important fields
    true_fields = {}
    for entity_type, start, end in true_entities:
        if entity_type not in true_fields:
            true_fields[entity_type] = " ".join(tokens[start:end])

    pred_fields = {}
    for entity_type, start, end in pred_entities:
        if entity_type not in pred_fields:
            pred_fields[entity_type] = " ".join(tokens[start:end])

    # Compare extracted fields
    field_accuracy = {}

    # Priority fields for receipts
    key_fields = [
        "store_name",
        "date",
        "total_amount",
        "address",
        "phone_number",
    ]

    for field in key_fields:
        true_value = true_fields.get(field, "")
        pred_value = pred_fields.get(field, "")

        # Exact match
        field_accuracy[f"{field}_exact"] = 1.0 if true_value == pred_value else 0.0

        # Character overlap (for partial matches)
        max_len = max(len(true_value), len(pred_value))
        if max_len > 0:
            # Simple character-level similarity
            shared_chars = sum(1 for c in true_value if c in pred_value)
            field_accuracy[f"{field}_char_overlap"] = shared_chars / max_len
        else:
            field_accuracy[f"{field}_char_overlap"] = 0.0

    return field_accuracy


def compute_all_ner_metrics(
    true_labels: List[str],
    pred_labels: List[str],
    tokens: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compute all NER metrics in a single call.

    Args:
        true_labels: Gold standard labels in IOB format
        pred_labels: Predicted labels in IOB format
        tokens: Optional token texts for field extraction accuracy

    Returns:
        Dictionary with all NER metrics
    """
    metrics = {}

    # Token classification metrics
    metrics["token_classification"] = classification_report(
        true_labels, pred_labels, output_dict=True, zero_division=0
    )

    # Entity-level metrics
    metrics["entity_level"] = entity_level_metrics(true_labels, pred_labels)

    # Entity class accuracy
    metrics["entity_accuracy"] = entity_class_accuracy(true_labels, pred_labels)

    # Confusion matrix for entities
    metrics["entity_confusion_matrix"] = confusion_matrix_entities(
        true_labels, pred_labels
    )

    # Field extraction accuracy (if tokens provided)
    if tokens:
        metrics["field_extraction"] = field_extraction_accuracy(
            true_labels, pred_labels, tokens
        )

    return metrics

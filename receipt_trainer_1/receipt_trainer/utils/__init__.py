"""Utility functions for Receipt Trainer."""

from receipt_trainer.utils.aws import get_dynamo_table, load_env
from receipt_trainer.utils.data import (
    create_sliding_windows,
    process_receipt_details,
)
from receipt_trainer.utils.metrics import (
    compute_all_ner_metrics,
    confusion_matrix_entities,
    entity_class_accuracy,
    entity_level_metrics,
    field_extraction_accuracy,
)

__all__ = [
    "process_receipt_details",
    "create_sliding_windows",
    "load_env",
    "get_dynamo_table",
    # Entity-level metrics exports
    "entity_level_metrics",
    "entity_class_accuracy",
    "compute_all_ner_metrics",
    "field_extraction_accuracy",
    "confusion_matrix_entities",
]

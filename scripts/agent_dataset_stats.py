#!/usr/bin/env python3
"""Get current dataset statistics for AI agent.

Usage:
    python scripts/agent_dataset_stats.py [table_name] [output_file]

Example:
    python scripts/agent_dataset_stats.py ReceiptsTable-dc5be22 dataset_stats.json
"""

import json
import sys
from typing import Any, Dict

from receipt_layoutlm.receipt_layoutlm.data_loader import CORE_LABELS

from receipt_dynamo import DynamoClient


def get_dataset_stats(table_name: str, output_file: str = None) -> Dict[str, Any]:
    """Get dataset statistics and output JSON for agent.

    Args:
        table_name: DynamoDB table name
        output_file: Optional file path to save JSON output

    Returns:
        Dictionary with dataset statistics
    """
    dynamo = DynamoClient(table_name=table_name)

    label_counts = {}
    for label in CORE_LABELS.keys():
        count = dynamo.count_labels_by_category_and_status(label, "VALID")
        label_counts[label] = count

    receipt_count = dynamo.count_receipts()
    total_valid_labels = sum(label_counts.values())

    # Calculate distribution
    label_distribution = {
        label: count / total_valid_labels if total_valid_labels > 0 else 0.0
        for label, count in label_counts.items()
    }

    # Identify class imbalance
    entity_labels = {k: v for k, v in label_counts.items() if v > 0}
    if entity_labels:
        max_count = max(entity_labels.values())
        min_count = min(entity_labels.values())
        imbalance_ratio = max_count / min_count if min_count > 0 else float("inf")
    else:
        imbalance_ratio = 0.0

    stats = {
        "total_receipts": receipt_count,
        "label_counts": label_counts,
        "total_valid_labels": total_valid_labels,
        "label_distribution": label_distribution,
        "class_imbalance": {
            "ratio": imbalance_ratio,
            "max_label": (
                max(entity_labels.items(), key=lambda x: x[1])[0]
                if entity_labels
                else None
            ),
            "min_label": (
                min(entity_labels.items(), key=lambda x: x[1])[0]
                if entity_labels
                else None
            ),
        },
        "available_labels": list(CORE_LABELS.keys()),
        "labels_with_data": [
            label for label, count in label_counts.items() if count > 0
        ],
        "labels_without_data": [
            label for label, count in label_counts.items() if count == 0
        ],
    }

    if output_file:
        with open(output_file, "w") as f:
            json.dump(stats, f, indent=2)
        print(f"✅ Dataset statistics saved to {output_file}")
    else:
        print(json.dumps(stats, indent=2))

    return stats


if __name__ == "__main__":
    table_name = sys.argv[1] if len(sys.argv) > 1 else "ReceiptsTable-dc5be22"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    get_dataset_stats(table_name, output_file)

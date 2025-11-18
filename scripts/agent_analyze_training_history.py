#!/usr/bin/env python3
"""Analyze training history and provide insights for AI agent.

Usage:
    python scripts/agent_analyze_training_history.py [table_name] [output_file]

Example:
    python scripts/agent_analyze_training_history.py ReceiptsTable-dc5be22 training_history.json
"""

from receipt_dynamo import DynamoClient
import json
import sys
from typing import Dict, List, Any


def analyze_training_history(table_name: str, output_file: str = None) -> Dict[str, Any]:
    """Analyze all training jobs and output JSON for agent.

    Args:
        table_name: DynamoDB table name
        output_file: Optional file path to save JSON output

    Returns:
        Dictionary with training history analysis
    """
    dynamo = DynamoClient(table_name=table_name)

    jobs = dynamo.list_jobs_by_status("succeeded")
    history = []

    for job in jobs:
        metrics = dynamo.list_job_metrics(job.job_id)
        if not metrics:
            continue

        best_metric = max(metrics, key=lambda m: m.value)
        epoch_metrics = sorted(metrics, key=lambda m: m.epoch or 0)

        # Extract key hyperparameters from job_config
        config = job.job_config or {}
        hyperparams = {
            "batch_size": config.get("batch_size"),
            "learning_rate": config.get("learning_rate"),
            "epochs": config.get("epochs"),
            "warmup_ratio": config.get("warmup_ratio"),
            "label_smoothing": config.get("label_smoothing"),
            "o_entity_ratio": config.get("o_entity_ratio"),
            "merge_amounts": config.get("merge_amounts"),
            "allowed_labels": config.get("allowed_labels"),
        }

        history.append({
            "job_id": job.job_id,
            "job_name": job.name,
            "created_at": job.created_at,
            "config": hyperparams,
            "best_f1": best_metric.value,
            "best_epoch": best_metric.epoch,
            "total_epochs": len(epoch_metrics),
            "epochs": [
                {
                    "epoch": m.epoch,
                    "f1": m.value,
                    "timestamp": m.timestamp
                }
                for m in epoch_metrics
            ]
        })

    # Sort by best F1 descending
    history.sort(key=lambda x: x["best_f1"], reverse=True)

    # Calculate statistics
    if history:
        best_run = history[0]
        avg_f1 = sum(r["best_f1"] for r in history) / len(history)

        # Group by batch size
        batch_size_groups: Dict[int, List[float]] = {}
        for run in history:
            batch_size = run["config"].get("batch_size")
            if batch_size:
                if batch_size not in batch_size_groups:
                    batch_size_groups[batch_size] = []
                batch_size_groups[batch_size].append(run["best_f1"])

        batch_size_stats = {
            batch: {
                "avg_f1": sum(f1s) / len(f1s),
                "max_f1": max(f1s),
                "count": len(f1s)
            }
            for batch, f1s in batch_size_groups.items()
        }
    else:
        best_run = None
        avg_f1 = 0.0
        batch_size_stats = {}

    output = {
        "total_runs": len(history),
        "best_f1": best_run["best_f1"] if best_run else None,
        "best_run_job_id": best_run["job_id"] if best_run else None,
        "average_f1": avg_f1,
        "batch_size_analysis": batch_size_stats,
        "runs": history
    }

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"✅ Training history saved to {output_file}")
    else:
        print(json.dumps(output, indent=2))

    return output


if __name__ == "__main__":
    table_name = sys.argv[1] if len(sys.argv) > 1 else "ReceiptsTable-dc5be22"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    analyze_training_history(table_name, output_file)


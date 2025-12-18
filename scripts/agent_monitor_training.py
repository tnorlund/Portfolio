#!/usr/bin/env python3
"""Monitor active training job for AI agent.

Usage:
    python scripts/agent_monitor_training.py [table_name] [output_file]

Example:
    python scripts/agent_monitor_training.py ReceiptsTable-dc5be22 active_training.json
"""

import json
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo import DynamoClient


def monitor_active_training(table_name: str, output_file: str = None) -> Dict[str, Any]:
    """Get status of currently running training job.

    Args:
        table_name: DynamoDB table name
        output_file: Optional file path to save JSON output

    Returns:
        Dictionary with active training status
    """
    dynamo = DynamoClient(table_name=table_name)

    running_jobs = dynamo.list_jobs_by_status("running")

    if not running_jobs:
        status: Dict[str, Any] = {
            "status": "no_active_jobs",
            "message": "No training jobs currently running",
        }
    else:
        job = running_jobs[0]  # Assume one active job at a time
        metrics = dynamo.list_job_metrics(job.job_id)
        logs = dynamo.list_job_logs(job.job_id, limit=20)

        latest_metric = max(metrics, key=lambda m: m.epoch or 0) if metrics else None

        # Extract key hyperparameters
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

        # Calculate training progress
        total_epochs = hyperparams.get("epochs", 0)
        current_epoch = latest_metric.epoch if latest_metric else 0
        progress_pct = (current_epoch / total_epochs * 100) if total_epochs > 0 else 0.0

        # Analyze F1 trend
        epoch_metrics = sorted(metrics, key=lambda m: m.epoch or 0)
        f1_trend = "increasing" if len(epoch_metrics) >= 2 else "unknown"
        if len(epoch_metrics) >= 2:
            recent_f1 = epoch_metrics[-1].value
            previous_f1 = epoch_metrics[-2].value
            if recent_f1 > previous_f1:
                f1_trend = "increasing"
            elif recent_f1 < previous_f1:
                f1_trend = "decreasing"
            else:
                f1_trend = "stable"

        status = {
            "job_id": job.job_id,
            "job_name": job.name,
            "status": job.status,
            "started_at": job.created_at,
            "config": hyperparams,
            "progress": {
                "current_epoch": current_epoch,
                "total_epochs": total_epochs,
                "progress_percent": progress_pct,
            },
            "latest_metrics": {
                "epoch": latest_metric.epoch if latest_metric else None,
                "f1": latest_metric.value if latest_metric else None,
                "timestamp": (latest_metric.timestamp if latest_metric else None),
            },
            "f1_trend": f1_trend,
            "all_epochs": [
                {"epoch": m.epoch, "f1": m.value, "timestamp": m.timestamp}
                for m in sorted(epoch_metrics, key=lambda m: m.epoch or 0)
            ],
            "recent_logs": [log.message for log in logs[-10:]] if logs else [],
        }

    if output_file:
        with open(output_file, "w") as f:
            json.dump(status, f, indent=2)
        print(f"✅ Active training status saved to {output_file}")
    else:
        print(json.dumps(status, indent=2))

    return status


if __name__ == "__main__":
    table_name = sys.argv[1] if len(sys.argv) > 1 else "ReceiptsTable-dc5be22"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    monitor_active_training(table_name, output_file)

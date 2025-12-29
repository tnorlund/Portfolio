#!/usr/bin/env python3
"""CLI tool to query training job status and metrics from DynamoDB."""

import argparse
import json
import sys
from datetime import datetime
from typing import Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_metric import JobMetric
from receipt_dynamo.entities.job_log import JobLog


def get_client(table_name: str, region: str = "us-east-1") -> DynamoClient:
    """Create a DynamoDB client."""
    return DynamoClient(table_name=table_name, region=region)


def list_jobs(client: DynamoClient, status: Optional[str] = None, limit: int = 10):
    """List jobs, optionally filtered by status."""
    if status:
        jobs, _ = client.list_jobs_by_status(status, limit=limit)
    else:
        # List recent jobs by querying each status
        all_jobs = []
        for s in ["running", "pending", "succeeded", "failed"]:
            jobs, _ = client.list_jobs_by_status(s, limit=limit)
            all_jobs.extend(jobs)
        jobs = sorted(all_jobs, key=lambda j: j.created_at, reverse=True)[:limit]

    return jobs


def get_job_by_name(client: DynamoClient, name: str) -> Optional[Job]:
    """Get a job by name using GSI2."""
    try:
        jobs, _ = client.get_job_by_name(name, limit=1)
        return jobs[0] if jobs else None
    except Exception as e:
        print(f"Error querying by name (GSI2 may not exist): {e}", file=sys.stderr)
        # Fallback: scan for the job
        for status in ["running", "pending", "succeeded", "failed"]:
            jobs, _ = client.list_jobs_by_status(status, limit=100)
            for job in jobs:
                if job.name == name:
                    return job
        return None


def get_job_metrics(client: DynamoClient, job_id: str) -> list[JobMetric]:
    """Get all metrics for a job."""
    metrics, _ = client.list_job_metrics(job_id)
    return metrics


def get_job_logs(client: DynamoClient, job_id: str) -> list[JobLog]:
    """Get all logs for a job."""
    logs, _ = client.list_job_logs(job_id)
    return logs


def get_job_config(logs: list[JobLog]) -> dict | None:
    """Extract run config from job logs."""
    for log in logs:
        try:
            msg = json.loads(log.message)
            if msg.get("type") == "run_config":
                return msg.get("data", {})
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def format_job(job: Job) -> str:
    """Format a job for display."""
    lines = [
        f"Job: {job.name}",
        f"  ID: {job.job_id}",
        f"  Status: {job.status}",
        f"  Created: {job.created_at}",
        f"  Created By: {job.created_by}",
    ]
    # results attribute may not exist in older versions
    results = getattr(job, "results", None)
    if results:
        lines.append(f"  Results: {json.dumps(results, indent=4)}")
    return "\n".join(lines)


def format_config(config: dict) -> str:
    """Format job config for display."""
    if not config:
        return "  No config found"

    lines = []

    # Training config
    tc = config.get("training_config", {})
    if tc:
        lines.append("\n  Training Config:")
        lines.append(f"    Model: {tc.get('pretrained_model_name', 'N/A')}")
        lines.append(f"    Batch Size: {tc.get('batch_size', 'N/A')}")
        lines.append(f"    Learning Rate: {tc.get('learning_rate', 'N/A')}")
        lines.append(f"    Epochs: {tc.get('epochs', 'N/A')}")
        lines.append(f"    Early Stopping Patience: {tc.get('early_stopping_patience', 'N/A')}")

    # Data config
    dc = config.get("data_config", {})
    if dc:
        lines.append("\n  Data Config:")
        lines.append(f"    Merge Amounts: {dc.get('merge_amounts', False)}")
        lines.append(f"    Allowed Labels: {dc.get('allowed_labels', 'all')}")
        lines.append(f"    Max Seq Length: {dc.get('max_seq_length', 'N/A')}")

    # Label list
    label_list = config.get("label_list", [])
    if label_list:
        # Filter to just entity labels (not O, B-, I-)
        entity_labels = sorted(set(
            l.replace("B-", "").replace("I-", "")
            for l in label_list if l != "O"
        ))
        lines.append(f"\n  Entity Labels ({len(entity_labels)}):")
        lines.append(f"    {', '.join(entity_labels)}")

    # Dataset counts with label distribution
    dataset_counts = config.get("dataset_counts", {})
    if dataset_counts:
        lines.append("\n  Dataset Label Distribution:")

        for split in ["train", "validation"]:
            if split not in dataset_counts:
                continue

            stats = dataset_counts[split]
            label_counts = stats.get("label_counts", {})

            lines.append(f"\n    {split.upper()}:")
            lines.append(f"      Lines: {stats.get('num_lines', 'N/A')}")
            lines.append(f"      Entity Tokens: {stats.get('num_entity_tokens', 'N/A')}")
            lines.append(f"      O Tokens: {stats.get('num_o_tokens', 'N/A')}")
            lines.append(f"      O:Entity Ratio: {stats.get('o_entity_ratio', 'N/A'):.2f}")

            if label_counts:
                lines.append(f"\n      Label Counts:")
                # Sort by count descending
                sorted_labels = sorted(label_counts.items(), key=lambda x: x[1], reverse=True)
                for label, count in sorted_labels:
                    # Strip B-/I- prefix for cleaner display
                    clean_label = label.replace("B-", "").replace("I-", "")
                    prefix = "B" if label.startswith("B-") else "I"
                    lines.append(f"        {prefix}-{clean_label:<20} {count:>6}")

    return "\n".join(lines)


def format_metrics_summary(metrics: list[JobMetric]) -> str:
    """Format metrics into a readable summary."""
    if not metrics:
        return "  No metrics found"

    lines = []

    # Group metrics by type
    epoch_metrics = {}  # epoch -> {metric_name: value}
    dataset_metrics = {}  # metric_name -> value
    label_metrics = {}  # epoch -> label -> {metric_type: value}

    for m in metrics:
        if m.epoch is not None:
            epoch = int(m.epoch)
            if m.metric_name.startswith("label_"):
                # Per-label metric: label_MERCHANT_NAME_f1
                parts = m.metric_name.split("_")
                metric_type = parts[-1]  # f1, precision, recall, support
                label_name = "_".join(parts[1:-1])

                if epoch not in label_metrics:
                    label_metrics[epoch] = {}
                if label_name not in label_metrics[epoch]:
                    label_metrics[epoch][label_name] = {}
                label_metrics[epoch][label_name][metric_type] = m.value
            else:
                if epoch not in epoch_metrics:
                    epoch_metrics[epoch] = {}
                epoch_metrics[epoch][m.metric_name] = m.value
        else:
            dataset_metrics[m.metric_name] = m.value

    # Dataset metrics
    if dataset_metrics:
        lines.append("\n  Dataset Metrics:")
        for name, value in sorted(dataset_metrics.items()):
            if isinstance(value, float) and value != int(value):
                lines.append(f"    {name}: {value:.4f}")
            else:
                lines.append(f"    {name}: {value}")

    # Epoch metrics summary
    if epoch_metrics:
        lines.append("\n  Training Progress:")
        lines.append(f"    {'Epoch':<8} {'F1':<10} {'Precision':<12} {'Recall':<10} {'Loss':<10} {'Train Loss':<12}")
        lines.append(f"    {'-'*8} {'-'*10} {'-'*12} {'-'*10} {'-'*10} {'-'*12}")

        for epoch in sorted(epoch_metrics.keys()):
            m = epoch_metrics[epoch]
            f1 = m.get("val_f1", 0)
            prec = m.get("val_precision", 0)
            rec = m.get("val_recall", 0)
            loss = m.get("eval_loss", 0)
            train_loss = m.get("train_loss", 0)
            lines.append(f"    {epoch:<8} {f1:<10.4f} {prec:<12.4f} {rec:<10.4f} {loss:<10.4f} {train_loss:<12.4f}")

    # Per-label metrics for latest epoch
    if label_metrics:
        latest_epoch = max(label_metrics.keys())
        lines.append(f"\n  Per-Label Metrics (Epoch {latest_epoch}):")
        lines.append(f"    {'Label':<25} {'F1':<10} {'Precision':<12} {'Recall':<10} {'Support':<10}")
        lines.append(f"    {'-'*25} {'-'*10} {'-'*12} {'-'*10} {'-'*10}")

        # Sort by F1 score descending
        label_data = label_metrics[latest_epoch]
        sorted_labels = sorted(
            label_data.items(),
            key=lambda x: x[1].get("f1", 0),
            reverse=True
        )

        for label, data in sorted_labels:
            f1 = data.get("f1", 0)
            prec = data.get("precision", 0)
            rec = data.get("recall", 0)
            support = int(data.get("support", 0))
            lines.append(f"    {label:<25} {f1:<10.4f} {prec:<12.4f} {rec:<10.4f} {support:<10}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Query training job status and metrics")
    parser.add_argument("--table", default="ReceiptsTable-dc5be22", help="DynamoDB table name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # List jobs
    list_parser = subparsers.add_parser("list", help="List jobs")
    list_parser.add_argument("--status", choices=["running", "pending", "succeeded", "failed"], help="Filter by status")
    list_parser.add_argument("--limit", type=int, default=10, help="Maximum number of jobs to show")

    # Get job details
    get_parser = subparsers.add_parser("get", help="Get job details")
    get_parser.add_argument("name", help="Job name")
    get_parser.add_argument("--metrics", action="store_true", help="Include metrics")

    # Get metrics only
    metrics_parser = subparsers.add_parser("metrics", help="Get job metrics")
    metrics_parser.add_argument("name", help="Job name")

    # Get config and label distribution
    config_parser = subparsers.add_parser("config", help="Get job config and label distribution")
    config_parser.add_argument("name", help="Job name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    client = get_client(args.table, args.region)

    if args.command == "list":
        jobs = list_jobs(client, args.status, args.limit)
        if not jobs:
            print("No jobs found")
            return 0

        print(f"Found {len(jobs)} job(s):\n")
        for job in jobs:
            print(format_job(job))
            print()

    elif args.command == "get":
        job = get_job_by_name(client, args.name)
        if not job:
            print(f"Job '{args.name}' not found")
            return 1

        print(format_job(job))

        if args.metrics:
            metrics = get_job_metrics(client, job.job_id)
            print(format_metrics_summary(metrics))

    elif args.command == "metrics":
        job = get_job_by_name(client, args.name)
        if not job:
            print(f"Job '{args.name}' not found")
            return 1

        print(f"Metrics for job: {job.name} ({job.status})\n")
        metrics = get_job_metrics(client, job.job_id)
        print(format_metrics_summary(metrics))

    elif args.command == "config":
        job = get_job_by_name(client, args.name)
        if not job:
            print(f"Job '{args.name}' not found")
            return 1

        print(f"Config for job: {job.name} ({job.status})\n")
        logs = get_job_logs(client, job.job_id)
        config = get_job_config(logs)
        if config:
            print(format_config(config))
        else:
            print("  No config log found for this job")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""EMF (Embedded Metrics Format) utility for label_harmonizer Lambda functions.

Uses AWS Embedded Metric Format to send metrics via CloudWatch Logs instead of
individual PutMetricData API calls. This reduces costs by ~99.9%.

Cost comparison:
- API calls: $0.30 per metric per month (can add up to $42K/month at scale!)
- EMF logs: ~$0.50/GB for log ingestion (~$0.01-0.10 for typical Lambda runs)

Usage:
    from utils.emf_metrics import emf_metrics

    # Collect metrics during processing
    metrics = {
        "OutliersDetected": 5,
        "LabelsProcessed": 100,
        "ProcessingTimeSeconds": 45.2,
    }

    # Emit ONE log line at the end (CloudWatch parses automatically)
    emf_metrics.log_metrics(
        metrics=metrics,
        dimensions={"LabelType": "GRAND_TOTAL"},
        properties={"merchant_name": "Sprouts", "batch_file": "..."}
    )
"""

import json
import os
import time
from typing import Any, Dict, Optional


class EmbeddedMetricsFormatter:
    """Formats metrics using AWS Embedded Metric Format (EMF).

    EMF allows CloudWatch to automatically extract metrics from structured
    log lines, eliminating the need for PutMetricData API calls.

    Key cost savings:
    - No per-metric API costs ($0.30/metric/month)
    - Only pay for log ingestion (~$0.50/GB)
    - Same CloudWatch Metrics dashboard visibility
    """

    def __init__(self, namespace: str = "LabelHarmonizer"):
        """Initialize EMF formatter.

        Args:
            namespace: CloudWatch namespace for metrics
        """
        self.namespace = namespace
        self.enabled = os.environ.get("ENABLE_METRICS", "true").lower() == "true"

    def create_metric_log(
        self,
        metrics: Dict[str, float],
        dimensions: Optional[Dict[str, str]] = None,
        properties: Optional[Dict[str, Any]] = None,
        units: Optional[Dict[str, str]] = None,
    ) -> str:
        """Create an EMF-formatted log entry.

        Args:
            metrics: Dictionary of metric names and values
            dimensions: Metric dimensions (keep cardinality LOW!)
            properties: Additional log properties (for context, not dimensions)
            units: Optional unit overrides (default: Count)

        Returns:
            JSON string in EMF format
        """
        if not self.enabled:
            return ""

        units = units or {}

        emf_log = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),  # EMF expects milliseconds
                "CloudWatchMetrics": [
                    {
                        "Namespace": self.namespace,
                        "Dimensions": (
                            [list(dimensions.keys())] if dimensions else [[]]
                        ),
                        "Metrics": [
                            {
                                "Name": name,
                                "Unit": units.get(name, "Count"),
                            }
                            for name in metrics.keys()
                        ],
                    }
                ],
            }
        }

        # Add dimension values
        if dimensions:
            emf_log.update(dimensions)

        # Add metric values
        emf_log.update(metrics)

        # Add additional properties (context, not dimensions)
        if properties:
            emf_log.update(properties)

        return json.dumps(emf_log)

    def log_metrics(
        self,
        metrics: Dict[str, float],
        dimensions: Optional[Dict[str, str]] = None,
        properties: Optional[Dict[str, Any]] = None,
        units: Optional[Dict[str, str]] = None,
    ):
        """Log metrics using EMF format to stdout.

        CloudWatch automatically parses EMF from Lambda stdout and creates
        metrics without any API calls.

        Args:
            metrics: Dictionary of metric names and values
            dimensions: Metric dimensions (keep LOW cardinality!)
            properties: Additional log properties for context
            units: Optional unit overrides (default: Count)

        IMPORTANT: Keep dimensions low-cardinality!
        - Good: {"LabelType": "GRAND_TOTAL"} - ~15 unique values
        - Bad: {"Merchant": "..."} - 1000s of unique values = $$$$
        """
        if not self.enabled:
            return

        emf_log = self.create_metric_log(metrics, dimensions, properties, units)
        if emf_log:
            print(emf_log, flush=True)


# Global EMF metrics formatter instance
emf_metrics = EmbeddedMetricsFormatter()

"""EMF (Embedded Metrics Format) utility for label_evaluator Lambda functions.

Uses AWS Embedded Metric Format to send metrics via CloudWatch Logs instead of
individual PutMetricData API calls. This reduces costs by ~99.9%.

Usage:
    from utils.emf_metrics import emf_metrics

    metrics = {
        "IssuesFound": 5,
        "ReceiptsProcessed": 100,
        "ProcessingTimeSeconds": 45.2,
    }

    emf_metrics.log_metrics(
        metrics=metrics,
        dimensions={},
        properties={"merchant_name": "Sprouts", "execution_id": "..."}
    )
"""

import json
import os
import time
from typing import Any, Optional


class EmbeddedMetricsFormatter:
    """Formats metrics using AWS Embedded Metric Format (EMF)."""

    def __init__(self, namespace: str = "LabelEvaluator"):
        """Initialize EMF formatter.

        Args:
            namespace: CloudWatch namespace for metrics
        """
        self.namespace = namespace
        self.enabled = (
            os.environ.get("ENABLE_METRICS", "true").lower() == "true"
        )

    def create_metric_log(
        self,
        metrics: dict[str, int | float],
        dimensions: Optional[dict[str, str]] = None,
        properties: Optional[dict[str, Any]] = None,
        units: Optional[dict[str, str]] = None,
    ) -> str:
        """Create an EMF-formatted log entry.

        Args:
            metrics: Dictionary of metric names and values
            dimensions: Metric dimensions (keep cardinality LOW!)
            properties: Additional log properties (for context)
            units: Optional unit overrides (default: Count)

        Returns:
            JSON string in EMF format
        """
        if not self.enabled:
            return ""

        units = units or {}

        emf_log = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
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

        if dimensions:
            emf_log.update(dimensions)  # type: ignore[arg-type]

        emf_log.update(metrics)  # type: ignore[arg-type]

        if properties:
            emf_log.update(properties)

        return json.dumps(emf_log)

    def log_metrics(
        self,
        metrics: dict[str, int | float],
        dimensions: Optional[dict[str, str]] = None,
        properties: Optional[dict[str, Any]] = None,
        units: Optional[dict[str, str]] = None,
    ) -> None:
        """Log metrics using EMF format to stdout.

        Args:
            metrics: Dictionary of metric names and values
            dimensions: Metric dimensions (keep LOW cardinality!)
            properties: Additional log properties for context
            units: Optional unit overrides (default: Count)
        """
        if not self.enabled:
            return

        emf_log = self.create_metric_log(
            metrics, dimensions, properties, units
        )
        if emf_log:
            print(emf_log, flush=True)


emf_metrics = EmbeddedMetricsFormatter()

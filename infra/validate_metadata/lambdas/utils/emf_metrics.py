"""EMF (Embedded Metrics Format) utility for validate_metadata Lambda functions."""

import json
import os
import time
from typing import Any, Dict, Optional


class EmbeddedMetricsFormatter:
    """Formats metrics using AWS Embedded Metric Format (EMF) for efficient CloudWatch integration."""

    def __init__(self, namespace: str = "ValidateMetadata"):
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
        metrics: Dict[str, float],
        dimensions: Optional[Dict[str, str]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create an EMF-formatted log entry.

        Args:
            metrics: Dictionary of metric names and values
            dimensions: Metric dimensions
            properties: Additional log properties

        Returns:
            JSON string in EMF format
        """
        if not self.enabled:
            return ""

        emf_log = {
            "_aws": {
                "Timestamp": int(
                    time.time() * 1000
                ),  # EMF expects milliseconds
                "CloudWatchMetrics": [
                    {
                        "Namespace": self.namespace,
                        "Dimensions": (
                            [list(dimensions.keys())] if dimensions else [[]]
                        ),
                        "Metrics": [
                            {"Name": name, "Unit": "Count"}
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

        # Add additional properties
        if properties:
            emf_log.update(properties)

        return json.dumps(emf_log)

    def log_metrics(
        self,
        metrics: Dict[str, float],
        dimensions: Optional[Dict[str, str]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ):
        """Log metrics using EMF format to stdout (CloudWatch will parse automatically).

        Args:
            metrics: Dictionary of metric names and values
            dimensions: Metric dimensions
            properties: Additional log properties
        """
        if not self.enabled:
            return

        emf_log = self.create_metric_log(metrics, dimensions, properties)
        print(emf_log, flush=True)  # CloudWatch automatically parses EMF from stdout


# Global EMF metrics formatter instance
emf_metrics = EmbeddedMetricsFormatter()


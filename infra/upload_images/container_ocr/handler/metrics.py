"""CloudWatch custom metrics utility for upload lambda."""

import json
import os
import time
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError


class MetricsCollector:
    """Collects and publishes custom metrics to CloudWatch."""

    def __init__(self, namespace: str = "EmbeddingWorkflow"):
        """Initialize metrics collector.

        Args:
            namespace: CloudWatch namespace for metrics
        """
        self.namespace = namespace
        self.enabled = (
            os.environ.get("ENABLE_METRICS", "true").lower() == "true"
        )

        if self.enabled:
            try:
                self.cloudwatch = boto3.client("cloudwatch")
            except Exception as e:
                print(f"[METRICS] Failed to initialize CloudWatch client: {e}", flush=True)
                self.enabled = False

    def put_metric(
        self,
        metric_name: str,
        value: float,
        unit: str = "Count",
        dimensions: Optional[Dict[str, str]] = None,
        timestamp: Optional[float] = None,
    ):
        """Put a single metric to CloudWatch.

        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: Metric unit (Count, Seconds, etc.)
            dimensions: Metric dimensions as key-value pairs
            timestamp: Unix timestamp (defaults to current time)
        """
        if not self.enabled:
            return

        try:
            metric_data = {
                "MetricName": metric_name,
                "Value": value,
                "Unit": unit,
                "Timestamp": timestamp or time.time(),
            }

            if dimensions:
                metric_data["Dimensions"] = [
                    {"Name": k, "Value": v} for k, v in dimensions.items()
                ]

            self.cloudwatch.put_metric_data(
                Namespace=self.namespace, MetricData=[metric_data]
            )

        except ClientError as e:
            print(f"[METRICS] Failed to publish metric {metric_name}: {e}", flush=True)

    def count(
        self,
        metric_name: str,
        value: int = 1,
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """Publish a count metric.

        Args:
            metric_name: Name of the count metric
            value: Count value (defaults to 1)
            dimensions: Metric dimensions
        """
        self.put_metric(metric_name, value, "Count", dimensions)

    def gauge(
        self,
        metric_name: str,
        value: float,
        unit: str = "None",
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """Publish a gauge metric.

        Args:
            metric_name: Name of the gauge metric
            value: Gauge value
            unit: Metric unit
            dimensions: Metric dimensions
        """
        self.put_metric(metric_name, value, unit, dimensions)

    def timer(
        self,
        metric_name: str,
        duration: float,
        unit: str = "Seconds",
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """Publish a duration metric.

        Args:
            metric_name: Name of the duration metric
            duration: Duration in the specified unit
            unit: Time unit (Seconds, Milliseconds)
            dimensions: Metric dimensions
        """
        if unit == "Milliseconds":
            duration = duration * 1000
        self.put_metric(metric_name, duration, unit, dimensions)


# Global metrics collector instance
metrics = MetricsCollector()


"""CloudWatch custom metrics utility for Lambda functions."""

import json
import os
import time
from contextlib import contextmanager
from typing import Dict, Any, Optional, Union
from functools import wraps

import boto3
from botocore.exceptions import ClientError

from .logging import get_operation_logger


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
        self.logger = get_operation_logger(__name__)

        if self.enabled:
            try:
                self.cloudwatch = boto3.client("cloudwatch")
            except Exception as e:
                self.logger.error(
                    "Failed to initialize CloudWatch client", error=str(e)
                )
                self.enabled = False

    def put_metric(
        self,
        metric_name: str,
        value: Union[int, float],
        unit: str = "Count",
        dimensions: Optional[Dict[str, str]] = None,
        timestamp: Optional[float] = None,
    ):
        """Put a single metric to CloudWatch.

        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: Metric unit (Count, Seconds, Bytes, etc.)
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

            self.logger.debug(
                "Published metric to CloudWatch",
                metric_name=metric_name,
                value=value,
                unit=unit,
                dimensions=dimensions,
            )

        except ClientError as e:
            self.logger.error(
                "Failed to publish metric to CloudWatch",
                metric_name=metric_name,
                error=str(e),
            )

    def put_metric_batch(self, metrics: list):
        """Put multiple metrics to CloudWatch in a single call.

        Args:
            metrics: List of metric dictionaries
        """
        if not self.enabled or not metrics:
            return

        try:
            # CloudWatch allows max 20 metrics per call
            for i in range(0, len(metrics), 20):
                batch = metrics[i : i + 20]
                self.cloudwatch.put_metric_data(
                    Namespace=self.namespace, MetricData=batch
                )

            self.logger.debug(
                "Published metric batch to CloudWatch",
                metric_count=len(metrics),
            )

        except ClientError as e:
            self.logger.error(
                "Failed to publish metric batch to CloudWatch", error=str(e)
            )

    @contextmanager
    def timer(
        self,
        metric_name: str,
        dimensions: Optional[Dict[str, str]] = None,
        unit: str = "Seconds",
    ):
        """Context manager for timing operations and publishing duration metrics.

        Args:
            metric_name: Name of the duration metric
            dimensions: Metric dimensions
            unit: Time unit (Seconds, Milliseconds)

        Yields:
            Start time for additional timing calculations
        """
        start_time = time.time()
        try:
            yield start_time
        finally:
            duration = time.time() - start_time
            if unit == "Milliseconds":
                duration *= 1000

            self.put_metric(metric_name, duration, unit, dimensions)

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
        value: Union[int, float],
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

    def time_function(
        self,
        metric_name: Optional[str] = None,
        dimensions: Optional[Dict[str, str]] = None,
    ):
        """Decorator for timing function execution and publishing metrics.

        Args:
            metric_name: Custom metric name (defaults to function name)
            dimensions: Metric dimensions

        Returns:
            Decorated function
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                name = metric_name or f"{func.__name__}Duration"
                with self.timer(name, dimensions):
                    return func(*args, **kwargs)

            return wrapper

        return decorator


class EmbeddedMetricsFormatter:
    """Formats metrics using AWS Embedded Metric Format (EMF) for efficient CloudWatch integration."""

    def __init__(self, namespace: str = "EmbeddingWorkflow"):
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
        metrics: Dict[str, Union[int, float]],
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
        metrics: Dict[str, Union[int, float]],
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
        print(emf_log)  # CloudWatch automatically parses EMF from stdout


# Global metrics collector instance
metrics = MetricsCollector()
emf_metrics = EmbeddedMetricsFormatter()


def timed_operation(
    metric_name: str, dimensions: Optional[Dict[str, str]] = None
):
    """Decorator for timing operations with both CloudWatch and EMF metrics.

    Args:
        metric_name: Name of the metric
        dimensions: Metric dimensions

    Returns:
        Decorated function
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time

                # Publish to both CloudWatch and EMF
                metrics.put_metric(
                    metric_name, duration, "Seconds", dimensions
                )
                emf_metrics.log_metrics(
                    {metric_name: duration},
                    dimensions,
                    {"operation": "success"},
                )

                return result

            except Exception as e:
                duration = time.time() - start_time

                # Publish error metrics
                error_dimensions = {**(dimensions or {}), "status": "error"}
                metrics.put_metric(
                    metric_name, duration, "Seconds", error_dimensions
                )
                emf_metrics.log_metrics(
                    {metric_name: duration},
                    error_dimensions,
                    {"operation": "error", "error": str(e)},
                )

                raise

        return wrapper

    return decorator


# Convenience functions for common operations
def track_openai_api_call(operation: str = "poll"):
    """Track OpenAI API call duration."""
    return timed_operation("OpenAIAPICallDuration", {"operation": operation})


def track_s3_operation(operation: str):
    """Track S3 operation duration."""
    return timed_operation("S3OperationDuration", {"operation": operation})


def track_chromadb_operation(operation: str):
    """Track ChromaDB operation duration."""
    return timed_operation(
        "ChromaDBOperationDuration", {"operation": operation}
    )


def track_dynamodb_operation(operation: str):
    """Track DynamoDB operation duration."""
    return timed_operation(
        "DynamoDBOperationDuration", {"operation": operation}
    )

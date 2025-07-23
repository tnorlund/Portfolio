"""Job metric operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities.job_metric import JobMetric
from receipt_dynamo.entities import item_to_job_metric
from receipt_dynamo.data._job_metric import _JobMetric


class JobMetricOperations(_JobMetric):
    """Handles job metric-related operations."""

    def add_job_metric_with_params(
        self,
        job_id: str,
        metric_name: str,
        metric_value: float,
        unit: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> JobMetric:
        """Add a metric for a job.

        Args:
            job_id: The job ID
            metric_name: Name of the metric (e.g., "accuracy", "loss")
            metric_value: The metric value
            unit: Optional unit of measurement
            tags: Optional tags for the metric

        Returns:
            The created JobMetric
        """
        job_metric = JobMetric(
            job_id=job_id,
            timestamp=datetime.now(),
            metric_name=metric_name,
            value=metric_value,
            unit=unit,
        )
        super().add_job_metric(job_metric)
        return job_metric

    def get_job_metrics(self, job_id: str) -> List[JobMetric]:
        """Get all metrics for a job."""
        # TODO: Implement get_job_metrics in _JobMetric base class
        # For now, return empty list
        return []

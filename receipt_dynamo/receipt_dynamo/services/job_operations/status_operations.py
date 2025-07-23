"""Job status and logging operations."""

from datetime import datetime
from typing import List, Optional

from receipt_dynamo.entities.job_log import JobLog
from receipt_dynamo.entities.job_status import JobStatus
from receipt_dynamo.entities import item_to_job_log, item_to_job_status
from receipt_dynamo.data._job_log import _JobLog
from receipt_dynamo.data._job_status import _JobStatus


class JobStatusOperations(_JobStatus, _JobLog):
    """Handles job status and logging operations."""

    def add_job_status_with_params(
        self,
        job_id: str,
        status: str,
        message: Optional[str] = None,
        progress: int = 0,
    ) -> JobStatus:
        """Add a status update for a job using parameters.

        Args:
            job_id: The job ID
            status: The new status
            message: Optional status message
            progress: Optional progress percentage (0-100)

        Returns:
            The created JobStatus
        """
        job_status = JobStatus(
            job_id=job_id,
            updated_at=datetime.now(),
            status=status,
            message=message,
            progress=float(progress) if progress else None,
        )
        super().add_job_status(job_status)
        return job_status

    def get_job_status_history(self, job_id: str) -> List[JobStatus]:
        """Get the complete status history for a job."""
        # TODO: Implement get_job_status_history in _JobStatus base class
        # For now, use list_job_statuses with the job_id
        items, _ = super().list_job_statuses(job_id=job_id)
        return items

    def add_job_log_with_params(
        self, job_id: str, log_level: str, message: str
    ) -> JobLog:
        """Add a log entry for a job using parameters.

        Args:
            job_id: The job ID
            log_level: Log level (INFO, WARNING, ERROR, DEBUG)
            message: The log message

        Returns:
            The created JobLog
        """
        job_log = JobLog(
            job_id=job_id,
            timestamp=datetime.now(),
            log_level=log_level,
            message=message,
        )
        super().add_job_log(job_log)
        return job_log

    def get_job_logs(self, job_id: str) -> List[JobLog]:
        """Get all logs for a job."""
        # Use list_job_logs from parent class
        items, _ = super().list_job_logs(job_id=job_id)
        return items

"""Job status and logging operations."""

from datetime import datetime
from typing import List

from receipt_dynamo import (JobLog, JobStatus, item_to_job_log,
                            item_to_job_status)
from receipt_dynamo.data._job_log import _JobLog
from receipt_dynamo.data._job_status import _JobStatus


class JobStatusOperations(_JobStatus, _JobLog):
    """Handles job status and logging operations."""

    def add_job_status(
        self,
        job_id: str,
        status: str,
        message: str = None,
        progress: int = 0,
    ) -> JobStatus:
        """Add a status update for a job.

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
            timestamp=datetime.now(),
            status=status,
            message=message,
            progress=progress,
        )
        super().add_job_status(job_status)
        return job_status

    def get_job_status_history(self, job_id: str) -> List[JobStatus]:
        """Get the complete status history for a job."""
        items = super().get_job_status_history(job_id)
        return [item_to_job_status(item) for item in items]

    def add_job_log(self, job_id: str, log_level: str, message: str) -> JobLog:
        """Add a log entry for a job.

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
        items = super().get_job_logs(job_id)
        return [item_to_job_log(item) for item in items]

"""Job checkpoint operations."""

from datetime import datetime
from typing import Dict, List, Optional

from receipt_dynamo.data._job_checkpoint import _JobCheckpoint
from receipt_dynamo.entities.job_checkpoint import JobCheckpoint


class JobCheckpointOperations(_JobCheckpoint):
    """Handles job checkpoint-related operations."""

    def add_job_checkpoint_with_params(
        self,
        job_id: str,
        checkpoint_name: str,
        metrics: Optional[Dict[str, float]] = None,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        step: int = 0,
        epoch: int = 0,
    ) -> JobCheckpoint:
        """Add a checkpoint for a job.

        Args:
            job_id: The job ID
            checkpoint_name: Name of the checkpoint
            metrics: Optional metrics at checkpoint time
            s3_bucket: S3 bucket containing checkpoint
            s3_key: S3 key for checkpoint file
            step: Training step number
            epoch: Training epoch number

        Returns:
            The created JobCheckpoint
        """
        checkpoint = JobCheckpoint(
            job_id=job_id,
            timestamp=datetime.now().isoformat(),
            s3_bucket=s3_bucket or "unknown",
            s3_key=s3_key or f"checkpoints/{job_id}/{checkpoint_name}",
            size_bytes=0,  # TODO: Get actual checkpoint size
            step=step,
            epoch=epoch,
            metrics=metrics or {},
        )
        super().add_job_checkpoint(checkpoint)
        return checkpoint

    def get_job_checkpoints(self, job_id: str) -> List[JobCheckpoint]:
        """Get all checkpoints for a job."""
        # TODO: Implement get_job_checkpoints in _JobCheckpoint base class
        return []

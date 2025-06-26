"""
Standardized job processor implementation for Receipt Trainer.

This module defines a common interface for job processing and provides
concrete implementations for different environments.
"""

import abc
import enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import boto3
from botocore.exceptions import ClientError
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.services.job_service import JobService
from receipt_trainer.jobs.job import Job, JobStatus
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig


class ProcessingMode(enum.Enum):
    """Processing mode for the job processor."""

    NORMAL = "normal"  # Standard processing mode
    DEBUG = "debug"  # Debug mode with detailed logging
    TEST = "test"  # Test mode with isolated resources


class JobProcessor(abc.ABC):
    """
    Abstract base class for job processors.

    This class defines the interface that all job processors must implement,
    ensuring consistent behavior across different processing environments.
    """

    def __init__(self, mode: ProcessingMode = ProcessingMode.NORMAL):
        """
        Initialize the job processor.

        Args:
            mode: Processing mode
        """
        self.mode = mode
        self.logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._stop_event = threading.Event()

        # Configure debug logging if in debug mode
        if mode == ProcessingMode.DEBUG:
            self._setup_debug_logging()

    def _setup_debug_logging(self) -> None:
        """Configure detailed logging for debug mode."""
        # Create a dedicated debug logger
        debug_logger = logging.getLogger(f"{__name__}.debug")
        debug_logger.setLevel(logging.DEBUG)

        # Add a file handler to log debug information
        try:
            handler = logging.FileHandler("job_processor_debug.log")
            handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            debug_logger.addHandler(handler)

            self.debug_logger = debug_logger
            self.logger.info("Debug logging enabled for job processor")
        except Exception as e:
            self.logger.warning(f"Failed to set up debug logging: {e}")
            self.debug_logger = self.logger

    def debug_log(self, message: str) -> None:
        """
        Log a debug message if in debug mode.

        Args:
            message: Debug message to log
        """
        if self.mode == ProcessingMode.DEBUG and hasattr(self, "debug_logger"):
            self.debug_logger.debug(message)

    @abc.abstractmethod
    def start(self) -> None:
        """Start processing jobs."""
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop processing jobs."""
        pass

    @abc.abstractmethod
    def process_job(self, job: Job) -> bool:
        """
        Process a single job.

        Args:
            job: Job to process

        Returns:
            True if successful, False otherwise
        """
        pass

    @abc.abstractmethod
    def submit_job(self, job: Job) -> str:
        """
        Submit a job for processing.

        Args:
            job: Job to submit

        Returns:
            Job ID
        """
        pass

    @abc.abstractmethod
    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """
        Get the status of a job.

        Args:
            job_id: ID of the job to check

        Returns:
            Job status if found, None otherwise
        """
        pass


class SQSJobProcessor(JobProcessor):
    """
    Job processor implementation using AWS SQS for job queueing.

    This class processes jobs from an SQS queue and tracks job status
    in DynamoDB. It's the recommended standard implementation for
    production use.
    """

    def __init__(
        self,
        queue_url: str,
        dynamo_table: str,
        handler: Callable[[Job], bool],
        region: Optional[str] = None,
        mode: ProcessingMode = ProcessingMode.NORMAL,
        dlq_url: Optional[str] = None,
        visibility_timeout_seconds: int = 1800,
        max_retries: int = 3,
        test_prefix: Optional[str] = None,
    ):
        """
        Initialize the SQS job processor.

        Args:
            queue_url: SQS queue URL
            dynamo_table: DynamoDB table name for job tracking
            handler: Function to handle job processing
            region: AWS region
            mode: Processing mode
            dlq_url: Dead-letter queue URL
            visibility_timeout_seconds: Visibility timeout for messages
            max_retries: Maximum number of retries for failed jobs
            test_prefix: Prefix for test resources (test mode only)
        """
        super().__init__(mode)

        self.queue_url = queue_url
        self.dynamo_table = dynamo_table
        self.handler = handler
        self.region = region
        self.test_prefix = test_prefix

        # Create job queue configuration
        self.job_queue = JobQueue(
            JobQueueConfig(
                queue_url=queue_url,
                dlq_url=dlq_url,
                visibility_timeout_seconds=visibility_timeout_seconds,
                max_retries=max_retries,
                aws_region=region,
            )
        )

        # Initialize DynamoDB client
        self.dynamo_client = DynamoClient(
            table_name=dynamo_table, region=region
        )

        # Initialize job service
        self.job_service = JobService(dynamo_table, region=region)

        # Set up processing thread
        self._processing_thread = None

        self.logger.info(
            f"Initialized SQS job processor for queue {queue_url}"
        )
        if mode == ProcessingMode.TEST:
            self.logger.info(f"Running in TEST mode with prefix {test_prefix}")
        elif mode == ProcessingMode.DEBUG:
            self.logger.info("Running in DEBUG mode with detailed logging")

    def start(self) -> None:
        """Start processing jobs from the queue."""
        if self._processing_thread and self._processing_thread.is_alive():
            self.logger.warning("Job processor is already running")
            return

        self._stop_event.clear()
        self._processing_thread = threading.Thread(
            target=self._process_jobs_loop,
            daemon=True,
        )
        self._processing_thread.start()
        self.logger.info("Started job processing thread")

        if self.mode == ProcessingMode.DEBUG:
            self.debug_log("Job processor started in debug mode")

    def stop(self) -> None:
        """Stop processing jobs."""
        if (
            not self._processing_thread
            or not self._processing_thread.is_alive()
        ):
            self.logger.warning("Job processor is not running")
            return

        self.logger.info("Stopping job processor...")
        self._stop_event.set()
        self._processing_thread.join(timeout=30)

        if self._processing_thread.is_alive():
            self.logger.warning("Job processor thread did not stop gracefully")
        else:
            self.logger.info("Job processor stopped")

        if self.mode == ProcessingMode.DEBUG:
            self.debug_log("Job processor stopped")

    def _process_jobs_loop(self) -> None:
        """Process jobs continuously from the queue."""
        self.logger.info("Starting job processing loop")

        while not self._stop_event.is_set():
            try:
                # Receive jobs from the queue
                jobs = self.job_queue.receive_jobs()

                if not jobs:
                    # No jobs available, wait before polling again
                    time.sleep(5)
                    continue

                self.logger.debug(f"Received {len(jobs)} jobs from queue")

                for job, receipt_handle in jobs:
                    if self.mode == ProcessingMode.DEBUG:
                        self.debug_log(
                            f"Processing job {job.job_id}: {job.name}"
                        )

                    # For tracking the current job (used by spot termination handler)
                    self._current_job = job

                    try:
                        # Process the job
                        success = self.process_job(job, receipt_handle)

                        if success:
                            # Delete the message from the queue on success
                            try:
                                self.job_queue.delete_job(receipt_handle)
                                self.logger.info(
                                    f"Job {job.job_id} processed successfully"
                                )
                            except Exception as e:
                                self.logger.error(
                                    f"Error deleting job from queue: {e}"
                                )

                            if self.mode == ProcessingMode.DEBUG:
                                self.debug_log(
                                    f"Job {job.job_id} completed successfully"
                                )
                        else:
                            # Handle failed job
                            if job.can_retry():
                                # Retry the job
                                try:
                                    self.job_queue.retry_job(job)
                                    self.job_queue.delete_job(receipt_handle)
                                    self.logger.info(
                                        f"Job {job.job_id} failed, scheduled for retry"
                                    )
                                except Exception as e:
                                    self.logger.error(
                                        f"Error retrying job: {e}"
                                    )

                                if self.mode == ProcessingMode.DEBUG:
                                    self.debug_log(
                                        f"Job {job.job_id} failed, scheduled for retry (attempt {job.attempt_count})"
                                    )
                            else:
                                # No more retries, mark as failed and delete from queue
                                try:
                                    self.job_queue.delete_job(receipt_handle)
                                    self.logger.warning(
                                        f"Job {job.job_id} failed after {job.attempt_count} attempts"
                                    )
                                except Exception as e:
                                    self.logger.error(
                                        f"Error deleting failed job from queue: {e}"
                                    )

                                if self.mode == ProcessingMode.DEBUG:
                                    self.debug_log(
                                        f"Job {job.job_id} failed permanently after {job.attempt_count} attempts"
                                    )
                    except Exception as e:
                        self.logger.error(
                            f"Error processing job {job.job_id}: {e}"
                        )

                        if self.mode == ProcessingMode.DEBUG:
                            import traceback

                            self.debug_log(
                                f"Error processing job {job.job_id}: {e}\n{traceback.format_exc()}"
                            )

                        # Extend visibility timeout to prevent other consumers from picking up this job
                        try:
                            self.job_queue.extend_visibility_timeout(
                                receipt_handle, 60
                            )
                        except Exception as extend_error:
                            self.logger.error(
                                f"Error extending visibility timeout: {extend_error}"
                            )

                    finally:
                        # Clear current job reference
                        self._current_job = None

            except Exception as e:
                self.logger.error(f"Error in job processor: {e}")

                if self.mode == ProcessingMode.DEBUG:
                    import traceback

                    self.debug_log(
                        f"Error in job processor main loop: {e}\n{traceback.format_exc()}"
                    )

                # Wait before retrying to prevent tight error loops
                time.sleep(10)

        self.logger.info("Job processing loop terminated")

    def process_job(
        self, job: Job, receipt_handle: Optional[str] = None
    ) -> bool:
        """
        Process a single job.

        Args:
            job: Job to process
            receipt_handle: SQS receipt handle (optional)

        Returns:
            True if successful, False otherwise
        """
        if self.mode == ProcessingMode.DEBUG:
            self.debug_log(
                f"Begin processing job {job.job_id} ({job.name}, type={job.type})"
            )

        # Mark job as started
        try:
            job.mark_started()
            # Try to update the status in DynamoDB, but continue even if it fails
            try:
                self._update_job_status(job, JobStatus.RUNNING)
            except Exception as e:
                self.logger.warning(
                    f"Failed to update job start status, continuing anyway: {e}"
                )
        except Exception as e:
            self.logger.error(f"Error marking job as started: {e}")
            return False

        try:
            # Call the handler function to process the job
            result = self.handler(job)

            if self.mode == ProcessingMode.DEBUG:
                self.debug_log(f"Job {job.job_id} handler result: {result}")

            # Try to update the final status, but continue even if it fails
            try:
                if result:
                    job.mark_completed(True)
                    self._update_job_status(job, JobStatus.SUCCEEDED)
                else:
                    job.mark_completed(False, "Job handler returned False")
                    self._update_job_status(
                        job, JobStatus.FAILED, "Job handler returned False"
                    )
            except Exception as e:
                self.logger.warning(
                    f"Failed to update job completion status, continuing anyway: {e}"
                )

            return result
        except Exception as e:
            self.logger.error(
                f"Error in job handler for job {job.job_id}: {e}"
            )

            if self.mode == ProcessingMode.DEBUG:
                import traceback

                self.debug_log(
                    f"Error in job handler for job {job.job_id}: {e}\n{traceback.format_exc()}"
                )

            # Mark job as failed
            try:
                job.mark_completed(False, str(e))
                self._update_job_status(job, JobStatus.FAILED, str(e))
            except Exception as update_error:
                self.logger.warning(
                    f"Failed to update job failure status, continuing anyway: {update_error}"
                )

            return False

    def submit_job(self, job: Job) -> str:
        """
        Submit a job for processing.

        Args:
            job: Job to submit

        Returns:
            Job ID
        """
        if self.mode == ProcessingMode.TEST and self.test_prefix:
            # Add test prefix to job name for isolation
            if not job.name.startswith(self.test_prefix):
                job.name = f"{self.test_prefix}_{job.name}"

            # Add test tag
            job.tags["test"] = "true"

        if self.mode == ProcessingMode.DEBUG:
            self.debug_log(
                f"Submitting job {job.job_id} ({job.name}, type={job.type})"
            )

        # Store job in DynamoDB first
        self._store_job(job)

        # Submit to SQS queue
        job_id = self.job_queue.submit_job(job)

        self.logger.info(f"Submitted job {job_id} to queue")
        return job_id

    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """
        Get the status of a job.

        Args:
            job_id: ID of the job to check

        Returns:
            Job status if found, None otherwise
        """
        try:
            # Get job from DynamoDB using JobService
            try:
                job_data = self.job_service.get_job(job_id)

                if not job_data:
                    return None

                # Get status string from job data
                status_str = (
                    job_data.status
                    if hasattr(job_data, "status")
                    else "pending"
                )

                try:
                    return JobStatus(status_str)
                except ValueError:
                    self.logger.warning(f"Unknown job status: {status_str}")
                    return None

            except Exception as e:
                self.logger.warning(
                    f"Failed to get job status from DynamoDB, continuing anyway: {e}"
                )
                return None

        except Exception as e:
            self.logger.error(f"Error getting job status for {job_id}: {e}")
            return None

    def _store_job(self, job: Job) -> None:
        """
        Store job in DynamoDB.

        Args:
            job: Job to store
        """
        try:
            # Convert job to dictionary
            job_dict = job.to_dict()

            # Add job to DynamoDB using JobService
            try:
                self.job_service.create_job(
                    job_id=job.job_id,
                    name=job.name,
                    description=f"Job {job.name} of type {job.type}",
                    created_by="job_processor",
                    status=job.status.value,
                    priority=job.priority.value,
                    job_config=job.config,
                    tags=job.tags,
                )

                if self.mode == ProcessingMode.DEBUG:
                    self.debug_log(f"Stored job {job.job_id} in DynamoDB")

            except Exception as e:
                self.logger.warning(
                    f"Failed to store job in DynamoDB, continuing anyway: {e}"
                )

        except Exception as e:
            self.logger.error(
                f"Error storing job {job.job_id} in DynamoDB: {e}"
            )

            if self.mode == ProcessingMode.DEBUG:
                import traceback

                self.debug_log(
                    f"Error storing job {job.job_id} in DynamoDB: {e}\n{traceback.format_exc()}"
                )

    def _update_job_status(
        self, job: Job, status: JobStatus, error_message: Optional[str] = None
    ) -> None:
        """
        Update job status in DynamoDB.

        Args:
            job: Job to update
            status: New job status
            error_message: Error message (if any)
        """
        try:
            # Update job status using JobService
            try:
                message = (
                    error_message or f"Job status updated to {status.value}"
                )
                self.job_service.add_job_status(
                    job_id=job.job_id, status=status.value, message=message
                )

                # Also log error messages if present
                if error_message:
                    self.job_service.add_job_log(
                        job_id=job.job_id,
                        log_level="ERROR",
                        message=error_message,
                    )

                if self.mode == ProcessingMode.DEBUG:
                    self.debug_log(
                        f"Updated job {job.job_id} status to {status.value}"
                    )

            except Exception as e:
                self.logger.warning(
                    f"Failed to update job status in DynamoDB, continuing anyway: {e}"
                )

        except Exception as e:
            self.logger.error(
                f"Error updating job {job.job_id} status in DynamoDB: {e}"
            )

            if self.mode == ProcessingMode.DEBUG:
                import traceback

                self.debug_log(
                    f"Error updating job {job.job_id} status in DynamoDB: {e}\n{traceback.format_exc()}"
                )


class EC2JobProcessor(SQSJobProcessor):
    """
    Job processor implementation specialized for EC2 instances.

    This class extends SQSJobProcessor with EC2-specific functionality
    such as spot instance handling and cluster coordination.
    """

    def __init__(
        self,
        queue_url: str,
        dynamo_table: str,
        handler: Callable[[Job], bool],
        region: Optional[str] = None,
        mode: ProcessingMode = ProcessingMode.NORMAL,
        dlq_url: Optional[str] = None,
        visibility_timeout_seconds: int = 1800,
        max_retries: int = 3,
        test_prefix: Optional[str] = None,
        instance_registry_table: Optional[str] = None,
        setup_efs: bool = True,
        handle_spot: bool = True,
        enable_coordination: bool = True,
    ):
        """
        Initialize the EC2 job processor.

        Args:
            queue_url: SQS queue URL
            dynamo_table: DynamoDB table name for job tracking
            handler: Function to handle job processing
            region: AWS region
            mode: Processing mode
            dlq_url: Dead-letter queue URL
            visibility_timeout_seconds: Visibility timeout for messages
            max_retries: Maximum number of retries for failed jobs
            test_prefix: Prefix for test resources (test mode only)
            instance_registry_table: DynamoDB table for instance registry
            setup_efs: Whether to set up EFS
            handle_spot: Whether to handle spot instance termination
            enable_coordination: Whether to enable cluster coordination
        """
        super().__init__(
            queue_url=queue_url,
            dynamo_table=dynamo_table,
            handler=handler,
            region=region,
            mode=mode,
            dlq_url=dlq_url,
            visibility_timeout_seconds=visibility_timeout_seconds,
            max_retries=max_retries,
            test_prefix=test_prefix,
        )

        self.instance_registry_table = instance_registry_table
        self.setup_efs = setup_efs
        self.handle_spot = handle_spot
        self.enable_coordination = enable_coordination

        # These will be initialized when start() is called
        self.training_env = None
        self.spot_handler = None

        self.logger.info(
            f"EC2JobProcessor initialized with coordination={enable_coordination}, spot={handle_spot}"
        )

    def start(self) -> None:
        """Start the EC2 job processor."""
        # Import here to avoid circular imports
        try:
            from receipt_trainer.utils.infrastructure import (
                TrainingEnvironment,
            )

            # Set up training environment
            self.logger.info("Setting up training environment")
            self.training_env = TrainingEnvironment(
                registry_table=self.instance_registry_table,
                setup_efs=self.setup_efs,
                handle_spot=self.handle_spot,
                enable_coordination=self.enable_coordination,
            )

            # Start processing thread
            super().start()

        except Exception as e:
            self.logger.error(f"Error starting EC2 job processor: {e}")
            raise

    def process_job(
        self, job: Job, receipt_handle: Optional[str] = None
    ) -> bool:
        """
        Process a job on EC2.

        Args:
            job: Job to process
            receipt_handle: SQS receipt handle

        Returns:
            True if successful, False otherwise
        """
        # Add EC2 instance ID to job tags
        if self.training_env:
            try:
                from receipt_trainer.utils.infrastructure import EC2Metadata

                instance_id = EC2Metadata.get_instance_id()
                if instance_id:
                    job.tags["instance_id"] = instance_id
            except Exception as e:
                self.logger.warning(f"Could not get EC2 instance ID: {e}")

        # Process the job using the parent class
        return super().process_job(job, receipt_handle)


def create_job_processor(
    processor_type: str,
    queue_url: str,
    dynamo_table: str,
    handler: Callable[[Job], bool],
    region: Optional[str] = None,
    mode: ProcessingMode = ProcessingMode.NORMAL,
    **kwargs,
) -> JobProcessor:
    """
    Factory function to create the appropriate job processor.

    Args:
        processor_type: Type of processor to create ("sqs" or "ec2")
        queue_url: SQS queue URL
        dynamo_table: DynamoDB table name
        handler: Job handler function
        region: AWS region
        mode: Processing mode
        **kwargs: Additional arguments for the specific processor type

    Returns:
        JobProcessor instance

    Raises:
        ValueError: If processor_type is not valid
    """
    if processor_type.lower() == "sqs":
        return SQSJobProcessor(
            queue_url=queue_url,
            dynamo_table=dynamo_table,
            handler=handler,
            region=region,
            mode=mode,
            **kwargs,
        )
    elif processor_type.lower() == "ec2":
        return EC2JobProcessor(
            queue_url=queue_url,
            dynamo_table=dynamo_table,
            handler=handler,
            region=region,
            mode=mode,
            **kwargs,
        )
    else:
        raise ValueError(
            f"Invalid processor type: {processor_type}. Must be 'sqs' or 'ec2'."
        )

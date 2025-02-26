"""
Job queue implementation for managing ML training jobs with AWS SQS.
"""

import json
import time
import enum
import logging
import threading
import dataclasses
from typing import Dict, Any, Optional, List, Union, Callable, Tuple, TypeVar

import boto3
from botocore.exceptions import ClientError

from .job import Job, JobStatus, JobPriority


# Type variable for job handler callback
T = TypeVar('T')


class JobRetryStrategy(enum.Enum):
    """Retry strategy for failed jobs."""
    NONE = "none"  # No retries
    IMMEDIATE = "immediate"  # Retry immediately
    LINEAR_BACKOFF = "linear_backoff"  # Linear backoff between retries
    EXPONENTIAL_BACKOFF = "exponential_backoff"  # Exponential backoff between retries


@dataclasses.dataclass
class JobQueueConfig:
    """Configuration for job queue."""
    queue_url: str
    dlq_url: Optional[str] = None
    max_retries: int = 3
    visibility_timeout_seconds: int = 1800  # 30 minutes
    retry_strategy: JobRetryStrategy = JobRetryStrategy.EXPONENTIAL_BACKOFF
    base_retry_seconds: int = 30  # Base time for retry calculations
    aws_region: Optional[str] = None
    max_batch_size: int = 10
    wait_time_seconds: int = 20  # Long polling time


class JobQueue:
    """
    Queue for managing ML training jobs using AWS SQS.
    
    This class provides methods to:
    1. Submit jobs to the queue
    2. Process jobs from the queue
    3. Implement retry mechanisms for failed jobs
    4. Track job status
    """
    
    def __init__(self, config: JobQueueConfig):
        """
        Initialize the job queue.
        
        Args:
            config: Configuration for the job queue
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Initialize boto3 clients
        self.sqs = boto3.client('sqs', region_name=config.aws_region)
        
        # Initialize DynamoDB client for job status tracking (optional enhancement)
        self.dynamodb = boto3.client('dynamodb', region_name=config.aws_region)
        
        # Flag to control the job processor loop
        self._stop_processing = threading.Event()
    
    def submit_job(self, job: Job) -> str:
        """
        Submit a job to the queue.
        
        Args:
            job: The job to submit
            
        Returns:
            The job ID
            
        Raises:
            ClientError: If there is an error submitting the job to SQS
        """
        self.logger.info(f"Submitting job {job.name} ({job.job_id}) to queue")
        
        try:
            # Send the message to SQS
            response = self.sqs.send_message(
                QueueUrl=self.config.queue_url,
                MessageBody=job.to_json(),
                MessageAttributes=job.get_sqs_message_attributes(),
                MessageGroupId=job.get_sqs_message_group_id(),
                MessageDeduplicationId=job.get_sqs_deduplication_id()
            )
            
            self.logger.debug(f"Job {job.job_id} submitted successfully with SQS message ID: {response.get('MessageId')}")
            return job.job_id
            
        except ClientError as e:
            self.logger.error(f"Error submitting job {job.job_id} to queue: {e}")
            raise
    
    def batch_submit_jobs(self, jobs: List[Job]) -> List[str]:
        """
        Submit multiple jobs to the queue in a batch.
        
        Args:
            jobs: The jobs to submit
            
        Returns:
            List of job IDs
            
        Raises:
            ClientError: If there is an error submitting the jobs to SQS
        """
        if not jobs:
            return []
        
        self.logger.info(f"Batch submitting {len(jobs)} jobs to queue")
        
        job_ids = []
        
        # SQS has a limit of 10 messages per batch request
        for i in range(0, len(jobs), 10):
            batch = jobs[i:i+10]
            entries = []
            
            for j, job in enumerate(batch):
                entries.append({
                    'Id': str(j),  # A unique ID for the batch operation, not the job ID
                    'MessageBody': job.to_json(),
                    'MessageAttributes': job.get_sqs_message_attributes(),
                    'MessageGroupId': job.get_sqs_message_group_id(),
                    'MessageDeduplicationId': job.get_sqs_deduplication_id()
                })
                
                job_ids.append(job.job_id)
            
            try:
                response = self.sqs.send_message_batch(
                    QueueUrl=self.config.queue_url,
                    Entries=entries
                )
                
                # Check for failed messages
                if 'Failed' in response and response['Failed']:
                    failed_messages = response['Failed']
                    self.logger.error(f"Failed to submit {len(failed_messages)} jobs in batch: {failed_messages}")
                    
                    # You could handle retries here if needed
                    
            except ClientError as e:
                self.logger.error(f"Error batch submitting jobs to queue: {e}")
                raise
        
        return job_ids
    
    def receive_jobs(self, max_messages: int = None) -> List[Tuple[Job, str]]:
        """
        Receive jobs from the queue.
        
        Args:
            max_messages: Maximum number of messages to receive (defaults to config.max_batch_size)
            
        Returns:
            List of (job, receipt_handle) tuples
            
        Raises:
            ClientError: If there is an error receiving messages from SQS
        """
        if max_messages is None:
            max_messages = self.config.max_batch_size
        
        max_messages = min(max_messages, 10)  # SQS limits to 10 messages max
        
        try:
            response = self.sqs.receive_message(
                QueueUrl=self.config.queue_url,
                MaxNumberOfMessages=max_messages,
                MessageAttributeNames=['All'],
                AttributeNames=['All'],
                WaitTimeSeconds=self.config.wait_time_seconds,
                VisibilityTimeout=self.config.visibility_timeout_seconds
            )
            
            messages = response.get('Messages', [])
            self.logger.debug(f"Received {len(messages)} messages from queue")
            
            result = []
            for message in messages:
                try:
                    job = Job.from_json(message['Body'])
                    receipt_handle = message['ReceiptHandle']
                    result.append((job, receipt_handle))
                except (json.JSONDecodeError, KeyError) as e:
                    self.logger.error(f"Error parsing message: {e}")
                    # Consider deleting invalid messages
            
            return result
            
        except ClientError as e:
            self.logger.error(f"Error receiving messages from queue: {e}")
            raise
    
    def delete_job(self, receipt_handle: str) -> bool:
        """
        Delete a job from the queue.
        
        Args:
            receipt_handle: The receipt handle for the job message
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ClientError: If there is an error deleting the message from SQS
        """
        try:
            self.sqs.delete_message(
                QueueUrl=self.config.queue_url,
                ReceiptHandle=receipt_handle
            )
            return True
        except ClientError as e:
            self.logger.error(f"Error deleting message from queue: {e}")
            return False
    
    def extend_visibility_timeout(self, receipt_handle: str, additional_seconds: int) -> bool:
        """
        Extend the visibility timeout for a job.
        
        This is useful for long-running jobs to prevent them from being reprocessed.
        
        Args:
            receipt_handle: The receipt handle for the job message
            additional_seconds: Additional seconds to add to the visibility timeout
            
        Returns:
            True if successful, False otherwise
            
        Raises:
            ClientError: If there is an error extending the visibility timeout
        """
        try:
            self.sqs.change_message_visibility(
                QueueUrl=self.config.queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=additional_seconds
            )
            return True
        except ClientError as e:
            self.logger.error(f"Error extending message visibility: {e}")
            return False
    
    def retry_job(self, job: Job) -> Optional[str]:
        """
        Retry a failed job.
        
        Args:
            job: The job to retry
            
        Returns:
            The job ID if successful, None otherwise
        """
        if not job.can_retry():
            self.logger.warning(f"Job {job.job_id} cannot be retried (attempts: {job.attempt_count}/{job.max_attempts})")
            return None
        
        # Increment the attempt count
        job.increment_attempt()
        
        # Reset the status to pending
        job.status = JobStatus.PENDING
        job.started_at = None
        job.completed_at = None
        
        # Calculate the delay for retrying based on the retry strategy
        delay_seconds = self._calculate_retry_delay(job)
        
        try:
            # Send the message with a delay
            response = self.sqs.send_message(
                QueueUrl=self.config.queue_url,
                MessageBody=job.to_json(),
                MessageAttributes=job.get_sqs_message_attributes(),
                MessageGroupId=job.get_sqs_message_group_id(),
                MessageDeduplicationId=job.get_sqs_deduplication_id(),
                DelaySeconds=min(delay_seconds, 900)  # SQS max delay is 15 minutes (900 seconds)
            )
            
            self.logger.info(f"Job {job.job_id} scheduled for retry (attempt {job.attempt_count}) with delay of {delay_seconds} seconds")
            return job.job_id
            
        except ClientError as e:
            self.logger.error(f"Error retrying job {job.job_id}: {e}")
            return None
    
    def _calculate_retry_delay(self, job: Job) -> int:
        """
        Calculate the delay for retrying a job based on the retry strategy.
        
        Args:
            job: The job to calculate the delay for
            
        Returns:
            The delay in seconds
        """
        attempt = job.attempt_count
        base_seconds = self.config.base_retry_seconds
        
        if self.config.retry_strategy == JobRetryStrategy.NONE:
            return 0
        elif self.config.retry_strategy == JobRetryStrategy.IMMEDIATE:
            return 0
        elif self.config.retry_strategy == JobRetryStrategy.LINEAR_BACKOFF:
            return attempt * base_seconds
        elif self.config.retry_strategy == JobRetryStrategy.EXPONENTIAL_BACKOFF:
            return base_seconds * (2 ** (attempt - 1))
        else:
            return base_seconds
    
    def process_jobs(self, handler: Callable[[Job], bool], interval_seconds: float = 5.0) -> None:
        """
        Process jobs from the queue continuously.
        
        Args:
            handler: Function to process a job, should return True if successful
            interval_seconds: Time to wait between polling for new jobs
        """
        self._stop_processing.clear()
        
        self.logger.info("Starting job processing loop")
        
        while not self._stop_processing.is_set():
            try:
                # Receive jobs from the queue
                job_tuples = self.receive_jobs()
                
                for job, receipt_handle in job_tuples:
                    self.logger.info(f"Processing job {job.name} ({job.job_id})")
                    
                    try:
                        # Mark the job as started
                        job.mark_started()
                        
                        # Process the job
                        success = handler(job)
                        
                        # Mark the job as completed
                        job.mark_completed(success=success)
                        
                        if success:
                            self.logger.info(f"Job {job.job_id} completed successfully")
                            # Delete the message from the queue
                            self.delete_job(receipt_handle)
                        else:
                            self.logger.warning(f"Job {job.job_id} failed")
                            
                            # Delete the message from the queue
                            self.delete_job(receipt_handle)
                            
                            # Retry if needed
                            if job.can_retry():
                                self.retry_job(job)
                                
                    except Exception as e:
                        self.logger.error(f"Error processing job {job.job_id}: {e}", exc_info=True)
                        
                        # Mark the job as failed
                        job.mark_completed(success=False, error_message=str(e))
                        
                        # Delete the message from the queue
                        self.delete_job(receipt_handle)
                        
                        # Retry if needed
                        if job.can_retry():
                            self.retry_job(job)
                
                # Wait for a bit before polling again if no messages were received
                if not job_tuples:
                    time.sleep(interval_seconds)
                    
            except Exception as e:
                self.logger.error(f"Error in job processing loop: {e}", exc_info=True)
                time.sleep(interval_seconds)
        
        self.logger.info("Job processing loop stopped")
    
    def start_processing(self, handler: Callable[[Job], bool], interval_seconds: float = 5.0) -> threading.Thread:
        """
        Start processing jobs in a background thread.
        
        Args:
            handler: Function to process a job, should return True if successful
            interval_seconds: Time to wait between polling for new jobs
            
        Returns:
            The processing thread
        """
        thread = threading.Thread(
            target=self.process_jobs,
            args=(handler, interval_seconds),
            daemon=True
        )
        
        thread.start()
        return thread
    
    def stop_processing(self) -> None:
        """Stop processing jobs."""
        self._stop_processing.set()
        self.logger.info("Stopping job processing")
    
    def get_queue_attributes(self) -> Dict[str, Any]:
        """
        Get attributes of the SQS queue.
        
        Returns:
            Queue attributes
            
        Raises:
            ClientError: If there is an error getting queue attributes
        """
        try:
            response = self.sqs.get_queue_attributes(
                QueueUrl=self.config.queue_url,
                AttributeNames=['All']
            )
            
            return response.get('Attributes', {})
            
        except ClientError as e:
            self.logger.error(f"Error getting queue attributes: {e}")
            raise
    
    def get_approximate_number_of_messages(self) -> int:
        """
        Get the approximate number of messages in the queue.
        
        Returns:
            The approximate number of messages
            
        Raises:
            ClientError: If there is an error getting queue attributes
        """
        attributes = self.get_queue_attributes()
        return int(attributes.get('ApproximateNumberOfMessages', 0)) 
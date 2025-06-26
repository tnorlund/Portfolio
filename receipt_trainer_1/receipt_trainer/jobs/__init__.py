"""
Job Queue System for ML training jobs.

This module provides a job queue system for managing ML training jobs, including:

1. Job class for defining ML training jobs
2. JobQueue class for managing job queues using AWS SQS
3. AWS utilities for creating and managing SQS queues
4. Command-line interface for job queue management
5. Job definition models for LayoutLM training configuration
6. Standardized job processor interface for different environments
"""

from .aws import (create_fifo_queue, create_queue_with_dlq,
                  create_standard_queue, delete_queue, get_queue_url,
                  purge_queue)
from .job import Job, JobPriority, JobStatus
from .job_definition import (CheckpointConfig, DatasetConfig, JobDependency,
                             LayoutLMJobDefinition, ModelConfig,
                             NotificationConfig, OutputConfig, ResourceConfig,
                             TrainingConfig)
from .processor import (EC2JobProcessor, JobProcessor, ProcessingMode,
                        SQSJobProcessor, create_job_processor)
from .queue import JobQueue, JobQueueConfig, JobRetryStrategy

__all__ = [
    "Job",
    "JobStatus",
    "JobPriority",
    "JobQueue",
    "JobQueueConfig",
    "JobRetryStrategy",
    "JobProcessor",
    "SQSJobProcessor",
    "EC2JobProcessor",
    "ProcessingMode",
    "create_job_processor",
    "LayoutLMJobDefinition",
    "ResourceConfig",
    "ModelConfig",
    "TrainingConfig",
    "DatasetConfig",
    "CheckpointConfig",
    "OutputConfig",
    "NotificationConfig",
    "JobDependency",
    "create_standard_queue",
    "create_fifo_queue",
    "get_queue_url",
    "delete_queue",
    "create_queue_with_dlq",
    "purge_queue",
]

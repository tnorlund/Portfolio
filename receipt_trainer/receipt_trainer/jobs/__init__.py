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

from .job import Job, JobStatus, JobPriority
from .queue import JobQueue, JobQueueConfig, JobRetryStrategy
from .processor import (
    JobProcessor,
    SQSJobProcessor,
    EC2JobProcessor,
    ProcessingMode,
    create_job_processor,
)
from .job_definition import (
    LayoutLMJobDefinition,
    ResourceConfig,
    ModelConfig,
    TrainingConfig,
    DatasetConfig,
    CheckpointConfig,
    OutputConfig,
    NotificationConfig,
    JobDependency,
)
from .aws import (
    create_standard_queue,
    create_fifo_queue,
    get_queue_url,
    delete_queue,
    create_queue_with_dlq,
    purge_queue,
)

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

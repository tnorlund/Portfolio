#!/usr/bin/env python
"""
Example script demonstrating how to use the LayoutLMJobDefinition class.

This script shows how to:
1. Load a job definition from a YAML file
2. Validate the job definition
3. Convert it to a Job object
4. Submit the job to the job queue
"""

import os
import sys
import logging
import argparse
from typing import Optional

# Add the parent directory to the path so we can import the jobs package
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
)

from receipt_trainer.jobs.job import Job, JobPriority
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig
from receipt_trainer.jobs.job_definition import LayoutLMJobDefinition
from receipt_trainer.jobs.aws import get_queue_url


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def load_job_definition(file_path: str) -> LayoutLMJobDefinition:
    """Load a job definition from a file."""
    if file_path.endswith(".yaml") or file_path.endswith(".yml"):
        logger.info(f"Loading job definition from YAML file: {file_path}")
        return LayoutLMJobDefinition.from_yaml(file_path)
    elif file_path.endswith(".json"):
        logger.info(f"Loading job definition from JSON file: {file_path}")
        return LayoutLMJobDefinition.from_json(file_path)
    else:
        raise ValueError(f"Unsupported file format: {file_path}")


def convert_to_job(job_definition: LayoutLMJobDefinition) -> Job:
    """Convert a LayoutLMJobDefinition to a Job object."""
    # Map priority from 1-10 scale to JobPriority enum
    priority_map = {
        1: JobPriority.LOW,
        2: JobPriority.LOW,
        3: JobPriority.LOW,
        4: JobPriority.MEDIUM,
        5: JobPriority.MEDIUM,
        6: JobPriority.MEDIUM,
        7: JobPriority.MEDIUM,
        8: JobPriority.HIGH,
        9: JobPriority.HIGH,
        10: JobPriority.CRITICAL,
    }

    # Convert job definition to Job config dict
    job_config = job_definition.to_job_config()

    # Create Job object
    job = Job(
        name=job_definition.name,
        type="layoutlm_training",
        config=job_config,
        priority=priority_map.get(job_definition.priority, JobPriority.MEDIUM),
        timeout_seconds=job_definition.resources.max_runtime,
        tags={
            "model_type": job_definition.model.type,
            "model_version": job_definition.model.version,
            **{f"tag_{i}": tag for i, tag in enumerate(job_definition.tags)},
        },
    )

    return job


def submit_job_to_queue(
    job: Job, queue_name: str, aws_region: Optional[str] = None
) -> str:
    """Submit a job to the job queue."""
    # Get queue URL
    queue_url = get_queue_url(queue_name, aws_region)

    # Create job queue
    queue_config = JobQueueConfig(queue_url=queue_url, aws_region=aws_region)
    job_queue = JobQueue(queue_config)

    # Submit job
    job_id = job_queue.submit_job(job)

    return job_id


def main():
    """Main function."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Submit a LayoutLM training job")
    parser.add_argument(
        "job_file",
        help="Path to the job definition file (YAML or JSON)",
    )
    parser.add_argument(
        "--queue-name",
        default="layoutlm-training-jobs",
        help="Name of the SQS queue to submit the job to",
    )
    parser.add_argument(
        "--aws-region",
        help="AWS region for the SQS queue",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate the job definition, don't submit to queue",
    )
    parser.add_argument(
        "--output",
        help="Path to save the processed job definition (for validation)",
    )

    args = parser.parse_args()

    try:
        # Load job definition
        job_definition = load_job_definition(args.job_file)
        logger.info(f"Job definition loaded: {job_definition.name}")

        # If output path is specified, save the processed job definition
        if args.output:
            if args.output.endswith(".yaml") or args.output.endswith(".yml"):
                job_definition.to_yaml(args.output)
                logger.info(f"Job definition saved to YAML file: {args.output}")
            elif args.output.endswith(".json"):
                job_definition.to_json(args.output)
                logger.info(f"Job definition saved to JSON file: {args.output}")
            else:
                logger.warning(f"Unsupported output format: {args.output}")

        # If only validating, exit here
        if args.validate_only:
            logger.info("Job definition validated successfully")
            return

        # Convert to Job object
        job = convert_to_job(job_definition)
        logger.info(f"Job object created with ID: {job.job_id}")

        # Submit job to queue
        job_id = submit_job_to_queue(job, args.queue_name, args.aws_region)
        logger.info(f"Job submitted to queue: {args.queue_name}")
        logger.info(f"Job ID: {job_id}")

    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

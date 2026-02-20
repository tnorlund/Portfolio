"""Lambda handler to queue CoreML export after SageMaker training completion.

This Lambda is triggered by EventBridge when a SageMaker training job
completes successfully. It:
1. Looks up the Job entity in DynamoDB to get the best checkpoint S3 path
2. Creates a CoreMLExportJob record in DynamoDB
3. Sends a message to the export job queue for the Mac worker to process

Uses the receipt_dynamo package (via Lambda layer) for proper entity access.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

# receipt_dynamo is provided via Lambda layer
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CoreMLExportStatus
from receipt_dynamo.entities import CoreMLExportJob

# Initialize clients
sqs = boto3.client("sqs")


def handler(event, context):
    """Process SageMaker training job completion event.

    EventBridge event format:
    {
        "source": "aws.sagemaker",
        "detail-type": "SageMaker Training Job State Change",
        "detail": {
            "TrainingJobName": "layoutlm-20240101-120000",
            "TrainingJobStatus": "Completed",
            "TrainingJobArn": "arn:aws:sagemaker:...",
            ...
        }
    }
    """
    print(f"Received event: {json.dumps(event)}")

    detail = event.get("detail", {})
    job_name = detail.get("TrainingJobName", "")
    status = detail.get("TrainingJobStatus", "")

    # Only process completed jobs
    if status != "Completed":
        print(f"Ignoring job {job_name} with status {status}")
        return {"statusCode": 200, "body": "Skipped - not completed"}

    # Only process layoutlm training jobs
    if not job_name.startswith("layoutlm-"):
        print(f"Ignoring non-layoutlm job: {job_name}")
        return {"statusCode": 200, "body": "Skipped - not layoutlm job"}

    # Check for opt-out tag
    job_arn = detail.get("TrainingJobArn", "")
    if _has_skip_export_tag(job_arn):
        print("Skipping export - job has skip-coreml-export tag")
        return {"statusCode": 200, "body": "Skipped - opt-out tag"}

    # Initialize DynamoDB client using receipt_dynamo
    dynamo_table = os.environ["DYNAMO_TABLE_NAME"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    dynamo = DynamoClient(table_name=dynamo_table, region=region)

    # Look up the Job entity by name using GSI2
    try:
        jobs, _ = dynamo.get_job_by_name(name=job_name, limit=1)
    except Exception as e:
        print(f"Failed to look up job by name: {e}")
        return {"statusCode": 500, "body": f"Failed to look up job: {e}"}

    if not jobs:
        print(f"Job not found in DynamoDB: {job_name}")
        return {"statusCode": 404, "body": f"Job not found: {job_name}"}

    job = jobs[0]
    print(f"Found job: {job.name} (ID: {job.job_id}, status: {job.status})")

    # Get the best checkpoint S3 path from job.results
    model_s3_uri = None
    if job.results:
        model_s3_uri = job.results.get("best_checkpoint_s3_path")

    # Fallback to best_dir_uri() helper method
    if not model_s3_uri:
        model_s3_uri = job.best_dir_uri()

    if not model_s3_uri:
        print(f"No best_checkpoint_s3_path in job results: {job_name}")
        print(f"Job results: {job.results}")
        return {"statusCode": 400, "body": "No best_checkpoint_s3_path found"}

    # Generate export job details
    export_id = str(uuid.uuid4())

    # Parse bucket from model S3 URI for output path
    # model_s3_uri format: s3://bucket/runs/job-name/best/
    output_bucket = os.environ.get("OUTPUT_BUCKET", "")
    if not output_bucket and model_s3_uri.startswith("s3://"):
        output_bucket = model_s3_uri.split("/")[2]

    output_s3_prefix = f"s3://{output_bucket}/coreml/{job_name}/"
    quantize = os.environ.get("DEFAULT_QUANTIZE", "") or None
    queue_url = os.environ["COREML_EXPORT_JOB_QUEUE_URL"]

    # Create CoreMLExportJob entity using proper class
    export_job = CoreMLExportJob(
        export_id=export_id,
        job_id=job.job_id,
        model_s3_uri=model_s3_uri,
        created_at=datetime.now(timezone.utc),
        status=CoreMLExportStatus.PENDING.value,
        quantize=quantize,
        output_s3_prefix=output_s3_prefix,
    )

    # Add to DynamoDB using proper data layer method
    try:
        dynamo.add_coreml_export_job(export_job)
        print(f"Created CoreMLExportJob record: {export_id}")
    except Exception as e:
        print(f"Failed to create DynamoDB record: {e}")
        return {
            "statusCode": 500,
            "body": f"Failed to create DynamoDB record: {e}",
        }

    # Send message to export queue
    message = {
        "export_id": export_id,
        "job_id": job.job_id,
        "model_s3_uri": model_s3_uri,
        "quantize": quantize,
        "output_s3_prefix": output_s3_prefix,
    }

    try:
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message),
        )
        print(f"Queued CoreML export: {export_id}")
        print(f"  Training Job: {job_name}")
        print(f"  Job ID: {job.job_id}")
        print(f"  Model: {model_s3_uri}")
        print(f"  Output: {output_s3_prefix}")
        print(f"  Quantize: {quantize}")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "export_id": export_id,
                    "job_name": job_name,
                    "job_id": job.job_id,
                    "model_s3_uri": model_s3_uri,
                }
            ),
        }
    except Exception as e:
        print(f"Failed to send SQS message: {e}")
        return {"statusCode": 500, "body": f"Failed to queue export: {e}"}


def _has_skip_export_tag(job_arn: str) -> bool:
    """Check if training job has the skip-coreml-export tag."""
    if not job_arn:
        return False

    try:
        sagemaker = boto3.client("sagemaker")
        response = sagemaker.list_tags(ResourceArn=job_arn)
        tags = {t["Key"]: t["Value"] for t in response.get("Tags", [])}
        return tags.get("skip-coreml-export", "").lower() == "true"
    except Exception as e:
        print(f"Failed to list tags: {e}")
        return False

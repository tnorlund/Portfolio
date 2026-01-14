"""Lambda handler to queue CoreML export after SageMaker training completion.

This Lambda is triggered by EventBridge when a SageMaker training job
completes successfully. It:
1. Looks up the Job entity in DynamoDB to get the best checkpoint S3 path
2. Creates a CoreMLExportJob record in DynamoDB
3. Sends a message to the export job queue for the Mac worker to process
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

# Initialize clients
sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")


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
        print(f"Skipping export - job has skip-coreml-export tag")
        return {"statusCode": 200, "body": "Skipped - opt-out tag"}

    # Look up the Job entity in DynamoDB to get the best checkpoint path
    dynamo_table = os.environ["DYNAMO_TABLE_NAME"]
    job_data = _get_job_by_name(dynamo_table, job_name)

    if not job_data:
        print(f"Job not found in DynamoDB: {job_name}")
        return {"statusCode": 404, "body": f"Job not found: {job_name}"}

    # Get the best checkpoint S3 path from job.results
    results = job_data.get("results", {})
    if isinstance(results, str):
        results = json.loads(results)

    model_s3_uri = results.get("best_checkpoint_s3_path")
    if not model_s3_uri:
        print(f"No best_checkpoint_s3_path in job results: {job_name}")
        print(f"Job results: {results}")
        return {"statusCode": 400, "body": "No best_checkpoint_s3_path found"}

    # Generate export job details
    export_id = str(uuid.uuid4())
    job_id = job_data.get("job_id", job_name)

    # Parse bucket from model S3 URI for output path
    # model_s3_uri format: s3://bucket/runs/job-name/best/
    output_bucket = os.environ.get("OUTPUT_BUCKET", "")
    if not output_bucket and model_s3_uri.startswith("s3://"):
        output_bucket = model_s3_uri.split("/")[2]

    output_s3_prefix = f"s3://{output_bucket}/coreml/{job_name}/"
    quantize = os.environ.get("DEFAULT_QUANTIZE", "float16")
    queue_url = os.environ["COREML_EXPORT_JOB_QUEUE_URL"]

    # Create CoreMLExportJob record in DynamoDB
    try:
        _create_export_job_record(
            dynamo_table=dynamo_table,
            export_id=export_id,
            job_id=job_id,
            model_s3_uri=model_s3_uri,
            quantize=quantize,
            output_s3_prefix=output_s3_prefix,
        )
    except Exception as e:
        print(f"Failed to create DynamoDB record: {e}")
        return {"statusCode": 500, "body": f"Failed to create DynamoDB record: {e}"}

    # Send message to export queue
    message = {
        "export_id": export_id,
        "job_id": job_id,
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
        print(f"  Job ID: {job_id}")
        print(f"  Model: {model_s3_uri}")
        print(f"  Output: {output_s3_prefix}")
        print(f"  Quantize: {quantize}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "export_id": export_id,
                "job_name": job_name,
                "job_id": job_id,
                "model_s3_uri": model_s3_uri,
            }),
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


def _get_job_by_name(table_name: str, job_name: str) -> dict | None:
    """Look up a Job by name using GSI1 (name index).

    The Job entity uses GSI1PK = "JOB" and GSI1SK = job_name for name lookups.
    """
    table = dynamodb.Table(table_name)

    try:
        # Query GSI1 for jobs by name
        response = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK = :sk",
            ExpressionAttributeValues={
                ":pk": "JOB",
                ":sk": job_name,
            },
            Limit=1,
        )

        items = response.get("Items", [])
        if items:
            return items[0]

        # Fallback: scan for job by name (less efficient but more robust)
        response = table.scan(
            FilterExpression="#n = :name AND #t = :type",
            ExpressionAttributeNames={
                "#n": "name",
                "#t": "entity_type",
            },
            ExpressionAttributeValues={
                ":name": job_name,
                ":type": "Job",
            },
            Limit=1,
        )

        items = response.get("Items", [])
        return items[0] if items else None

    except Exception as e:
        print(f"Failed to look up job: {e}")
        return None


def _create_export_job_record(
    dynamo_table: str,
    export_id: str,
    job_id: str,
    model_s3_uri: str,
    quantize: str,
    output_s3_prefix: str,
) -> None:
    """Create a CoreMLExportJob record in DynamoDB."""
    table = dynamodb.Table(dynamo_table)

    now = datetime.now(timezone.utc).isoformat()

    item = {
        "PK": f"COREML_EXPORT#{export_id}",
        "SK": f"COREML_EXPORT#{export_id}",
        "entity_type": "CoreMLExportJob",
        "export_id": export_id,
        "job_id": job_id,
        "model_s3_uri": model_s3_uri,
        "quantize": quantize,
        "output_s3_prefix": output_s3_prefix,
        "status": "PENDING",
        "created_at": now,
        # GSI1: Query by job_id
        "GSI1PK": f"JOB#{job_id}",
        "GSI1SK": f"COREML_EXPORT#{export_id}",
        # GSI2: Query by status
        "GSI2PK": "COREML_EXPORT_STATUS#PENDING",
        "GSI2SK": now,
    }

    table.put_item(Item=item)
    print(f"Created CoreMLExportJob record: {export_id}")

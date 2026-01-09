"""Lambda handler for processing CoreML export results from SQS.

This Lambda is triggered by the results queue when the macOS worker
sends export completion messages. It updates the DynamoDB records with
the export results.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

# receipt_dynamo is available via Lambda layer
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CoreMLExportStatus


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process CoreML export results from SQS.

    Args:
        event: SQS event with Records array
        context: Lambda context

    Returns:
        Response dict with processing status
    """
    table_name = os.environ.get("DYNAMO_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMO_TABLE_NAME environment variable required")

    dynamo = DynamoClient(table_name=table_name)
    records = event.get("Records", [])

    processed = 0
    errors: List[str] = []

    for record in records:
        try:
            message = json.loads(record["body"])
            process_export_result(dynamo, message)
            processed += 1
        except Exception as e:
            error_msg = f"Error processing record: {e}"
            print(error_msg)
            errors.append(error_msg)

    result = {
        "statusCode": 200,
        "body": json.dumps(
            {
                "processed": processed,
                "errors": len(errors),
                "error_messages": errors[:10],  # Limit error messages
            }
        ),
    }

    print(f"Processed {processed} records, {len(errors)} errors")
    return result


def process_export_result(
    dynamo: DynamoClient, message: Dict[str, Any]
) -> None:
    """Process a single export result message.

    Args:
        dynamo: DynamoDB client
        message: Export result message from worker
    """
    export_id = message["export_id"]
    job_id = message["job_id"]
    status = message["status"]

    print(
        f"Processing export result: {export_id} for job {job_id}, status={status}"
    )

    # Get and update the export job record
    try:
        export_job = dynamo.get_coreml_export_job(export_id)
    except Exception as e:
        print(f"Export job {export_id} not found: {e}")
        # Create a new record if it doesn't exist (shouldn't happen normally)
        return

    # Update status
    if status == "SUCCESS":
        export_job.status = CoreMLExportStatus.SUCCEEDED.value
        export_job.mlpackage_s3_uri = message.get("mlpackage_s3_uri")
        export_job.bundle_s3_uri = message.get("bundle_s3_uri")
        export_job.model_size_bytes = message.get("model_size_bytes")
    else:
        export_job.status = CoreMLExportStatus.FAILED.value
        export_job.error_message = message.get(
            "error_message", "Unknown error"
        )

    export_job.export_duration_seconds = message.get("export_duration_seconds")
    export_job.updated_at = datetime.utcnow()
    export_job.completed_at = datetime.utcnow()

    dynamo.update_coreml_export_job(export_job)
    print(f"Updated export job {export_id} status to {export_job.status}")

    # Also update the training Job record with the CoreML bundle location
    if status == "SUCCESS" and export_job.bundle_s3_uri:
        try:
            job = dynamo.get_job(job_id)
            if job.results is None:
                job.results = {}
            job.results["coreml_bundle_s3_uri"] = export_job.bundle_s3_uri
            job.results["coreml_mlpackage_s3_uri"] = (
                export_job.mlpackage_s3_uri
            )
            dynamo.update_job(job)
            print(f"Updated job {job_id} with CoreML bundle path")
        except Exception as e:
            # Don't fail if we can't update the job - export still succeeded
            print(f"Warning: Could not update job {job_id}: {e}")

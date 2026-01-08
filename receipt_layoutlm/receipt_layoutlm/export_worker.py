"""CoreML Export Worker for processing export jobs from SQS.

This module polls the CoreML export job queue and processes export requests
on macOS. It must run on macOS since coremltools only works on Apple platforms.

Usage via CLI:
    python -m receipt_layoutlm.cli export-worker --continuous
    python -m receipt_layoutlm.cli export-worker --once
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import boto3

from .export_coreml import export_coreml


def download_from_s3(s3_uri: str, local_dir: str) -> str:
    """Download all files from an S3 prefix to a local directory.

    Args:
        s3_uri: S3 URI (s3://bucket/prefix/)
        local_dir: Local directory to download to

    Returns:
        Path to the local directory containing the downloaded files
    """
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    # Ensure prefix ends with / for proper listing
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    local_path = Path(local_dir)
    local_path.mkdir(parents=True, exist_ok=True)

    print(f"Downloading from {s3_uri} to {local_dir}...")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel_path = key[len(prefix) :].lstrip("/")
            if not rel_path:
                continue

            file_path = local_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)

            print(f"  Downloading {rel_path}...")
            s3.download_file(bucket, key, str(file_path))

    return str(local_path)


def upload_to_s3(local_path: str, s3_prefix: str) -> str:
    """Upload a directory to S3.

    Args:
        local_path: Local path to upload (file or directory)
        s3_prefix: S3 URI prefix (s3://bucket/prefix/)

    Returns:
        S3 URI to the uploaded content
    """
    parsed = urlparse(s3_prefix)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    s3 = boto3.client("s3")
    local = Path(local_path)

    print(f"Uploading {local_path} to {s3_prefix}...")

    if local.is_file():
        key = prefix + local.name
        print(f"  Uploading {local.name}...")
        s3.upload_file(str(local), bucket, key)
        return f"s3://{bucket}/{key}"
    else:
        # Upload directory recursively
        for file_path in local.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(local)
                key = prefix + str(rel_path)
                print(f"  Uploading {rel_path}...")
                s3.upload_file(str(file_path), bucket, key)

        return f"s3://{bucket}/{prefix}"


def get_directory_size(path: str) -> int:
    """Get total size of a directory in bytes."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            total += os.path.getsize(filepath)
    return total


def process_export_job(message: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single CoreML export job.

    Args:
        message: Job queue message with export parameters

    Returns:
        Result message to send to results queue
    """
    export_id = message["export_id"]
    job_id = message["job_id"]
    model_s3_uri = message["model_s3_uri"]
    quantize = message.get("quantize", "float16")
    output_s3_prefix = message["output_s3_prefix"]

    print(f"\nProcessing export job {export_id}")
    print(f"  Job ID: {job_id}")
    print(f"  Model: {model_s3_uri}")
    print(f"  Quantize: {quantize}")
    print(f"  Output: {output_s3_prefix}")

    start_time = time.time()

    try:
        with tempfile.TemporaryDirectory(prefix="coreml_export_") as tmpdir:
            # Download model checkpoint from S3
            model_dir = os.path.join(tmpdir, "model")
            download_from_s3(model_s3_uri, model_dir)

            # Run CoreML export
            output_dir = os.path.join(tmpdir, "output")
            print("\nRunning CoreML export...")

            bundle_path = export_coreml(
                checkpoint_dir=model_dir,
                output_dir=output_dir,
                model_name="LayoutLM",
                quantize=quantize,
            )

            # Get exported model size
            model_size = get_directory_size(bundle_path)
            print(f"Exported model size: {model_size / 1024 / 1024:.1f} MB")

            # Upload to S3
            bundle_s3_uri = upload_to_s3(bundle_path, output_s3_prefix)
            mlpackage_s3_uri = f"{bundle_s3_uri}LayoutLM.mlpackage"

            duration = time.time() - start_time

            print(f"\nExport completed in {duration:.1f}s")
            print(f"  Bundle: {bundle_s3_uri}")

            return {
                "export_id": export_id,
                "job_id": job_id,
                "status": "SUCCESS",
                "mlpackage_s3_uri": mlpackage_s3_uri,
                "bundle_s3_uri": bundle_s3_uri,
                "model_size_bytes": model_size,
                "export_duration_seconds": duration,
            }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)
        print(f"\nExport failed: {error_msg}")

        return {
            "export_id": export_id,
            "job_id": job_id,
            "status": "FAILED",
            "error_message": error_msg,
            "export_duration_seconds": duration,
        }


def update_export_status(
    dynamo_client,
    export_id: str,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """Update the export job status in DynamoDB.

    Args:
        dynamo_client: DynamoDB client instance
        export_id: Export job ID
        status: New status
        error_message: Error message if failed
    """
    from receipt_dynamo.constants import CoreMLExportStatus

    try:
        export_job = dynamo_client.get_coreml_export_job(export_id)
        export_job.status = status
        export_job.updated_at = datetime.utcnow()

        if status in (
            CoreMLExportStatus.SUCCEEDED.value,
            CoreMLExportStatus.FAILED.value,
        ):
            export_job.completed_at = datetime.utcnow()

        if error_message:
            export_job.error_message = error_message

        dynamo_client.update_coreml_export_job(export_job)
        print(f"Updated export job {export_id} status to {status}")
    except Exception as e:
        print(f"Warning: Failed to update export job status: {e}")


def run_worker(
    job_queue_url: str,
    results_queue_url: str,
    dynamo_table: Optional[str] = None,
    region: str = "us-east-1",
    run_once: bool = False,
) -> None:
    """Run the CoreML export worker.

    Args:
        job_queue_url: URL of the job queue to poll
        results_queue_url: URL of the results queue to send results
        dynamo_table: DynamoDB table name for status updates (optional)
        region: AWS region
        run_once: If True, process one batch then exit
    """
    sqs = boto3.client("sqs", region_name=region)

    # Optional DynamoDB client for status updates
    dynamo_client = None
    if dynamo_table:
        try:
            from receipt_dynamo import DynamoClient

            dynamo_client = DynamoClient(table_name=dynamo_table, region=region)
            print(f"Connected to DynamoDB table: {dynamo_table}")
        except Exception as e:
            print(f"Warning: Could not connect to DynamoDB: {e}")

    print("Starting CoreML export worker...")
    print(f"  Job queue: {job_queue_url}")
    print(f"  Results queue: {results_queue_url}")
    print(f"  Mode: {'single batch' if run_once else 'continuous'}")

    iteration = 0
    while True:
        iteration += 1
        print(f"\n--- Polling iteration {iteration} ---")

        try:
            # Long poll for messages (up to 20 seconds)
            response = sqs.receive_message(
                QueueUrl=job_queue_url,
                MaxNumberOfMessages=1,  # Process one at a time
                VisibilityTimeout=3600,  # 1 hour for large models
                WaitTimeSeconds=20,
            )

            messages = response.get("Messages", [])

            if not messages:
                print("No messages in queue")
                if run_once:
                    print("Exiting (--once mode)")
                    break
                continue

            for msg in messages:
                receipt_handle = msg["ReceiptHandle"]
                body = json.loads(msg["Body"])

                export_id = body.get("export_id", "unknown")

                # Update status to RUNNING
                if dynamo_client:
                    update_export_status(dynamo_client, export_id, "RUNNING")

                # Process the export
                result = process_export_job(body)

                # Update DynamoDB with results
                if dynamo_client:
                    update_export_status(
                        dynamo_client,
                        export_id,
                        result["status"],
                        result.get("error_message"),
                    )

                # Send result to results queue
                print("Sending result to results queue...")
                sqs.send_message(
                    QueueUrl=results_queue_url,
                    MessageBody=json.dumps(result),
                )

                # Delete message from job queue
                print("Deleting message from job queue...")
                sqs.delete_message(
                    QueueUrl=job_queue_url,
                    ReceiptHandle=receipt_handle,
                )

                print(f"Job {export_id} completed with status: {result['status']}")

            if run_once:
                print("Exiting (--once mode)")
                break

        except KeyboardInterrupt:
            print("\nShutting down worker...")
            break
        except Exception as e:
            print(f"Error processing messages: {e}")
            if run_once:
                raise
            # Continue polling after error
            time.sleep(5)


def check_macos() -> None:
    """Verify we're running on macOS."""
    if sys.platform != "darwin":
        print("Error: This worker must run on macOS (coremltools requirement)")
        sys.exit(1)

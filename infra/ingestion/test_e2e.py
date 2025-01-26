import boto3
import pytest
import pulumi.automation as auto
import os
import subprocess
import time
from pathlib import Path
from .utils import (
    backup_raw_s3,
    backup_cdn_s3,
    backup_dynamo_items,
    group_images,
    delete_raw_s3,
    delete_cdn_s3,
    delete_dynamo_items,
    restore_s3,
    restore_dynamo_items,
)
from dynamo import DynamoClient


@pytest.fixture(scope="session")
def pulumi_outputs():
    """
    Session-scoped fixture to fetch Pulumi outputs once.
    """
    stack = auto.select_stack(
        stack_name="tnorlund/portfolio/e2e",
        project_name="portfolio",
        program=lambda: None,
    )

    # Convert Pulumi OutputValue objects to raw Python values
    return {key: val.value for key, val in stack.outputs().items()}


@pytest.fixture
def setup_and_cleanup(pulumi_outputs):
    """
    A fixture that:
     1. Backs up existing S3 files (RAW and CDN) and DynamoDB items
     2. Deletes them (so the environment is 'clean' for testing)
     3. Yields the expected results to the test
     4. Restores original data after the test
    """
    print()
    # Retrieve references from Pulumi outputs
    raw_bucket = pulumi_outputs["image_bucket_name"]
    cdn_bucket = pulumi_outputs["bucketName"]
    dynamo_table = pulumi_outputs["table_name"]

    # Backup existing data
    print("Backing up existing data...")
    raw_keys = backup_raw_s3(raw_bucket)
    cdn_keys = backup_cdn_s3(cdn_bucket)
    dynamo_backup_path = backup_dynamo_items(dynamo_table)

    # Delete existing data
    print("Deleting existing data...")
    delete_raw_s3(raw_bucket)
    delete_cdn_s3(cdn_bucket)
    delete_dynamo_items(dynamo_table)

    # Yield control to the test
    yield group_images(raw_keys, cdn_keys, dynamo_backup_path)
    print()

    # Restore original data
    print("Restoring original data...")
    restore_s3(raw_bucket, raw_keys)
    restore_s3(cdn_bucket, cdn_keys)
    restore_dynamo_items(dynamo_table, dynamo_backup_path)


def test_e2e(setup_and_cleanup, pulumi_outputs):
    """
    Example E2E test that:
    1) Uses setup_and_cleanup fixture to ensure a clean environment
    2) Runs your local upload script
    3) Polls or waits to let the Lambda do its job
    4) Validates S3 and Dynamo state
    """
    # Use the Pulumi outputs for resource names:
    raw_bucket = pulumi_outputs["image_bucket_name"]
    cdn_bucket = pulumi_outputs["bucketName"]
    dynamo_table = pulumi_outputs["table_name"]

    print("Running E2E test...")
    print(setup_and_cleanup)
    # 1. ARRANGE (we already did environment cleanup in the fixture)
    # Maybe mock the UUID if you want consistent naming
    # Or set some environment variables to point to "dev" or "test" stack

    # 2. ACT: run your local script
    # For example:
    # script_path = Path(__file__).parent.parent / "upload_images_to_s3.py"
    # cmd = ["python", str(script_path), "--directory_to_upload", "./test_assets", "--env", "dev"]
    # subprocess.run(cmd, check=True)

    # Wait/poll for Lambda to complete (if async):
    time.sleep(5)  # or a small loop that checks Dynamo until items appear

    # 3. ASSERT: check results in S3, Dynamo, etc.
    # s3_client = aws_clients["s3"]
    # dynamo_client = aws_clients["dynamo"]

    # Example minimal assertion (pseudo-code):
    # raw_files = list_s3_keys(s3_client, raw_bucket)
    # assert len(raw_files) > 0, "No files uploaded to RAW bucket!"
    #
    # cdn_files = list_s3_keys(s3_client, cdn_bucket)
    # assert len(cdn_files) > 0, "No files uploaded to CDN bucket!"
    #
    # items = dynamo_client.scan(TableName=dynamo_table).get("Items", [])
    # assert len(items) > 0, "No items found in DynamoDB!"

    print("Test completed successfully.")

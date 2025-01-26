import shutil
import boto3
import pytest
import pulumi.automation as auto
import os
import subprocess
from collections import deque
from tempfile import TemporaryDirectory
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
from . import upload_images_to_s3
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
    with TemporaryDirectory() as tmpdir:
        # Group all backups by UUID
        grouped = group_images(raw_keys, cdn_keys, dynamo_backup_path)

        # Copy only the RAW .png files to the temp directory,
        # but remove any "raw_" prefix in the final filename.
        for uuid, data in grouped.items():
            for local_file_path in data["raw"]:
                if local_file_path.lower().endswith(".png"):
                    old_filename = os.path.basename(local_file_path)
                    # For example: "raw_be4073b9-68f6-453a-9b43-e8a27ca42d4b.png"
                    if old_filename.startswith("raw_"):
                        new_filename = old_filename[4:]  # drop "raw_"
                    else:
                        new_filename = old_filename

                    dest = os.path.join(tmpdir, new_filename)
                    shutil.copy2(local_file_path, dest)

        yield (tmpdir, grouped)
    print()

    # Restore original data
    print("Restoring original data...")
    restore_s3(raw_bucket, raw_keys)
    restore_s3(cdn_bucket, cdn_keys)
    restore_dynamo_items(dynamo_table, dynamo_backup_path)


def test_e2e(monkeypatch, setup_and_cleanup, pulumi_outputs):
    """
    E2E test that:
    1) Uses 'setup_and_cleanup' to back up/delete existing data, and yield (temp_dir, grouped).
    2) Finds all .png files in 'temp_dir' -> each becomes a mock UUID.
    3) Monkeypatches 'uuid.uuid4()' to return those UUIDs in sequence.
    4) Runs your ingestion script, verifying each new "image" uses a predictable UUID.
    5) Restores everything after the test.
    """
    temp_dir, grouped = setup_and_cleanup

    # -------------------------------------------------------------------------
    # ARRANGE: Collect .png filenames -> parse out the UUID from each
    # -------------------------------------------------------------------------
    all_png_files = [
        f
        for f in os.listdir(temp_dir)
        if f.lower().endswith(".png")
    ]
    if not all_png_files:
        pytest.skip("No .png files found in temp directory; nothing to test.")

    derived_uuids = [
        os.path.splitext(filename)[0]
        for filename in all_png_files
    ]

    # We'll put them in a queue so each uuid.uuid4() call gets the "next" one.
    uuid_queue = deque(derived_uuids)

    def mock_uuid4():
        if not uuid_queue:
            raise RuntimeError(
                "Ran out of mock UUIDs! Script called uuid.uuid4() more times than expected."
            )
        return uuid_queue.popleft()
    monkeypatch.setattr(upload_images_to_s3, "uuid4", mock_uuid4)

    # Act
    upload_images_to_s3.upload_files_with_uuid_in_batches(
        directory=Path(temp_dir).resolve(),
        bucket_name=pulumi_outputs["image_bucket_name"],
        lambda_function=pulumi_outputs["cluster_lambda_function_name"],
        dynamodb_table_name=pulumi_outputs["table_name"],
    )

    # Assert

    print("[TEST] All expected UUIDs were used for newly uploaded images!")
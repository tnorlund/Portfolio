import os
import time
import shutil
import pytest
from pathlib import Path
from collections import deque
from tempfile import TemporaryDirectory

import pulumi.automation as auto

from .utils import (
    assert_dynamo,
    backup_raw_s3,
    backup_cdn_s3,
    backup_dynamo_items,
    group_images,
    delete_raw_s3,
    delete_cdn_s3,
    delete_dynamo_items,
    restore_s3,
    restore_dynamo_items,
    assert_s3_cdn,
    assert_s3_raw,
)
from . import upload_images_to_s3


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
    return {key: val.value for key, val in stack.outputs().items()}


@pytest.fixture
def setup_and_cleanup(pulumi_outputs):
    """
    This fixture:
      1. Backs up existing S3 files (RAW/CDN) + DynamoDB items.
      2. Deletes them (clean environment).
      3. Yields the data needed for testing.
      4. Restores everything afterward.
    """
    print("\nBacking up existing data...")
    raw_bucket = pulumi_outputs["raw_bucket_name"]
    cdn_bucket = pulumi_outputs["cdn_bucket_name"]
    dynamo_table = pulumi_outputs["dynamodb_table_name"]

    raw_keys = backup_raw_s3(raw_bucket)
    cdn_keys = backup_cdn_s3(cdn_bucket)
    dynamo_backup_path = backup_dynamo_items(dynamo_table)

    print("Deleting existing data...")
    delete_raw_s3(raw_bucket)
    delete_cdn_s3(cdn_bucket)
    delete_dynamo_items(dynamo_table)

    # Organize backups by UUID
    grouped = group_images(raw_keys, cdn_keys, dynamo_backup_path)

    # Create a temp directory for the test to use
    with TemporaryDirectory() as tmpdir:
        for uuid, data in grouped.items():
            for local_file_path in data["raw"]:
                if local_file_path.lower().endswith(".png"):
                    old_filename = os.path.basename(local_file_path)
                    if old_filename.startswith("raw_"):
                        new_filename = old_filename[4:]
                    else:
                        new_filename = old_filename
                    dest = os.path.join(tmpdir, new_filename)
                    shutil.copy2(local_file_path, dest)

        yield (tmpdir, grouped, cdn_keys, raw_keys, dynamo_backup_path)

    print("\nCleaning up test data...")
    delete_raw_s3(raw_bucket)
    delete_cdn_s3(cdn_bucket)
    delete_dynamo_items(dynamo_table)

    print("Restoring original data...")
    restore_s3(raw_bucket, raw_keys)
    restore_s3(cdn_bucket, cdn_keys)
    restore_dynamo_items(dynamo_table, dynamo_backup_path)


def test_e2e(monkeypatch, setup_and_cleanup, pulumi_outputs):
    """
    E2E test that:
      1) Backs up/deletes existing data via `setup_and_cleanup`.
      2) Collects .png files from the temp directory for testing.
      3) Mocks out uuid.uuid4() calls to provide predictable UUIDs.
      4) Calls the ingestion script to upload images + trigger cluster lambda + store in Dynamo.
      5) Verifies final S3 (RAW/CDN) + Dynamo states match the originals.
      6) Restores original data automatically after test completion.
    """
    temp_dir, grouped, cdn_keys, raw_keys, dynamo_backup_path = setup_and_cleanup

    all_png_files = sorted(f for f in os.listdir(temp_dir) if f.lower().endswith(".png"))
    if not all_png_files:
        pytest.skip("No .png files found in temp directory; nothing to test.")

    derived_uuids = [os.path.splitext(filename)[0] for filename in all_png_files]
    image_indexes = [grouped[uuid]["dynamo"]["image"].id for uuid in derived_uuids]
    uuid_queue = deque(derived_uuids)

    def mock_uuid4():
        if not uuid_queue:
            raise RuntimeError(
                "Ran out of mock UUIDs! Script called uuid.uuid4() more times than expected."
            )
        return uuid_queue.popleft()

    monkeypatch.setattr(upload_images_to_s3, "uuid4", mock_uuid4)

    def mock_get_image_indexes(client, number_images):
        # Return the pre-collected image IDs (1 per PNG).
        return image_indexes

    monkeypatch.setattr(upload_images_to_s3, "get_image_indexes", mock_get_image_indexes)

    # ACT: Upload images -> triggers ingestion + cluster + Dynamo insert
    upload_images_to_s3.upload_files_with_uuid_in_batches(
        directory=Path(temp_dir).resolve(),
        bucket_name=pulumi_outputs["raw_bucket_name"],
        lambda_function=pulumi_outputs["cluster_lambda_function_name"],
        dynamodb_table_name=pulumi_outputs["dynamodb_table_name"],
    )

    # Wait for the cluster lambda to finish in real usage
    time.sleep(10)

    # ASSERT: Compare final state (RAW, CDN, Dynamo) to the backups
    assert_s3_raw(pulumi_outputs["raw_bucket_name"], raw_keys)
    assert_s3_cdn(pulumi_outputs["cdn_bucket_name"], cdn_keys)
    assert_dynamo(pulumi_outputs["dynamodb_table_name"], dynamo_backup_path)
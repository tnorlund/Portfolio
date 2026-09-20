"""sync_ocr_jobs_dev_to_prod must copy the ocr_results/ artifact even when
the OCRJob row already exists in prod.

Since the reconcile copy writes every OCR_JOB row itself (so the line-item
updater's re-OCR cap holds the moment a partition lands), every promoted
job reaches this script with its row present. Skipping such jobs before
the S3 leg would leave prod without any ocr_results/ artifacts.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "receipt_dynamo"))

import sync_ocr_jobs_dev_to_prod as sync  # noqa: E402

from receipt_dynamo.entities.ocr_job import OCRJob  # noqa: E402

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"
JOB_ID = "11111111-1111-4111-8111-111111111111"


def _job():
    return OCRJob(
        image_id=IMAGE_ID,
        job_id=JOB_ID,
        s3_bucket="devraw",
        s3_key=f"raw/{IMAGE_ID}.png",
        created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        status="COMPLETED",
        job_type="REGIONAL_REOCR",
        receipt_id=1,
        reocr_reason="line_items_recon",
    )


def _run(row_exists: bool):
    ddb = MagicMock()
    # both the Image row and (when row_exists) the OCRJob row are present
    ddb.get_item.side_effect = lambda **kw: (
        {"Item": {}} if kw["Key"]["SK"]["S"] == "IMAGE" or row_exists else {}
    )
    boto = MagicMock()
    boto.client.return_value = ddb
    prod_client = MagicMock()
    with patch.object(sync, "boto3", boto), patch.object(
        sync,
        "load_env",
        side_effect=lambda env: {
            "dynamodb_table_name": f"{env}-table",
            "raw_bucket_name": f"{env}raw",
        },
    ), patch.object(
        sync, "DynamoClient", side_effect=[MagicMock(), prod_client]
    ), patch.object(
        sync, "fetch_all_ocr_jobs", side_effect=[[_job()], []]
    ), patch.object(
        sync, "find_ocr_result_key", return_value="ocr_results/x.json"
    ), patch.object(
        sync, "s3_object_exists", return_value=False
    ), patch.object(
        sync, "copy_s3_object", return_value=True
    ) as copy_obj, patch.object(
        sys, "argv", ["sync_ocr_jobs_dev_to_prod.py", "--no-dry-run"]
    ):
        sync.main()
    return copy_obj, prod_client


def test_artifact_is_copied_when_row_already_in_prod():
    copy_obj, prod_client = _run(row_exists=True)
    copy_obj.assert_called_once()
    assert copy_obj.call_args.args[1:] == (
        "devraw",
        "ocr_results/x.json",
        "prodraw",
        "ocr_results/x.json",
    )
    prod_client.add_ocr_job.assert_not_called()


def test_row_and_artifact_are_written_when_row_absent():
    copy_obj, prod_client = _run(row_exists=False)
    copy_obj.assert_called_once()
    prod_client.add_ocr_job.assert_called_once()
    assert prod_client.add_ocr_job.call_args.args[0].s3_bucket == "prodraw"

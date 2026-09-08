"""Unit tests for the LINE_ITEM_REFINE fields on OCRJob (Tier 3).

The refine job carries the receipt's REAL summary (and merchant) to the
Mac worker, so the second decode pass runs against the graded baseline
instead of the worker's own scanned figures. These fields must survive
the DynamoDB round trip exactly — the Swift side has the mirror test in
``LineItemRefineTests.refineJobRoundTripsThroughDynamoItemMapping``.
"""

from datetime import datetime, timezone

import pytest

from receipt_dynamo.constants import OCRJobType
from receipt_dynamo.entities.ocr_job import OCRJob, item_to_ocr_job

IMAGE_ID = "12345678-1234-4123-8123-123456789012"
JOB_ID = "87654321-4321-4321-8321-210987654321"


def _job(**overrides) -> OCRJob:
    kwargs = dict(
        image_id=IMAGE_ID,
        job_id=JOB_ID,
        s3_bucket="raw",
        s3_key="ocr_results/contract.json",
        created_at=datetime.now(timezone.utc),
        job_type=OCRJobType.LINE_ITEM_REFINE.value,
        receipt_id=2,
        refine_summary={
            "subtotal": 32.74,
            "tax": None,
            "grand_total": 34.15,
        },
        refine_merchant_name="Sprouts Farmers Market",
    )
    kwargs.update(overrides)
    return OCRJob(**kwargs)


def test_refine_fields_round_trip_through_the_item_format():
    job = _job()
    restored = item_to_ocr_job(job.to_item())
    assert restored == job
    assert restored.job_type == "LINE_ITEM_REFINE"
    assert restored.refine_summary == {
        "subtotal": 32.74,
        "tax": None,
        "grand_total": 34.15,
    }
    assert restored.refine_merchant_name == "Sprouts Farmers Market"


def test_refine_fields_default_to_none_and_round_trip():
    job = _job(refine_summary=None, refine_merchant_name=None)
    restored = item_to_ocr_job(job.to_item())
    assert restored.refine_summary is None
    assert restored.refine_merchant_name is None


def test_items_without_refine_attributes_still_parse():
    """Rows written before the fields existed must keep parsing."""
    item = _job(refine_summary=None, refine_merchant_name=None).to_item()
    del item["refine_summary"]
    del item["refine_merchant_name"]
    restored = item_to_ocr_job(item)
    assert restored.refine_summary is None
    assert restored.refine_merchant_name is None


def test_refine_summary_requires_exactly_the_three_figures():
    with pytest.raises(ValueError, match="refine_summary must contain"):
        _job(refine_summary={"subtotal": 1.0})
    with pytest.raises(ValueError, match="must be numeric or None"):
        _job(
            refine_summary={
                "subtotal": "3.99",
                "tax": None,
                "grand_total": None,
            }
        )

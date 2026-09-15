"""Write-side contracts of the dev->prod copier, against a mocked client.

Both of these broke a real promotion on 2026-09-11 after 661 prod
partitions had been deleted, so they are pinned here at the boundary
where the copier calls DynamoClient:

* Legacy word labels (OTHER, PHONE, BUSINESS_NAME, ...) must be restored
  with ``allow_non_core_labels=True``. Without it the DAL raises for the
  whole batch, and because that raise leaves ``copy_image_entities``, it
  also aborts every entity queued after labels.
* ReceiptFactOverride rows must never be written. Owner facts are stated
  on the dev table only and the DAL refuses them on prod; attempting the
  write aborts embeddings and routing decisions for that image.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "receipt_dynamo"))

from copy_dynamodb_dev_to_prod import copy_image_entities  # noqa: E402

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"
TS = "2026-09-11T10:19:00+00:00"


def _label(word_id, label):
    return {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 1,
        "word_id": word_id,
        "label": label,
        "reasoning": "restored",
        "timestamp_added": TS,
        "validation_status": "VALID",
        "label_proposed_by": "legacy",
        "label_consolidated_from": None,
    }


def _copy(export_data):
    client = MagicMock()
    stats = copy_image_entities(
        export_data,
        client,
        "devraw",
        "prodraw",
        "devcdn",
        "prodcdn",
        dry_run=False,
    )
    return client, stats


def test_legacy_labels_are_restored_with_the_bypass():
    client, stats = _copy(
        {"receipt_word_labels": [_label(1, "OTHER"), _label(2, "PHONE")]}
    )
    assert stats["errors"] == []
    assert stats["receipt_word_labels"] == 2
    client.add_receipt_word_labels.assert_called()
    for call in client.add_receipt_word_labels.call_args_list:
        assert call.kwargs.get("allow_non_core_labels") is True


def test_fact_overrides_are_never_written_to_prod():
    client, stats = _copy(
        {
            "receipt_fact_overrides": [
                {
                    "image_id": IMAGE_ID,
                    "receipt_id": 1,
                    "revision": 1,
                    "date": "2026-09-11",
                    "date_reference": None,
                    "merchant_name": None,
                    "merchant_name_reference": None,
                    "source": "owner",
                    "changed_at": TS,
                }
            ],
            # entities queued AFTER fact overrides must still be written
            "ocr_routing_decisions": [],
        }
    )
    assert stats["errors"] == []
    client.add_receipt_fact_override.assert_not_called()
    client.add_receipt_fact_overrides.assert_not_called()
    # still reported so the operator can see they were left behind
    assert stats["receipt_fact_overrides"] == 1


def _ocr_job(job_id, bucket="devraw"):
    return {
        "image_id": IMAGE_ID,
        "job_id": job_id,
        "s3_bucket": bucket,
        "s3_key": f"raw/{IMAGE_ID}.png",
        "created_at": TS,
        "updated_at": TS,
        "status": "COMPLETED",
        "job_type": "REGIONAL_REOCR",
        "receipt_id": 1,
        "reocr_region": {"x": 0.1, "y": 0.4, "width": 0.4, "height": 0.2},
        "reocr_reason": "line_items_recon",
        "reocr_strategy": "plain",
        "reocr_mechanism": None,
        "reocr_words_accepted": 3,
        "reocr_words_rejected": 0,
        "reocr_delta_before": 1.5,
        "reocr_delta_after": 1.5,
        "refine_summary": None,
        "refine_merchant_name": None,
    }


def test_ocr_jobs_are_copied_before_anything_that_wakes_the_updater():
    """The line-item updater caps re-OCR by counting REGIONAL_REOCR jobs in
    the destination table. On 2026-09-13 the copy landed partitions without
    that ledger and prod re-OCR'd ~31 receipts within minutes, rewriting
    words dev had already reviewed. The jobs must be written, with the raw
    bucket rewritten, and before summaries/sections reach the stream."""
    client, stats = _copy(
        {
            "ocr_jobs": [
                _ocr_job("11111111-1111-4111-8111-111111111111"),
                _ocr_job("22222222-2222-4222-8222-222222222222", "other"),
            ],
            "receipt_summaries": [],
            "receipt_sections": [],
        }
    )
    assert stats["errors"] == []
    assert stats["ocr_jobs"] == 2
    client.add_ocr_jobs.assert_called_once()
    (jobs,), _ = client.add_ocr_jobs.call_args
    buckets = {j.job_id[:1]: j.s3_bucket for j in jobs}
    assert buckets == {"1": "prodraw", "2": "other"}
    assert all(j.reocr_reason == "line_items_recon" for j in jobs)
    assert jobs[0].reocr_strategy == "plain"
    order = [name for name, _, _ in client.mock_calls]
    ocr_at = order.index("add_ocr_jobs")
    for later in ("add_receipt_summaries", "add_receipt_sections"):
        if later in order:
            assert order.index(later) > ocr_at
    # images (no stream consumer) are the only rows allowed before the ledger
    assert set(order[:ocr_at]) <= {"add_images"}

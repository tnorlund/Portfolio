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

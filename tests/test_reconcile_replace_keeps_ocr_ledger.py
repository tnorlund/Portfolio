"""A REPLACE must not sweep prod's OCR_JOB rows.

They are the line-item updater's re-OCR attempt ledger: the cap
(REOCR_MAX_ATTEMPTS) is counted from REGIONAL_REOCR jobs already in the
destination table. Sweeping them on every REPLACE re-arms the cap, so prod
re-OCRs the same permanently-mismatched receipts after every promotion
and diverges from dev again (observed 2026-09-13, 31 images).
"""

import os
import sys
from unittest.mock import MagicMock, patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "receipt_dynamo"))

import reconcile_dev_to_prod as recon  # noqa: E402

REPLACED = "550e8400-e29b-41d4-a716-446655440000"
DELETED = "660e8400-e29b-41d4-a716-446655440000"


def test_replace_keeps_ocr_jobs_and_delete_sweeps_everything(tmp_path):
    prod = MagicMock()
    prod.delete_image_details.return_value = {"RECEIPT_WORD": 1}
    plan = {"add": [], "replace": [REPLACED], "delete": [DELETED]}
    with patch.object(recon, "export_image") as export, patch.object(
        recon, "copy_all_images"
    ) as copy_all:
        copy_all.return_value = {
            "copied": 1,
            "skipped": 0,
            "skipped_empty": 0,
            "failed": 0,
            "errors": [],
        }
        recon.apply_plan(
            plan, MagicMock(), prod, {"table": "dev"}, {"table": "prod"}
        )
    export.assert_called_once()
    calls = {
        c.args[0]: c.kwargs.get("entity_types")
        for c in prod.delete_image_details.call_args_list
    }
    assert calls[DELETED] is None
    kept_out = recon.RESTORABLE_TYPES - calls[REPLACED]
    assert kept_out == {"OCR_JOB"}
    assert "RECEIPT_WORD" in calls[REPLACED]

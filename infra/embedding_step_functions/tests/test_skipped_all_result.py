"""Zero-embedding batches must still flow to MarkBatchesComplete.

A batch whose results are ALL filtered (deleted receipts) previously
returned an inline dict with status="completed" and result_s3_key=None:
normalize_poll_batches_data dropped it as an invalid S3 reference and
mark_batches_complete rejected it (wrong keys), so the batch re-polled
forever as a PENDING zombie (44 observed in dev, 2026-07-09).
"""

import importlib.util
import json
import os
import pathlib
from unittest.mock import patch

# Load the module directly by path: handlers/__init__ eagerly imports
# sibling handlers that need live AWS env at import time.
_MOD_PATH = (
    pathlib.Path(__file__).parent.parent
    / "unified_embedding"
    / "handlers"
    / "skipped_all.py"
)
_spec = importlib.util.spec_from_file_location("skipped_all", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_skipped_all_s3_result = _mod.build_skipped_all_s3_result


class FakeS3:
    def __init__(self):
        self.uploads = {}

    def upload_file(self, path, bucket, key):
        with open(path, "r", encoding="utf-8") as f:
            self.uploads[(bucket, key)] = json.load(f)


def test_skipped_all_result_is_completable_s3_ref():
    s3 = FakeS3()
    with patch.dict(os.environ, {"S3_BUCKET": "test-bucket"}):
        ref = build_skipped_all_s3_result(
            s3, "batch-1", "openai-1", 3, "missing_receipts"
        )
    assert ref == {
        "batch_id": "batch-1",
        "result_s3_key": "poll_results/batch-1/result.json",
        "result_s3_bucket": "test-bucket",
    }
    body = s3.uploads[("test-bucket", "poll_results/batch-1/result.json")]
    # the two fields mark_batches_complete gates on
    assert body["batch_status"] == "completed"
    assert body["action"] == "process_results"
    # no delta keys: compaction's delta filters must skip this result
    assert "delta_key" not in body and "delta_id" not in body
    assert body["skipped_all"] is True
    assert body["skipped_receipt_count"] == 3
    assert body["skip_reason"] == "missing_receipts"


def test_missing_bucket_env_raises():
    import pytest

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            build_skipped_all_s3_result(FakeS3(), "b", "o", 1, "r")

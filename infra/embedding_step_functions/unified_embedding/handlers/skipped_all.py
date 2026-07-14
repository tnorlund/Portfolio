"""Poll-result envelope for batches whose results were ALL filtered out.

A batch can complete at OpenAI yet have nothing to save — e.g. every
referenced receipt was deleted between submit and poll. Such a batch must
still flow through normalize_poll_batches_data (which drops entries
without a result_s3_key) and mark_batches_complete (which only marks
COMPLETED when batch_status=="completed" and action=="process_results").
Returning an inline dict without those fields left zero-embedding batches
re-polling forever as PENDING zombies (44 observed in dev, 2026-07-09).

Kept import-light so it is unit-testable without the handlers' module
import side effects.
"""

import json
import os
import tempfile


def build_skipped_all_s3_result(
    s3_client,
    batch_id: str,
    openai_batch_id: str,
    skipped_receipt_count: int,
    reason: str,
) -> dict:
    """Upload a completable zero-embedding poll result; return its S3 ref.

    The uploaded body mirrors the normal-path envelope (batch_status,
    action) but carries no delta keys, so compaction's delta filters skip
    it while mark_batches_complete still marks the batch COMPLETED.
    """
    full_result = {
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": "completed",
        "action": "process_results",
        "results_count": 0,
        "embedding_count": 0,
        "skipped_all": True,
        "skipped_receipt_count": skipped_receipt_count,
        "skip_reason": reason,
    }
    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET environment variable not set")
    result_s3_key = f"poll_results/{batch_id}/result.json"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp_file:
        json.dump(full_result, tmp_file)
        tmp_file_path = tmp_file.name
    try:
        s3_client.upload_file(tmp_file_path, bucket, result_s3_key)
    finally:
        try:
            os.unlink(tmp_file_path)
        except OSError:
            pass
    return {
        "batch_id": batch_id,
        "result_s3_key": result_s3_key,
        "result_s3_bucket": bucket,
    }

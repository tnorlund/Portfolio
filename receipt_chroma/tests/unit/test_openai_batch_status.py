"""Unit tests for OpenAI batch status → DynamoDB write shape (#1272)."""

from datetime import datetime, timezone
from types import SimpleNamespace

from receipt_chroma.embedding.openai.batch_status import (
    handle_cancelled_status,
    handle_failed_status,
    handle_in_progress_status,
    map_openai_to_dynamo_status,
)
from receipt_chroma.embedding.openai.poll import (
    ACTIVE_BATCH_STATUSES,
    list_pending_line_embedding_batches,
    list_pending_word_embedding_batches,
)
from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.entities import BatchSummary


def _summary(status: str = BatchStatus.PENDING.value) -> BatchSummary:
    return BatchSummary(
        batch_id="batch-a",
        batch_type=BatchType.WORD_EMBEDDING.value,
        openai_batch_id="openai-a",
        submitted_at=datetime.now(timezone.utc),
        status=status,
        result_file_id="",
        receipt_refs=[("image-a", 1)],
    )


class _CapturingDynamo:
    def __init__(self, summary: BatchSummary):
        self.summary = summary
        self.updated: list[BatchSummary] = []

    def get_batch_summary(self, batch_id: str) -> BatchSummary:
        assert batch_id == "batch-a"
        return self.summary

    def update_batch_summary(self, updated: BatchSummary) -> None:
        self.updated.append(updated)


def test_map_openai_to_dynamo_status_returns_string_values() -> None:
    assert map_openai_to_dynamo_status("in_progress") == "IN_PROGRESS"
    assert map_openai_to_dynamo_status("validating") == "VALIDATING"
    assert isinstance(map_openai_to_dynamo_status("finalizing"), str)


def test_handle_in_progress_persists_string_status_and_gsi() -> None:
    summary = _summary()
    dynamo = _CapturingDynamo(summary)

    result = handle_in_progress_status(
        "batch-a", "openai-a", "in_progress", dynamo
    )

    assert result["action"] == "wait"
    assert summary.status == BatchStatus.IN_PROGRESS.value
    assert dynamo.updated == [summary]
    item = summary.to_item()
    assert item["GSI1PK"] == {"S": "STATUS#IN_PROGRESS"}
    assert item["status"] == {"S": "IN_PROGRESS"}


def test_handle_failed_persists_string_status() -> None:
    summary = _summary()

    class FakeDynamo(_CapturingDynamo):
        def list_receipt_words_from_receipt(self, _image_id, _receipt_id):
            return []

        def update_receipt_words(self, _words):
            raise AssertionError("no pending words to update")

    dynamo = FakeDynamo(summary)

    class _FakeOpenAI:
        class batches:
            @staticmethod
            def retrieve(_batch_id):
                return SimpleNamespace(error_file_id=None, status="failed")

    result = handle_failed_status(
        "batch-a", "openai-a", dynamo, _FakeOpenAI()
    )

    assert result["status"] == "failed"
    assert summary.status == BatchStatus.FAILED.value
    assert summary.to_item()["GSI1PK"] == {"S": "STATUS#FAILED"}


def test_handle_cancelled_persists_string_canceling() -> None:
    summary = _summary(status=BatchStatus.IN_PROGRESS.value)
    dynamo = _CapturingDynamo(summary)

    result = handle_cancelled_status(
        "batch-a", "openai-a", "canceling", dynamo
    )

    assert result["marked_for_retry"] == 0
    assert summary.status == BatchStatus.CANCELING.value
    assert summary.to_item()["GSI1PK"] == {"S": "STATUS#CANCELING"}


def test_list_pending_batches_covers_provider_active_statuses() -> None:
    now = datetime.now(timezone.utc)
    summaries = {
        status: [
            SimpleNamespace(
                batch_id=status.value,
                submitted_at=now,
            )
        ]
        for status in ACTIVE_BATCH_STATUSES
    }

    class FakeDynamo:
        def get_batch_summaries_by_status(
            self, *, status, batch_type, limit, last_evaluated_key
        ):
            del batch_type, limit, last_evaluated_key
            return summaries[status], None

    found_line = list_pending_line_embedding_batches(FakeDynamo())
    found_word = list_pending_word_embedding_batches(FakeDynamo())

    expected = {status.value for status in ACTIVE_BATCH_STATUSES}
    assert {s.batch_id for s in found_line} == expected
    assert {s.batch_id for s in found_word} == expected

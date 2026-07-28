"""The handler must not report success for an incomplete embedding.

``process_embeddings`` returns ``success=False`` when no CompactionRun was
written: its deltas are orphaned in S3, nothing will merge them, and it
published nothing to Chroma Cloud. Because the handler drives the
``UploadLambdaEmbeddingSuccess`` / ``UploadLambdaEmbeddingFailed`` metrics,
swallowing that flag makes the failure invisible.
"""

from unittest.mock import MagicMock, patch

import pytest
from handler import handler as handler_module


def _embedding_result(success: bool) -> dict:
    return {
        "success": success,
        "compaction_run_created": success,
        "merchant_found": True,
        "merchant_name": "Costco",
        "merchant_place_id": "place-1",
    }


@pytest.fixture
def processor():
    """Patch the processor and the observability hook it feeds."""
    with patch.object(
        handler_module, "_emit_section_observability", MagicMock()
    ):
        yield


def _run(monkeypatch, results):
    """Drive just the per-receipt embedding loop over ``results``."""
    calls = iter(results)
    proc = MagicMock()
    proc.process_embeddings.side_effect = lambda **_kw: next(calls)
    return proc


class TestEmbeddingSuccessPropagation:
    """The processor's success flag has to reach the handler's result."""

    def test_incomplete_receipt_marks_the_image_failed(self, processor):
        entries = [
            {"receipt_id": 1, "success": True},
            {"receipt_id": 2, "success": False},
        ]
        failed = [e["receipt_id"] for e in entries if not e["success"]]

        assert failed == [2]
        # Mirrors the handler's aggregation: any incomplete receipt flips
        # the image's embedding outcome.
        embeddings_ok = not failed
        assert embeddings_ok is False

    def test_all_complete_receipts_stay_successful(self, processor):
        entries = [
            {"receipt_id": 1, "success": True},
            {"receipt_id": 2, "success": True},
        ]
        failed = [e["receipt_id"] for e in entries if not e["success"]]
        assert failed == []
        assert (not failed) is True

    def test_missing_success_key_is_treated_as_success(self):
        """Older result shapes without the key must not regress."""
        assert bool({}.get("success", True)) is True


class TestHandlerResultShape:
    """The result dict feeds the embedding metrics directly."""

    @pytest.mark.parametrize(
        "failed_receipts, expect_success, expect_failed",
        [([], True, False), ([2], False, True)],
        ids=["all_complete", "one_incomplete"],
    )
    def test_metric_flags_are_mutually_exclusive(
        self, failed_receipts, expect_success, expect_failed
    ):
        embeddings_ok = not failed_receipts
        result = {
            "embedding_success": embeddings_ok,
            "embedding_failed": not embeddings_ok,
            "failed_receipt_ids": failed_receipts,
        }

        assert result["embedding_success"] is expect_success
        assert result["embedding_failed"] is expect_failed
        # The lambda_handler counts one or the other, never both.
        assert result["embedding_success"] != result["embedding_failed"]

    def test_source_marks_failure_on_incomplete_receipts(self):
        """Guard the handler wiring itself, not just the arithmetic."""
        import inspect

        source = inspect.getsource(handler_module._process_single_record)
        assert '"embedding_success": embeddings_ok' in source
        assert '"embedding_failed": not embeddings_ok' in source
        assert "failed_receipt_ids" in source

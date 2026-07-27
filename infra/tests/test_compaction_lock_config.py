"""Tests for the ChromaDB compaction handler's lock and failure handling.

The handler runs inside a Lambda package where ``utils`` and the monorepo
packages are installed at build time, so this module stubs those imports and
loads ``enhanced_compaction_handler.py`` directly from its path.  That keeps
the test dependency-free (pytest only) while still exercising the real
handler source.

Covers two regressions that caused the 07-12 compaction outage:

- ``LockManager`` was constructed without the duration/heartbeat settings the
  Lambda's environment provides, and ``start_heartbeat()`` was never called,
  so the lock expired mid-merge and ``upload_snapshot_atomic`` discarded the
  merged snapshot at its ownership check.
- Failed delta merges were dropped instead of being reported as batch item
  failures, so their SQS messages were deleted and the receipts kept no
  vectors.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HANDLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "chromadb_compaction"
    / "lambdas"
    / "enhanced_compaction_handler.py"
)


def _stub_module(name: str, **attrs) -> types.ModuleType:
    """Create a stub module whose unknown attributes are MagicMocks."""
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    module.__getattr__ = lambda _name: MagicMock()  # type: ignore[attr-defined]
    return module


def _passthrough_decorator(*_args, **_kwargs):
    """Stand in for the tracing/timeout decorators applied at import."""
    return lambda func: func


@pytest.fixture(name="handler")
def handler_fixture(monkeypatch):
    """Load the Lambda handler with its runtime-only imports stubbed."""
    utils = _stub_module(
        "utils",
        get_operation_logger=lambda *_a, **_k: MagicMock(),
        trace_function=_passthrough_decorator,
        with_compaction_timeout_protection=_passthrough_decorator,
        emf_metrics=MagicMock(),
    )

    class _ReceiptDynamoError(Exception):
        """Real exception class so ``except`` clauses stay valid."""

    stubs = {
        "utils": utils,
        "utils.lambda_types": _stub_module("utils.lambda_types"),
        "utils.logging": _stub_module("utils.logging"),
        "receipt_chroma": _stub_module("receipt_chroma"),
        "receipt_chroma.compaction": _stub_module("receipt_chroma.compaction"),
        "receipt_chroma.s3": _stub_module("receipt_chroma.s3"),
        "receipt_dynamo": _stub_module("receipt_dynamo"),
        "receipt_dynamo.constants": _stub_module("receipt_dynamo.constants"),
        "receipt_dynamo.data": _stub_module("receipt_dynamo.data"),
        "receipt_dynamo.data.dynamo_client": _stub_module(
            "receipt_dynamo.data.dynamo_client"
        ),
        "receipt_dynamo.data.shared_exceptions": _stub_module(
            "receipt_dynamo.data.shared_exceptions",
            ReceiptDynamoError=_ReceiptDynamoError,
        ),
        "receipt_dynamo_stream": _stub_module("receipt_dynamo_stream"),
        "receipt_dynamo_stream.models": _stub_module(
            "receipt_dynamo_stream.models"
        ),
        "receipt_dynamo_stream.stream_types": _stub_module(
            "receipt_dynamo_stream.stream_types"
        ),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "enhanced_compaction_handler_under_test", HANDLER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_collection():
    collection = MagicMock()
    collection.value = "lines"
    return collection


def _make_message(record_id: str):
    message = MagicMock()
    message.context.record_id = record_id
    message.entity_data = {}
    return message


def _make_result(**overrides):
    """Build a stand-in CollectionUpdateResult."""
    result = MagicMock()
    result.has_errors = False
    result.total_metadata_updated = 0
    result.total_labels_updated = 0
    result.total_sections_updated = 0
    result.delta_merge_count = 0
    result.delta_merge_results = []
    result.failed_delta_merges = []
    result.metadata_updates = []
    result.label_updates = []
    result.section_updates = []
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


def _arrange_successful_cycle(handler, monkeypatch, result=None):
    """Patch the handler's collaborators for one successful compaction."""
    monkeypatch.setenv("CHROMADB_BUCKET", "test-bucket")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("HEARTBEAT_INTERVAL_SECONDS", "17")
    monkeypatch.setenv("LOCK_DURATION_MINUTES", "11")
    monkeypatch.setenv("MAX_HEARTBEAT_FAILURES", "4")

    lock_manager = MagicMock()
    lock_manager.acquire.return_value = True
    lock_manager_cls = MagicMock(return_value=lock_manager)
    monkeypatch.setattr(handler, "LockManager", lock_manager_cls)
    monkeypatch.setattr(handler, "DynamoClient", MagicMock())
    monkeypatch.setattr(handler, "ChromaClient", MagicMock())
    monkeypatch.setattr(handler, "CloudConfig", MagicMock())
    monkeypatch.setattr(
        handler,
        "download_snapshot_atomic",
        MagicMock(return_value={"status": "success", "version": "v1"}),
    )
    monkeypatch.setattr(
        handler,
        "upload_snapshot_atomic",
        MagicMock(return_value={"status": "success", "version_id": "v2"}),
    )

    dual_result = MagicMock()
    dual_result.local_result = result if result is not None else _make_result()
    monkeypatch.setattr(
        handler,
        "apply_collection_updates",
        MagicMock(return_value=dual_result),
    )

    return lock_manager_cls, lock_manager


class TestLockConfiguration:
    """The lock must be built from the Lambda environment and kept alive."""

    def test_lock_manager_uses_environment_configuration(
        self, handler, monkeypatch
    ):
        lock_manager_cls, _ = _arrange_successful_cycle(handler, monkeypatch)

        handler.process_collection(
            collection=_make_collection(),
            messages=[_make_message("record-1")],
            op_logger=MagicMock(),
        )

        kwargs = lock_manager_cls.call_args.kwargs
        assert kwargs["heartbeat_interval"] == 17
        assert kwargs["lock_duration_minutes"] == 11
        assert kwargs["max_heartbeat_failures"] == 4

    def test_heartbeat_runs_for_the_whole_cycle(self, handler, monkeypatch):
        _, lock_manager = _arrange_successful_cycle(handler, monkeypatch)

        handler.process_collection(
            collection=_make_collection(),
            messages=[_make_message("record-1")],
            op_logger=MagicMock(),
        )

        lock_manager.start_heartbeat.assert_called_once()
        lock_manager.stop_heartbeat.assert_called_once()
        lock_manager.release.assert_called_once()

    def test_no_heartbeat_when_lock_is_not_acquired(
        self, handler, monkeypatch
    ):
        _, lock_manager = _arrange_successful_cycle(handler, monkeypatch)
        lock_manager.acquire.return_value = False

        result = handler.process_collection(
            collection=_make_collection(),
            messages=[_make_message("record-1")],
            op_logger=MagicMock(),
        )

        lock_manager.start_heartbeat.assert_not_called()
        assert result["failed_message_ids"] == ["record-1"]


class TestDeltaFailurePropagation:
    """Failed delta merges must be retried, not silently deleted."""

    def test_failed_delta_merge_is_reported_as_a_batch_failure(
        self, handler, monkeypatch
    ):
        result = _make_result(
            has_errors=True,
            failed_delta_merges=[
                {
                    "run_id": "run-1",
                    "error": "delta download failed",
                    "record_id": "record-delta",
                }
            ],
        )
        _arrange_successful_cycle(handler, monkeypatch, result=result)

        outcome = handler.process_collection(
            collection=_make_collection(),
            messages=[_make_message("record-delta")],
            op_logger=MagicMock(),
        )

        assert outcome["failed_message_ids"] == ["record-delta"]

    def test_failed_delta_merge_is_not_marked_completed(
        self, handler, monkeypatch
    ):
        failed_run = {
            "run_id": "run-1",
            "image_id": "img-1",
            "receipt_id": 1,
            "merged_count": 0,
            "error": "upsert verification failed",
            "record_id": "record-delta",
        }
        result = _make_result(
            has_errors=True,
            delta_merge_results=[failed_run],
            failed_delta_merges=[failed_run],
        )
        _arrange_successful_cycle(handler, monkeypatch, result=result)
        dynamo_client = MagicMock()
        monkeypatch.setattr(
            handler, "DynamoClient", MagicMock(return_value=dynamo_client)
        )

        handler.process_collection(
            collection=_make_collection(),
            messages=[_make_message("record-delta")],
            op_logger=MagicMock(),
        )

        dynamo_client.mark_compaction_run_completed.assert_not_called()

    def test_successful_delta_merge_is_marked_completed(
        self, handler, monkeypatch
    ):
        merged_run = {
            "run_id": "run-1",
            "image_id": "img-1",
            "receipt_id": 1,
            "merged_count": 12,
            "error": None,
            "record_id": "record-delta",
        }
        result = _make_result(delta_merge_results=[merged_run])
        _arrange_successful_cycle(handler, monkeypatch, result=result)
        dynamo_client = MagicMock()
        monkeypatch.setattr(
            handler, "DynamoClient", MagicMock(return_value=dynamo_client)
        )

        outcome = handler.process_collection(
            collection=_make_collection(),
            messages=[_make_message("record-delta")],
            op_logger=MagicMock(),
        )

        dynamo_client.mark_compaction_run_completed.assert_called_once()
        assert outcome["failed_message_ids"] == []

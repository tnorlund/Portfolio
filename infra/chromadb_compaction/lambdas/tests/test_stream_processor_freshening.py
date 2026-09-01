"""Stream-processor wiring tests for the vector-freshening leg (SPEC §3.4a).

Self-contained: stubs the Lambda-local ``utils`` package before importing
``stream_processor``; needs only ``receipt_dynamo`` + ``receipt_dynamo_stream``
installed.
"""

import os
import sys
import types
from unittest.mock import MagicMock

_LAMBDAS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _LAMBDAS_DIR not in sys.path:
    sys.path.insert(0, _LAMBDAS_DIR)


def _install_utils_stub() -> types.ModuleType:
    stub = types.ModuleType("utils")
    stub.get_operation_logger = lambda name: MagicMock()
    stub.metrics = MagicMock()
    stub.emf_metrics = MagicMock()
    stub.format_response = lambda response, *args, **kwargs: response
    stub.trace_function = lambda **kwargs: (lambda fn: fn)
    stub.with_compaction_timeout_protection = lambda **kwargs: (lambda fn: fn)
    stub.start_compaction_lambda_monitoring = MagicMock()
    stub.stop_compaction_lambda_monitoring = MagicMock()
    sys.modules["utils"] = stub
    return stub


_UTILS = _install_utils_stub()

import stream_processor  # noqa: E402  (needs the utils stub first)
from receipt_dynamo_stream import FresheningStats  # noqa: E402


def _record() -> dict:
    return {
        "eventID": "evt-1",
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": {"PK": {"S": "IMAGE#x"}, "SK": {"S": "UNRELATED"}},
            "NewImage": {},
        },
    }


def test_handler_invokes_freshening_leg_and_reports_metrics(monkeypatch):
    stats = FresheningStats(records_freshened=1, updates_applied=2)
    recorder = MagicMock(return_value=stats)
    monkeypatch.setattr(stream_processor, "apply_vector_freshening", recorder)
    _UTILS.emf_metrics.log_metrics.reset_mock()

    event = {"Records": [_record()]}
    response = stream_processor.lambda_handler(event, MagicMock())

    recorder.assert_called_once()
    assert recorder.call_args.args[0] is event["Records"]
    assert response["statusCode"] == 200

    logged = [
        call.args[0] for call in _UTILS.emf_metrics.log_metrics.call_args_list
    ]
    assert any(
        metrics.get("VectorFresheningUpdates") == 2 for metrics in logged
    )


def test_handler_is_noop_safe_without_table_env(monkeypatch):
    # Opted-out stacks: DYNAMO_TABLE_NAME unset -> the real leg is inert
    # and the handler completes normally.
    monkeypatch.delenv("DYNAMO_TABLE_NAME", raising=False)

    stats = stream_processor.apply_vector_freshening([_record()], None)
    assert stats == FresheningStats()

    response = stream_processor.lambda_handler(
        {"Records": [_record()]}, MagicMock()
    )
    assert response["statusCode"] == 200

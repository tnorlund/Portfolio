"""Stream-processor wiring tests for the vector-freshening leg (SPEC §3.4a).

Self-contained: a fixture stubs the Lambda-local ``utils`` package,
imports ``stream_processor`` against the stub, and restores
``sys.modules`` afterwards so co-collected tests (e.g.
``test_lambda_imports.py``) see the real modules. Needs only
``receipt_dynamo`` + ``receipt_dynamo_stream`` installed.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest
from receipt_dynamo_stream import FresheningStats

_LAMBDAS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _LAMBDAS_DIR not in sys.path:
    sys.path.insert(0, _LAMBDAS_DIR)

_STUBBED_MODULES = ("utils", "stream_processor")


def _build_utils_stub() -> types.ModuleType:
    stub = types.ModuleType("utils")
    stub.get_operation_logger = lambda name: MagicMock()
    stub.metrics = MagicMock()
    stub.emf_metrics = MagicMock()
    stub.format_response = lambda response, *args, **kwargs: response
    stub.trace_function = lambda **kwargs: (lambda fn: fn)
    stub.with_compaction_timeout_protection = lambda **kwargs: (lambda fn: fn)
    stub.start_compaction_lambda_monitoring = MagicMock()
    stub.stop_compaction_lambda_monitoring = MagicMock()
    return stub


@pytest.fixture()
def stream_env():
    """Import stream_processor against a scoped utils stub.

    Restores ``sys.modules`` on teardown so the stub never leaks into
    other collected test modules.
    """
    saved = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    stub = _build_utils_stub()
    sys.modules["utils"] = stub
    sys.modules.pop("stream_processor", None)
    try:
        import stream_processor

        yield stream_processor, stub
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _record() -> dict:
    return {
        "eventID": "evt-1",
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": {"PK": {"S": "IMAGE#x"}, "SK": {"S": "UNRELATED"}},
            "NewImage": {},
        },
    }


def test_handler_invokes_freshening_leg_and_reports_metrics(
    stream_env, monkeypatch
):
    stream_processor, utils_stub = stream_env
    stats = FresheningStats(records_freshened=1, updates_applied=2)
    recorder = MagicMock(return_value=stats)
    monkeypatch.setattr(stream_processor, "apply_vector_freshening", recorder)

    event = {"Records": [_record()]}
    response = stream_processor.lambda_handler(event, MagicMock())

    recorder.assert_called_once()
    assert recorder.call_args.args[0] is event["Records"]
    assert response["statusCode"] == 200

    logged = [
        call.args[0]
        for call in utils_stub.emf_metrics.log_metrics.call_args_list
    ]
    assert any(
        metrics.get("VectorFresheningUpdates") == 2 for metrics in logged
    )


def test_handler_is_noop_safe_without_table_env(stream_env, monkeypatch):
    # Opted-out stacks: DYNAMO_TABLE_NAME unset -> the real leg is inert
    # and the handler completes normally.
    stream_processor, _ = stream_env
    monkeypatch.delenv("DYNAMO_TABLE_NAME", raising=False)

    stats = stream_processor.apply_vector_freshening([_record()], None)
    assert stats == FresheningStats()

    response = stream_processor.lambda_handler(
        {"Records": [_record()]}, MagicMock()
    )
    assert response["statusCode"] == 200

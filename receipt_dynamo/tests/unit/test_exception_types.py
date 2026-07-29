"""Tests for the public receipt_dynamo exception taxonomy."""

import threading
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import receipt_dynamo
from receipt_dynamo.data.base_operations.error_handling import ErrorHandler
from receipt_dynamo.data.base_operations.shared_utils import (
    batch_write_with_retry,
    batch_write_with_retry_dict,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.resilient_dynamo_client import ResilientDynamoClient
from receipt_dynamo.data.shared_exceptions import (
    BatchOperationError,
    CircuitBreakerOpenError,
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBResourceNotFoundError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityValidationError,
    OperationError,
    ReceiptDynamoError,
    RetryExhaustedError,
)
from receipt_dynamo.utils import retry_with_backoff
from receipt_dynamo.utils.circuit_breaker import CircuitBreaker


def _client_error(code: str, message: str = "service failure") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"RequestId": "request-123"},
        },
        "PutItem",
    )


@pytest.mark.parametrize(
    ("code", "expected_type"),
    [
        ("ValidationException", DynamoDBValidationError),
        ("AccessDeniedException", DynamoDBAccessError),
        ("ResourceNotFoundException", DynamoDBResourceNotFoundError),
        ("ProvisionedThroughputExceededException", DynamoDBThroughputError),
        ("InternalServerError", DynamoDBServerError),
        ("SomeNewServiceError", DynamoDBError),
    ],
)
def test_error_handler_maps_and_chains_service_failures(
    code: str, expected_type: type[Exception]
) -> None:
    source = _client_error(code)

    with pytest.raises(expected_type) as raised:
        ErrorHandler().handle_client_error(source, "put_receipt")

    assert raised.value.__cause__ is source


def test_specific_service_errors_keep_compatible_base_categories() -> None:
    assert issubclass(DynamoDBValidationError, EntityValidationError)
    assert issubclass(DynamoDBResourceNotFoundError, OperationError)
    assert issubclass(DynamoDBAccessError, ReceiptDynamoError)


def test_exception_types_are_available_from_package_api() -> None:
    assert receipt_dynamo.DynamoDBAccessError is DynamoDBAccessError
    assert receipt_dynamo.RetryExhaustedError is RetryExhaustedError


@pytest.mark.parametrize(
    "writer",
    [batch_write_with_retry, batch_write_with_retry_dict],
)
def test_batch_retry_exhaustion_includes_unprocessed_items(writer) -> None:
    unprocessed = {"receipts": [{"PutRequest": {"Item": {"PK": "item"}}}]}
    client = Mock()
    client.batch_write_item.return_value = {"UnprocessedItems": unprocessed}

    with pytest.raises(BatchOperationError) as raised:
        if writer is batch_write_with_retry:
            writer(
                client,
                "receipts",
                unprocessed["receipts"],
                max_retries=1,
                initial_backoff=0,
            )
        else:
            writer(
                client,
                unprocessed,
                max_retries=1,
                initial_backoff=0,
            )

    assert raised.value.attempts == 2
    assert raised.value.unprocessed_items == unprocessed
    assert client.batch_write_item.call_count == 2


def test_retry_exhaustion_exposes_operation_attempts_and_cause() -> None:
    source = OSError("temporary outage")

    @retry_with_backoff(
        max_attempts=2, base_delay=0, jitter=False, exceptions=OSError
    )
    def always_fails() -> None:
        raise source

    with pytest.raises(RetryExhaustedError) as raised:
        always_fails()

    assert raised.value.attempts == 2
    assert raised.value.operation.endswith("always_fails")
    assert raised.value.last_exception is source
    assert raised.value.__cause__ is source


def test_open_circuit_reports_when_a_retry_is_allowed(monkeypatch) -> None:
    breaker = CircuitBreaker(failure_threshold=1, timeout_seconds=30)
    monkeypatch.setattr(
        "receipt_dynamo.utils.circuit_breaker.time.time", lambda: 100.0
    )

    with pytest.raises(ValueError):
        breaker.call(lambda: (_ for _ in ()).throw(ValueError("bad")))

    monkeypatch.setattr(
        "receipt_dynamo.utils.circuit_breaker.time.time", lambda: 105.0
    )
    with pytest.raises(CircuitBreakerOpenError) as raised:
        breaker.call(lambda: None)

    assert raised.value.retry_after_seconds == pytest.approx(25.0)
    assert isinstance(raised.value, ReceiptDynamoError)


def test_resilient_client_uses_typed_open_circuit_error() -> None:
    client = object.__new__(ResilientDynamoClient)
    client.max_retry_attempts = 2
    client._check_circuit_breaker = Mock(return_value=False)
    client._circuit_retry_after = Mock(return_value=12.5)

    with pytest.raises(CircuitBreakerOpenError) as raised:
        client._put_metric_with_retry(Mock())

    assert raised.value.retry_after_seconds == 12.5
    assert "metric write was blocked" in str(raised.value)


def test_resilient_client_wraps_retry_exhaustion(monkeypatch) -> None:
    client = object.__new__(ResilientDynamoClient)
    client.max_retry_attempts = 2
    client._check_circuit_breaker = Mock(return_value=True)
    client._record_failure = Mock()
    client._exponential_backoff = Mock(return_value=0)
    source = ValueError("invalid metric")

    def fail_put(_client, _metric) -> None:
        raise source

    monkeypatch.setattr(DynamoClient, "put_ai_usage_metric", fail_put)

    with pytest.raises(RetryExhaustedError) as raised:
        client._put_metric_with_retry(Mock())

    assert raised.value.operation == "put_ai_usage_metric"
    assert raised.value.attempts == 2
    assert raised.value.last_exception is source
    assert raised.value.__cause__ is source
    assert client._record_failure.call_count == 2


def test_resilient_batch_write_reports_open_circuit() -> None:
    client = object.__new__(ResilientDynamoClient)
    client.max_retry_attempts = 2
    client._check_circuit_breaker = Mock(return_value=False)
    client._circuit_retry_after = Mock(return_value=7.5)
    client._record_failure = Mock()

    with pytest.raises(CircuitBreakerOpenError) as raised:
        client._batch_write_metrics_with_retry([Mock(), Mock()])

    assert raised.value.retry_after_seconds == 7.5
    assert "2 metric writes were blocked" in str(raised.value)
    client._record_failure.assert_not_called()


def test_resilient_batch_write_wraps_retry_exhaustion(monkeypatch) -> None:
    client = object.__new__(ResilientDynamoClient)
    client.max_retry_attempts = 2
    client._check_circuit_breaker = Mock(return_value=True)
    client._record_failure = Mock()
    client._exponential_backoff = Mock(return_value=0)
    source = ValueError("invalid metric batch")

    def fail_batch_write(
        _client: DynamoClient, _metrics: list[object]
    ) -> None:
        raise source

    monkeypatch.setattr(
        DynamoClient, "batch_put_ai_usage_metrics", fail_batch_write
    )

    with pytest.raises(RetryExhaustedError) as raised:
        client._batch_write_metrics_with_retry([Mock()])

    assert raised.value.operation == "batch_put_ai_usage_metrics"
    assert raised.value.attempts == 2
    assert raised.value.last_exception is source
    assert raised.value.__cause__ is source
    assert client._record_failure.call_count == 2


def test_resilient_batch_write_preserves_partial_failure_context(
    monkeypatch,
) -> None:
    client = object.__new__(ResilientDynamoClient)
    client.max_retry_attempts = 1
    client._check_circuit_breaker = Mock(return_value=True)
    client._record_failure = Mock()
    failed_metric = Mock()

    monkeypatch.setattr(
        DynamoClient,
        "batch_put_ai_usage_metrics",
        lambda _client, _metrics: [failed_metric],
    )

    with pytest.raises(RetryExhaustedError) as raised:
        client._batch_write_metrics_with_retry([failed_metric])

    cause = raised.value.last_exception
    assert isinstance(cause, BatchOperationError)
    assert cause.attempts == 1
    assert cause.unprocessed_items == {"metrics": [failed_metric]}
    assert raised.value.__cause__ is cause
    client._record_failure.assert_called_once_with()


def test_auto_flush_worker_survives_unexpected_batch_error(
    monkeypatch, capsys
) -> None:
    client = object.__new__(ResilientDynamoClient)
    client.stop_flag = Mock()
    client.stop_flag.is_set.side_effect = [False, False, True]
    client.queue_lock = threading.Lock()
    client.metric_queue = [Mock()]
    client.batch_flush_interval = 0
    client.last_flush_time = 0
    client._prepare_flush = Mock(side_effect=[[Mock()], None])
    client._batch_write_metrics_with_retry = Mock(
        side_effect=TypeError("unexpected dependency failure")
    )
    monkeypatch.setattr(
        "receipt_dynamo.data.resilient_dynamo_client.time.sleep",
        lambda _delay: None,
    )

    client._auto_flush_worker()

    assert client.stop_flag.is_set.call_count == 3
    client._batch_write_metrics_with_retry.assert_called_once()
    assert (
        "Unexpected automatic metric flush failure" in capsys.readouterr().out
    )

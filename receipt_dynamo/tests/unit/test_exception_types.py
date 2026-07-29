"""Tests for the public receipt_dynamo exception taxonomy."""

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

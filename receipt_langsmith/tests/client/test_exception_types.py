"""Focused tests for receipt-langsmith's public failure contract."""

import asyncio
import sys
from json import JSONDecodeError
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

# The package's Parquet reader is an optional extra but is imported eagerly by
# its parser facade. These client tests do not exercise Parquet functionality.
_STUBBED_PYARROW = False
try:
    import pyarrow  # noqa: F401  # type: ignore[import-not-found]
except ImportError:
    _STUBBED_PYARROW = True
    sys.modules["pyarrow"] = ModuleType("pyarrow")
    sys.modules["pyarrow.parquet"] = ModuleType("pyarrow.parquet")

from receipt_langsmith.client.api import LangSmithClient
from receipt_langsmith.client.export import BulkExportManager
from receipt_langsmith.exceptions import (
    BulkExportResponseError,
    BulkExportTimeoutError,
    LangSmithAPIError,
    LangSmithConfigurationError,
    LangSmithResponseError,
    LangSmithTransportError,
)

if _STUBBED_PYARROW:
    # Do not leak the collection-only stub into unrelated Parquet tests.
    sys.modules.pop("pyarrow.parquet", None)
    sys.modules.pop("pyarrow", None)


def test_missing_api_key_raises_configuration_error(monkeypatch) -> None:
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)

    with pytest.raises(LangSmithConfigurationError) as caught:
        LangSmithClient(api_key="")

    assert type(caught.value) is LangSmithConfigurationError
    assert str(caught.value) == (
        "LangSmith API key required. Set LANGCHAIN_API_KEY env var or pass "
        "api_key parameter."
    )


def test_api_error_has_request_context() -> None:
    client = LangSmithClient(api_key="secret")
    response = Mock(status_code=429, text="rate limited")
    response.json.return_value = {"detail": "rate limited"}
    http_client = AsyncMock()
    http_client.request.return_value = response
    client._async_client = http_client  # pylint: disable=protected-access

    with pytest.raises(LangSmithAPIError) as caught:
        asyncio.run(client.arequest("GET", "/api/v1/runs"))

    assert type(caught.value) is LangSmithAPIError
    assert str(caught.value) == (
        "LangSmith API GET /api/v1/runs failed with status 429: rate limited"
    )
    assert caught.value.status_code == 429
    assert caught.value.method == "GET"
    assert caught.value.path == "/api/v1/runs"


def test_transport_error_preserves_httpx_cause() -> None:
    client = LangSmithClient(api_key="secret")
    request = httpx.Request("POST", "https://example.test/api/v1/runs")
    cause = httpx.ConnectError("connection refused", request=request)
    http_client = AsyncMock()
    http_client.request.side_effect = cause
    client._async_client = http_client  # pylint: disable=protected-access

    with pytest.raises(LangSmithTransportError) as caught:
        asyncio.run(
            LangSmithClient.arequest.__wrapped__(
                client, "POST", "/api/v1/runs"
            )
        )

    assert type(caught.value) is LangSmithTransportError
    assert str(caught.value) == (
        "LangSmith API POST /api/v1/runs transport failed"
    )
    assert caught.value.__cause__ is cause


def test_invalid_json_response_preserves_decoder_cause() -> None:
    client = LangSmithClient(api_key="secret")
    cause = JSONDecodeError("bad JSON", "not-json", 0)
    response = Mock(status_code=200)
    response.json.side_effect = cause
    http_client = AsyncMock()
    http_client.request.return_value = response
    client._async_client = http_client  # pylint: disable=protected-access

    with pytest.raises(LangSmithResponseError) as caught:
        asyncio.run(client.arequest("GET", "/api/v1/sessions"))

    assert type(caught.value) is LangSmithResponseError
    assert str(caught.value) == (
        "Invalid response from LangSmith API GET /api/v1/sessions: "
        "response body is not valid JSON"
    )
    assert caught.value.__cause__ is cause


def test_malformed_export_response_preserves_parse_cause() -> None:
    api_client = Mock()
    api_client.arequest = AsyncMock(return_value={"id": "export-1"})
    manager = BulkExportManager(api_client, destination_id="destination-1")

    with pytest.raises(BulkExportResponseError) as caught:
        asyncio.run(manager.atrigger_export())

    assert type(caught.value) is BulkExportResponseError
    assert str(caught.value) == (
        "Invalid bulk export trigger response: "
        "missing or invalid required fields"
    )
    assert isinstance(caught.value.__cause__, KeyError)


def test_export_timeout_has_job_context() -> None:
    manager = BulkExportManager(Mock(), destination_id="destination-1")

    with pytest.raises(BulkExportTimeoutError) as caught:
        asyncio.run(manager.await_completion("export-42", timeout=0))

    assert type(caught.value) is BulkExportTimeoutError
    assert str(caught.value) == "Export export-42 did not complete within 0s"
    assert caught.value.export_id == "export-42"
    assert caught.value.timeout == 0

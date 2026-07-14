"""Focused tests for extracted visualization workflow handlers."""

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def _load_handler(module_name: str) -> ModuleType:
    """Load a handler without importing the eager Pulumi package."""
    path = (
        Path(__file__).parents[1]
        / "routes"
        / "label_validation_viz_cache"
        / "handlers"
        / f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load handler: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_export = _load_handler("check_export")
trigger_export = _load_handler("trigger_export")


class FakeHttp:
    """Return a configured response and record the outbound request."""

    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        self.requests.append((method, url, kwargs))
        return self.response


@pytest.mark.parametrize(
    ("source_status", "expected_status"),
    [
        ("Complete", "completed"),
        ("Completed", "completed"),
        ("Failed", "failed"),
        ("Cancelled", "failed"),
        ("Pending", "pending"),
        ("Running", "pending"),
        ("InProgress", "pending"),
        ("Unexpected", "Unexpected"),
    ],
)
def test_check_export_normalizes_langsmith_status(
    monkeypatch: pytest.MonkeyPatch,
    source_status: str,
    expected_status: str,
) -> None:
    response = SimpleNamespace(
        status=200,
        data=json.dumps({"status": source_status}).encode(),
    )
    http = FakeHttp(response)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "secret")
    monkeypatch.setattr(check_export.urllib3, "PoolManager", lambda: http)

    result = check_export.handler({"export_id": "export-1"}, None)

    assert result == {
        "export_id": "export-1",
        "status": expected_status,
    }
    assert http.requests == [
        (
            "GET",
            "https://api.smith.langchain.com/api/v1/bulk-exports/export-1",
            {"headers": {"x-api-key": "secret"}},
        )
    ]


def test_check_export_requires_an_export_id() -> None:
    with pytest.raises(ValueError, match="export_id is required"):
        check_export.handler({}, None)


def test_trigger_export_reads_destination_from_setup_lambda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = io.BytesIO(
        json.dumps(
            {"statusCode": 200, "destination_id": "destination-1"}
        ).encode()
    )
    lambda_client = SimpleNamespace(
        invoke=lambda **_kwargs: {"Payload": payload}
    )
    monkeypatch.setattr(
        trigger_export.boto3,
        "client",
        lambda service: lambda_client if service == "lambda" else None,
    )

    assert (
        trigger_export._ensure_destination_exists("setup-lambda")
        == "destination-1"
    )

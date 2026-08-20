"""Tests for the receipts API handler's client reuse and limit validation."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


class FakeDynamoClient:
    instances = []

    def __init__(self, table_name):
        self.table_name = table_name
        self.calls = []
        self.instances.append(self)

    def list_receipts(self, **kwargs):
        self.calls.append(kwargs)
        return ([{"receipt_id": 1}], None)


@pytest.fixture
def handler_module(monkeypatch):
    FakeDynamoClient.instances = []
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "dev-table")

    profiler = ModuleType("_lambda_profiler")
    profiler.profile_handler = lambda handler: handler
    dynamo = ModuleType("receipt_dynamo")
    dynamo.DynamoClient = FakeDynamoClient
    monkeypatch.setitem(sys.modules, "_lambda_profiler", profiler)
    monkeypatch.setitem(sys.modules, "receipt_dynamo", dynamo)

    path = Path(__file__).with_name("index.py")
    spec = importlib.util.spec_from_file_location("receipts_handler", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _event(limit):
    return {
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": {"limit": str(limit)},
    }


def test_reuses_client_across_invocations(handler_module) -> None:
    handler_module.handler(_event(1), None)
    handler_module.handler(_event(1), None)

    assert len(FakeDynamoClient.instances) == 1
    assert FakeDynamoClient.instances[0].calls == [
        {"limit": 1, "last_evaluated_key": None},
        {"limit": 1, "last_evaluated_key": None},
    ]


def test_rejects_non_positive_limit(handler_module) -> None:
    assert handler_module.handler(_event(0), None)["statusCode"] == 400
    assert FakeDynamoClient.instances == []

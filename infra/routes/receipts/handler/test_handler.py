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
    clients = {}
    api_dynamo = ModuleType("_api_dynamo")

    def get_api_dynamo_client(table_name):
        if table_name not in clients:
            clients[table_name] = FakeDynamoClient(table_name)
        return clients[table_name]

    api_dynamo.get_api_dynamo_client = get_api_dynamo_client
    monkeypatch.setitem(sys.modules, "_lambda_profiler", profiler)
    monkeypatch.setitem(sys.modules, "_api_dynamo", api_dynamo)

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

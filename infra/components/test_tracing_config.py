"""Hosted tracing is optional and native workflows never call paid export APIs."""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import boto3
import pytest


def load(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config_module = load(Path(__file__).with_name("tracing_config.py"))
definition = load(
    Path(__file__).parents[1]
    / "routes/label_validation_viz_cache/definition.py"
)


class Config:
    def __init__(self, enabled: bool = False, rate: str = "0.1") -> None:
        self.enabled, self.rate = enabled, rate
        self.secrets: list[str] = []

    def get_bool(self, key: str) -> bool:
        return self.enabled

    def get(self, key: str) -> str:
        return self.rate

    def require_secret(self, key: str) -> str:
        self.secrets.append(key)
        return "test-key"


def test_default_needs_no_hosted_credentials() -> None:
    config = Config()
    assert config_module.hosted_tracing_environment(config) == {
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
    }
    assert not config.secrets


def test_opt_in_is_sampled() -> None:
    config = Config(enabled=True, rate="0.05")
    environment = config_module.hosted_tracing_environment(config)
    assert environment["LANGCHAIN_API_KEY"] == "test-key"
    assert environment["LANGSMITH_TRACING_SAMPLING_RATE"] == "0.05"
    assert environment["LANGSMITH_TRACING"] == "true"


def test_bad_sampling_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        config_module.hosted_tracing_environment(
            Config(enabled=True, rate="2")
        )


def test_label_workflow_reads_native_s3_without_export_waits() -> None:
    flow = definition.build_state_machine_definition(
        "query", "emr", "role", "artifacts", "traces", "cache"
    )
    assert set(flow["States"]) == {"QueryDynamoDB", "StartEMRJob"}
    job = flow["States"]["StartEMRJob"]["Parameters"]["JobDriver"][
        "SparkSubmit"
    ]
    assert "'--trace-format', 'native'" in job["EntryPointArguments.$"]
    assert "native-traces/" in job["EntryPointArguments.$"]


def test_label_api_reads_the_published_receipt_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_CACHE_BUCKET", "test-cache")
    objects = {
        "metadata.json": {
            "receipt_keys": ["cache-runs/run/receipts/r.json"],
            "cached_at": "now",
        },
        "cache-runs/run/receipts/r.json": {
            "image_id": "image",
            "receipt_id": 1,
        },
    }
    client = SimpleNamespace(
        get_object=lambda **kw: {
            "Body": io.BytesIO(json.dumps(objects[kw["Key"]]).encode())
        }
    )
    monkeypatch.setattr(boto3, "client", lambda name: client)
    api = load(
        Path(__file__).parents[1]
        / "routes/label_validation_viz_cache/lambdas/index.py"
    )
    response = api.handler(
        {
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {},
        },
        None,
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["total_count"] == 1
    assert body["receipts"][0]["image_id"] == "image"
    assert body["cached_at"] == "now"


@pytest.mark.parametrize("error_field", ["status", "capture_status"])
def test_receipt_lookup_selects_new_native_roots_instead_of_scan_sample(
    monkeypatch: pytest.MonkeyPatch,
    error_field: str,
) -> None:
    query = load(
        Path(__file__).parents[1]
        / "routes/label_validation_viz_cache/handlers/dynamo_query.py"
    )
    rows = [
        {
            "name": "receipt_processing",
            "status": "success",
            "extra": json.dumps(
                {"metadata": {"image_id": "new-image", "receipt_id": 2}}
            ),
        },
        {
            "name": "receipt_processing",
            "status": "success",
            error_field: "error",
            "extra": json.dumps(
                {"metadata": {"image_id": "failed-image", "receipt_id": 3}}
            ),
        },
    ]
    s3 = SimpleNamespace(
        get_paginator=lambda name: SimpleNamespace(
            paginate=lambda **kw: [
                {
                    "Contents": [
                        {"Key": "native-traces/new.ndjson", "LastModified": 2},
                        {
                            "Key": "native-traces/failed.ndjson",
                            "LastModified": 3,
                        },
                    ]
                }
            ]
        ),
        get_object=lambda **kw: {
            "Body": io.BytesIO(
                json.dumps(rows[1 if "failed" in kw["Key"] else 0]).encode()
            )
        },
    )
    assert query.native_receipt_keys(s3, "traces") == [("new-image", 2)]

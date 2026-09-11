"""Exercise native receipt cache publication without analytics dependencies."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from moto import mock_aws

HANDLERS = (
    Path(__file__).parents[1] / "routes/label_validation_viz_cache/handlers"
)
TRACE_ID = "11111111-1111-4111-8111-111111111111"


def load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        name, HANDLERS / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cache = load("receipt_cache")


def span(name: str, start: int = 0, end: int = 2, **kw: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": name,
        "trace_id": TRACE_ID,
        "name": name,
        "status": "success",
        "start_time": f"2026-09-11T00:00:{start:02d}+00:00",
        "end_time": f"2026-09-11T00:00:{end:02d}+00:00",
        "extra": json.dumps(
            {"metadata": {"image_id": "image", "receipt_id": 1}}
        ),
        "outputs": json.dumps({"merchant_name": "Target"}),
        **kw,
    }


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(HANDLERS))
    monkeypatch.setenv("DYNAMODB_TABLE", "test-table")
    monkeypatch.setenv("CACHE_BUCKET", "test-cache")
    monkeypatch.setenv("NATIVE_TRACE_BUCKET", "test-traces")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    handler = load("dynamo_query")
    receipt = SimpleNamespace(cdn_s3_key="receipt.png", width=200, height=400)
    word = SimpleNamespace(
        line_id=11,
        word_id=1,
        text="$8.19",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.1},
    )
    label = SimpleNamespace(
        line_id=11, word_id=1, label="GRAND_TOTAL", validation_status="VALID"
    )
    # The desired label is on the second DynamoDB page.
    client = SimpleNamespace(
        get_receipt=lambda *args: receipt,
        list_receipt_words_from_receipt=lambda *args: [word],
        list_receipt_word_labels_for_receipt=lambda *args, **kw: (
            ([label], None) if kw else ([], {"cursor": "next"})
        ),
    )
    monkeypatch.setattr(handler, "DynamoClient", lambda table: client)
    with mock_aws():
        s3 = boto3.client("s3")
        for bucket in ("test-cache", "test-traces"):
            s3.create_bucket(Bucket=bucket)
        s3.put_object(
            Bucket="test-cache",
            Key="metadata.json",
            Body=b'{"version":"previous"}',
        )
        root = span("receipt_processing")
        validation = span(
            "llm_batch_validation",
            10,
            14,
            outputs=json.dumps(
                {
                    "validations": [
                        {"line_id": 11, "word_id": 1, "decision": "VALID"}
                    ]
                }
            ),
        )
        for suffix, rows in (
            ("root", [root]),
            ("deferred", [validation, span("async_label_validation", 10, 15)]),
        ):
            s3.put_object(
                Bucket="test-traces",
                Key=f"native-traces/date=2026-09-11/{TRACE_ID}-{suffix}.ndjson",
                Body="\n".join(json.dumps(r) for r in rows),
            )
        yield handler, s3


def test_real_s3_cache_joins_deferred_spans_and_paginates_labels(
    setup: Any,
) -> None:
    handler, s3 = setup
    result = handler.handler({}, None)
    assert result["receipt_count"] == 1
    metadata = json.load(
        s3.get_object(Bucket="test-cache", Key="metadata.json")["Body"]
    )
    receipt = json.load(
        s3.get_object(Bucket="test-cache", Key=metadata["receipt_keys"][0])[
            "Body"
        ]
    )
    assert receipt["words"][0]["decision"] == "VALID"
    assert receipt["words"][0]["validation_status"] == "VALID"
    assert receipt["words"][0]["validation_source"] == "llm"
    assert receipt["words"][0]["bbox"]["x"] == 0.1
    assert receipt["llm"]["duration_seconds"] == 4
    assert receipt["step_timings"]["total"]["duration_seconds"] == 15
    assert metadata["aggregate_stats"]["total_valid"] == 1


def test_failed_receipt_write_preserves_published_index(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler, s3 = setup
    original = s3.put_object

    def fail(**kw: Any) -> Any:
        if kw["Key"].startswith("cache-runs/"):
            raise ClientError(
                {"Error": {"Code": "InternalError"}}, "PutObject"
            )
        return original(**kw)

    monkeypatch.setattr(s3, "put_object", fail)
    monkeypatch.setattr(handler.boto3, "client", lambda name: s3)
    with pytest.raises(ClientError):
        handler.handler({}, None)
    assert (
        json.load(
            s3.get_object(Bucket="test-cache", Key="metadata.json")["Body"]
        )["version"]
        == "previous"
    )


def test_empty_input_preserves_published_index(setup: Any) -> None:
    handler, s3 = setup
    for obj in s3.list_objects_v2(Bucket="test-traces")["Contents"]:
        s3.delete_object(Bucket="test-traces", Key=obj["Key"])
    with pytest.raises(ValueError, match="No native receipt"):
        handler.handler({}, None)
    assert (
        json.load(
            s3.get_object(Bucket="test-cache", Key="metadata.json")["Body"]
        )["version"]
        == "previous"
    )


@pytest.mark.parametrize("error_field", ["status", "capture_status"])
def test_reader_recovers_old_ancestor_and_ignores_failed_attempt(
    monkeypatch: pytest.MonkeyPatch, error_field: str
) -> None:
    monkeypatch.setattr(cache, "MAX_TRACE_OBJECTS", 1)
    payloads = {
        f"native-traces/{TRACE_ID}-root.ndjson": [span("receipt_processing")],
        f"native-traces/{TRACE_ID}-child.ndjson": [
            span("async_label_validation", 5, 8),
            span(
                "llm_batch_validation",
                **{
                    error_field: "error",
                    "start_time": None,
                    "end_time": "invalid",
                },
            ),
        ],
    }
    objects = [
        {"Key": key, "LastModified": i, "Size": 10}
        for i, key in enumerate(payloads)
    ]

    def body(**kw: Any) -> dict[str, Any]:
        data = "\n".join(json.dumps(r) for r in payloads[kw["Key"]]).encode()
        return {"Body": StreamingBody(io.BytesIO(data), len(data))}

    s3 = SimpleNamespace(
        get_paginator=lambda name: SimpleNamespace(
            paginate=lambda **kw: [{"Contents": objects}]
        ),
        get_object=body,
    )
    rows = cache.read_traces(s3, "test-traces")
    assert {r["name"] for r in rows} == {
        "receipt_processing",
        "async_label_validation",
    }
    assert len(cache.receipt_roots(rows)) == 1
    monkeypatch.setattr(cache, "MAX_TRACE_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        cache.read_traces(s3, "test-traces")


def test_deployed_modules_import_without_analytics_packages() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules.update({name: None for name in ('pyspark', 'pyarrow', 'receipt_langsmith')}); import receipt_cache; import dynamo_query",
        ],
        cwd=HANDLERS,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", None),
        ("trace_id", 3),
        ("end_time", ""),
        ("status", None),
        ("status", "pending"),
        ("capture_status", "pending"),
        ("start_time", "invalid"),
        ("end_time", "invalid"),
        ("start_time", "2026-09-11T00:00:00"),
    ],
)
def test_malformed_span_preserves_published_index(
    setup: Any, field: str, value: Any
) -> None:
    handler, s3 = setup
    row = span("receipt_processing")
    row[field] = value
    s3.put_object(
        Bucket="test-traces",
        Key=f"native-traces/{TRACE_ID}-malformed.ndjson",
        Body=json.dumps(row),
    )
    with pytest.raises(ValueError, match=field):
        handler.handler({}, None)
    metadata = json.load(
        s3.get_object(Bucket="test-cache", Key="metadata.json")["Body"]
    )
    assert metadata["version"] == "previous"


def test_llm_retry_supersedes_similarity_decision(setup: Any) -> None:
    handler, s3 = setup
    spans = [
        span(
            "label_validation_similarity",
            0,
            2,
            outputs=json.dumps(
                {
                    "validations": [
                        {"line_id": 11, "word_id": 1, "decision": "VALID"}
                    ]
                }
            ),
        ),
        span(
            "llm_batch_validation",
            20,
            22,
            outputs=json.dumps(
                {
                    "validations": [
                        {"line_id": 11, "word_id": 1, "decision": "CORRECTED"}
                    ]
                }
            ),
        ),
    ]
    s3.put_object(
        Bucket="test-traces",
        Key=f"native-traces/{TRACE_ID}-retry.ndjson",
        Body="\n".join(json.dumps(row) for row in spans),
    )
    handler.handler({}, None)
    metadata = json.load(
        s3.get_object(Bucket="test-cache", Key="metadata.json")["Body"]
    )
    receipt = json.load(
        s3.get_object(Bucket="test-cache", Key=metadata["receipt_keys"][0])[
            "Body"
        ]
    )
    assert receipt["words"][0]["decision"] == "INVALID"
    assert receipt["words"][0]["validation_source"] == "llm"
    assert receipt["llm"]["decisions"] == {
        "VALID": 0,
        "INVALID": 1,
        "NEEDS_REVIEW": 0,
    }

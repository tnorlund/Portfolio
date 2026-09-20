"""Offline distribution tests; conversion is faked, S3 runs in moto."""

import io
import json
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws

from receipt_layoutlm import export_worker


@pytest.fixture
def exported(monkeypatch):
    def convert(**kwargs):
        bundle = Path(kwargs["output_dir"])
        (bundle / "LayoutLM.mlpackage").mkdir(parents=True)
        (bundle / "vocab.txt").write_text("vocab")
        (bundle / "config.json").write_text("{}")
        return str(bundle)

    module = types.ModuleType("receipt_layoutlm.export_coreml")
    module.export_coreml = convert
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="training-dev")
        yield s3, {
            "export_id": "export-1",
            "job_id": "job-1",
            "model_s3_uri": "s3://training-dev/runs/checkpoint/",
            "output_s3_prefix": "s3://training-dev/coreml/job-1/",
            "quantize": "float16",
        }


def test_export_worker_publishes_version_and_alias_and_stamps_job(exported):
    s3, message = exported
    result = export_worker.process_export_job(message)
    assert result["status"] == "SUCCEEDED"
    key = "coreml/versions/export-1/layoutlm-coreml-bundle.zip"
    version = s3.get_object(Bucket="training-dev", Key=key)
    data = version["Body"].read()
    assert result["versioned_bundle_s3_uri"] == f"s3://training-dev/{key}"
    assert result["versioned_bundle_etag"] == version["ETag"]
    alias = s3.get_object(
        Bucket="training-dev", Key=export_worker.CANONICAL_BUNDLE_KEY
    )
    assert alias["Body"].read() == data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        identity = json.loads(archive.read("model_identity.json"))
    assert identity["export_id"] == "export-1"
    assert identity["training_job_id"] == "job-1"
    keys = {
        obj["Key"]
        for obj in s3.list_objects_v2(Bucket="training-dev")["Contents"]
    }
    assert "coreml/active.json" not in keys

    job = SimpleNamespace(results={"best_f1": 0.8})
    dynamo = Mock()
    dynamo.get_job.return_value = job
    export_worker.stamp_model_identity_on_job(dynamo, "job-1", result)
    dynamo.update_job.assert_called_once_with(job)
    assert job.results["best_f1"] == 0.8
    assert (
        job.results["coreml_versioned_bundle_s3_uri"]
        == result["versioned_bundle_s3_uri"]
    )
    assert job.results["coreml_versioned_bundle_etag"] == version["ETag"]


def test_export_worker_cannot_overwrite_an_immutable_version(exported):
    s3, message = exported
    key = "coreml/versions/export-1/layoutlm-coreml-bundle.zip"
    s3.put_object(Bucket="training-dev", Key=key, Body=b"original")
    result = export_worker.process_export_job(message)
    assert result["status"] == "FAILED"
    # A *different* artifact under the same immutable key is a real
    # conflict, not a redelivery: it must fail and must not be reused.
    assert "refusing to overwrite or reuse" in result["error_message"]
    assert (
        s3.get_object(Bucket="training-dev", Key=key)["Body"].read()
        == b"original"
    )
    assert "coreml/active.json" not in {
        obj["Key"]
        for obj in s3.list_objects_v2(Bucket="training-dev")["Contents"]
    }


def test_export_worker_redelivery_reuses_identical_immutable_version(
    exported,
):
    """SQS is at-least-once. A redelivered job must not turn a valid,
    already-published version into a FAILED export."""
    s3, message = exported
    key = "coreml/versions/export-1/layoutlm-coreml-bundle.zip"

    first = export_worker.process_export_job(message)
    assert first["status"] == "SUCCEEDED"
    original = s3.get_object(Bucket="training-dev", Key=key)
    original_body = original["Body"].read()

    second = export_worker.process_export_job(dict(message))
    assert second["status"] == "SUCCEEDED"
    assert second["versioned_bundle_etag"] == first["versioned_bundle_etag"]
    assert (
        second["versioned_bundle_s3_uri"] == first["versioned_bundle_s3_uri"]
    )

    after = s3.get_object(Bucket="training-dev", Key=key)
    assert after["Body"].read() == original_body
    assert after["ETag"] == original["ETag"]
    versions = [
        obj["Key"]
        for obj in s3.list_objects_v2(
            Bucket="training-dev", Prefix="coreml/versions/"
        )["Contents"]
    ]
    assert versions == [key]

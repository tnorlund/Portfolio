"""A deployed CoreML bundle must name the training run that produced it.

Reconstructing that link from S3 object timestamps is how a deployment history
gets confidently misread. The bundle carries ``model_identity.json``, and the
Job entity carries the bundle's ETag, so the trace runs both ways.
"""

import json
import os
from types import SimpleNamespace

from receipt_layoutlm.export_worker import (
    stamp_model_identity_on_job,
    write_model_identity,
)


def test_write_model_identity_records_the_source_run(tmp_path):
    bundle = tmp_path / "model-bundle"
    bundle.mkdir()

    path = write_model_identity(
        str(bundle),
        export_id="exp-1",
        job_id="job-uuid-1",
        model_s3_uri="s3://bucket/runs/layoutlm-v31/best/",
        quantize="float16",
    )

    assert os.path.basename(path) == "model_identity.json"
    identity = json.loads((bundle / "model_identity.json").read_text())
    assert identity["export_id"] == "exp-1"
    assert identity["training_job_id"] == "job-uuid-1"
    assert identity["source_checkpoint_s3_uri"] == (
        "s3://bucket/runs/layoutlm-v31/best/"
    )
    assert identity["quantize"] == "float16"
    assert identity["exported_at"].endswith("+00:00")


class _FakeDynamo:
    def __init__(self, job):
        self.job = job
        self.updated = None

    def get_job(self, job_id):
        assert job_id == self.job.job_id
        return self.job

    def update_job(self, job):
        self.updated = job


def test_stamp_merges_bundle_identity_without_dropping_training_results():
    job = SimpleNamespace(
        job_id="job-uuid-1",
        results={"best_f1": 0.74, "metrics_comparable": True},
    )
    dynamo = _FakeDynamo(job)

    stamp_model_identity_on_job(
        dynamo,
        "job-uuid-1",
        {
            "export_id": "exp-1",
            "bundle_s3_uri": "s3://bucket/coreml/layoutlm-v31/",
            "canonical_bundle_s3_uri": "s3://bucket/coreml/bundle.zip",
            "canonical_bundle_etag": "d41d8cd98f00b204e9800998ecf8427e",
            "model_size_bytes": 231000000,
        },
    )

    results = dynamo.updated.results
    # Pre-existing training results survive.
    assert results["best_f1"] == 0.74
    assert results["metrics_comparable"] is True
    # Deployment identity is now attached.
    assert results["coreml_canonical_bundle_etag"] == (
        "d41d8cd98f00b204e9800998ecf8427e"
    )
    assert results["coreml_export_id"] == "exp-1"
    assert results["coreml_model_size_bytes"] == 231000000
    assert results["coreml_exported_at"]


def test_stamp_is_a_noop_without_a_dynamo_client_or_job_id():
    stamp_model_identity_on_job(None, "job-uuid-1", {})
    stamp_model_identity_on_job(_FakeDynamo(SimpleNamespace()), "", {})


def test_stamp_never_fails_an_export_that_already_succeeded():
    class _Broken:
        def get_job(self, job_id):
            raise RuntimeError("dynamo is down")

    stamp_model_identity_on_job(_Broken(), "job-uuid-1", {})


def test_stamp_handles_a_job_with_no_prior_results():
    job = SimpleNamespace(job_id="job-uuid-1", results=None)
    dynamo = _FakeDynamo(job)

    stamp_model_identity_on_job(
        dynamo, "job-uuid-1", {"canonical_bundle_etag": "abc"}
    )

    assert dynamo.updated.results["coreml_canonical_bundle_etag"] == "abc"

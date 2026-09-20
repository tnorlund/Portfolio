"""The results-queue Lambda must carry CoreML identity onto the Job.

Promotion (``set_active_model``) selects a Job by ``coreml_export_id`` and
``coreml_versioned_bundle_s3_uri``. When the export worker runs without a
Dynamo table -- the documented configuration -- this Lambda is the only path
those fields take to the Job. Dropping them leaves a successful export
unpromotable.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "receipt_dynamo"))

_spec = importlib.util.spec_from_file_location(
    "coreml_process_results",
    REPO / "infra" / "coreml_export" / "process_results.py",
)
process_results = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(process_results)


class FakeDynamo:
    def __init__(self):
        self.export_job = SimpleNamespace(
            export_id="export-1",
            job_id="job-1",
            status="PENDING",
            mlpackage_s3_uri=None,
            bundle_s3_uri=None,
            model_size_bytes=None,
            error_message=None,
            export_duration_seconds=None,
            completed_at=None,
        )
        self.job = SimpleNamespace(job_id="job-1", name="run", results={})
        self.updated_jobs = []

    def get_coreml_export_job(self, export_id):
        assert export_id == "export-1"
        return self.export_job

    def update_coreml_export_job(self, export_job):
        self.export_job = export_job

    def get_job(self, job_id):
        assert job_id == "job-1"
        return self.job

    def update_job(self, job):
        self.updated_jobs.append(dict(job.results))


def test_success_result_stamps_full_coreml_identity_on_job():
    dynamo = FakeDynamo()
    message = {
        "export_id": "export-1",
        "job_id": "job-1",
        "status": "SUCCEEDED",
        "mlpackage_s3_uri": "s3://b/coreml/job/LayoutLM.mlpackage/",
        "bundle_s3_uri": "s3://b/coreml/job/",
        "canonical_bundle_s3_uri": "s3://b/coreml/layoutlm-coreml-bundle.zip",
        "canonical_bundle_etag": "etag-canonical",
        "versioned_bundle_s3_uri": (
            "s3://b/coreml/versions/export-1/layoutlm-coreml-bundle.zip"
        ),
        "versioned_bundle_etag": "etag-versioned",
        "model_size_bytes": 414952022,
        "export_duration_seconds": 12.5,
    }

    process_results.process_export_result(dynamo, message)

    assert dynamo.updated_jobs, "Job was not updated"
    results = dynamo.updated_jobs[-1]
    assert results["coreml_export_id"] == "export-1"
    assert results["coreml_versioned_bundle_s3_uri"] == (
        "s3://b/coreml/versions/export-1/layoutlm-coreml-bundle.zip"
    )
    assert results["coreml_versioned_bundle_etag"] == "etag-versioned"
    assert results["coreml_canonical_bundle_etag"] == "etag-canonical"
    assert results["coreml_model_size_bytes"] == 414952022
    # Legacy fields still present for existing consumers.
    assert results["coreml_bundle_s3_uri"] == "s3://b/coreml/job/"


def test_failed_result_does_not_touch_job_identity():
    dynamo = FakeDynamo()
    process_results.process_export_result(
        dynamo,
        {
            "export_id": "export-1",
            "job_id": "job-1",
            "status": "FAILED",
            "error_message": "boom",
        },
    )
    assert dynamo.updated_jobs == []

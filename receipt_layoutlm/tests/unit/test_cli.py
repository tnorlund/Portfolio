from types import SimpleNamespace

from receipt_layoutlm.cli import _resolve_run_s3


def test_resolve_run_s3_uses_explicit_uri_and_derives_job_name():
    run_s3_uri, job_name, bucket, run_prefix = _resolve_run_s3(
        dyn=object(),
        run_s3_uri="s3://layoutlm-training/runs/job-123/",
        job_name=None,
    )

    assert run_s3_uri == "s3://layoutlm-training/runs/job-123/"
    assert job_name == "job-123"
    assert bucket == "layoutlm-training"
    assert run_prefix == "runs/job-123/"


def test_resolve_run_s3_falls_back_to_best_checkpoint_path():
    class FakeDynamo:
        def get_job_by_name(self, job_name, limit):
            assert job_name == "job-123"
            assert limit == 1
            return [
                SimpleNamespace(
                    results={
                        "best_checkpoint_s3_path": (
                            "s3://layoutlm-training/runs/job-123/checkpoint-42/"
                        )
                    },
                    s3_uri_for_prefix=lambda _prefix: None,
                )
            ], None

    run_s3_uri, job_name, bucket, run_prefix = _resolve_run_s3(
        FakeDynamo(),
        run_s3_uri=None,
        job_name="job-123",
    )

    assert run_s3_uri == "s3://layoutlm-training/runs/job-123/"
    assert job_name == "job-123"
    assert bucket == "layoutlm-training"
    assert run_prefix == "runs/job-123/"

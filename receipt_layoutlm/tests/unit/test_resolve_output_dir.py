"""Tests for _resolve_output_dir SageMaker-vs-local checkpoint directory."""

from __future__ import annotations

from unittest import mock

from receipt_layoutlm.trainer import (
    SAGEMAKER_CHECKPOINT_DIR,
    _resolve_output_dir,
)


def test_uses_sagemaker_dir_when_present():
    """Inside a SageMaker BYOC container with CheckpointConfig the
    /opt/ml/checkpoints dir exists and gets auto-synced to S3 — the
    trainer must write checkpoints there for spot-restart resilience."""
    with mock.patch(
        "receipt_layoutlm.trainer.os.path.isdir",
        return_value=True,
    ) as is_dir:
        result = _resolve_output_dir("any-job-name")

    assert result == SAGEMAKER_CHECKPOINT_DIR
    is_dir.assert_called_once_with(SAGEMAKER_CHECKPOINT_DIR)


def test_falls_back_to_tmp_when_sagemaker_dir_absent():
    """On local dev machines /opt/ml/checkpoints doesn't exist, so the
    fallback per-job tmp path is used."""
    with mock.patch(
        "receipt_layoutlm.trainer.os.path.isdir",
        return_value=False,
    ):
        result = _resolve_output_dir("layoutlm-experiment-1")

    assert result == "/tmp/receipt_layoutlm/layoutlm-experiment-1"


def test_fallback_includes_job_name():
    """Local dev runs are isolated per-job-name so concurrent runs
    don't write into each other's directories."""
    with mock.patch(
        "receipt_layoutlm.trainer.os.path.isdir", return_value=False
    ):
        a = _resolve_output_dir("run-a")
        b = _resolve_output_dir("run-b")

    assert a != b
    assert a.endswith("/run-a")
    assert b.endswith("/run-b")


def test_sagemaker_path_constant():
    """SageMaker's CheckpointConfig mounts /opt/ml/checkpoints — this is
    SageMaker contract, not a configuration choice, so it's intentionally
    a constant."""
    assert SAGEMAKER_CHECKPOINT_DIR == "/opt/ml/checkpoints"

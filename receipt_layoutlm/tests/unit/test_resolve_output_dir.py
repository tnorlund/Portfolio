"""Tests for _resolve_output_dir SageMaker-vs-local checkpoint directory."""

from __future__ import annotations

from unittest import mock

import pytest

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
    fallback per-job tmp path is used. The returned path is
    realpath-resolved (so on macOS /tmp may show as /private/tmp)."""
    with mock.patch(
        "receipt_layoutlm.trainer.os.path.isdir",
        return_value=False,
    ):
        result = _resolve_output_dir("layoutlm-experiment-1")

    # endswith because /tmp symlinks differ across platforms.
    assert result.endswith("/receipt_layoutlm/layoutlm-experiment-1"), result


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


@pytest.mark.parametrize(
    "bad",
    [
        "../escape",
        "../../etc/passwd",
        "..",
        "/abs/path",
        "",
    ],
)
def test_fallback_rejects_traversal_job_name(bad):
    """job_name is interpolated into the /tmp fallback path. Names
    containing path separators or ``..`` segments could escape the
    intended root — _resolve_output_dir must reject them."""
    with mock.patch(
        "receipt_layoutlm.trainer.os.path.isdir", return_value=False
    ):
        with pytest.raises(ValueError, match="Invalid job_name"):
            _resolve_output_dir(bad)


def test_fallback_accepts_normal_names():
    """Sanity check: typical job names with hyphens, digits, and dots
    still resolve to a path inside the local root."""
    with mock.patch(
        "receipt_layoutlm.trainer.os.path.isdir", return_value=False
    ):
        for name in (
            "layoutlm-hybrid-8-labels-v6",
            "experiment_42",
            "v4.2.1",
        ):
            result = _resolve_output_dir(name)
            assert result.endswith(f"/{name}"), result

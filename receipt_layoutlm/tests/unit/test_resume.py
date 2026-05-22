"""Tests for receipt_layoutlm.resume.sync_resume_checkpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from receipt_layoutlm.resume import (
    _allowlist_numpy_pickle_globals,
    _parse_s3_uri,
    sync_resume_checkpoint,
)


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("s3://bucket/runs/v4/", ("bucket", "runs/v4/")),
        ("s3://bucket/runs/v4", ("bucket", "runs/v4/")),
        ("s3://bucket/", ("bucket", "")),
        ("s3://bucket", ("bucket", "")),
    ],
)
def test_parse_s3_uri(uri, expected):
    assert _parse_s3_uri(uri) == expected


@pytest.mark.parametrize("bad", ["http://bucket/key", "bucket/key", ""])
def test_parse_s3_uri_rejects_non_s3(bad):
    with pytest.raises(ValueError):
        _parse_s3_uri(bad)


def _build_fake_s3_client(objects):
    """Build a MagicMock S3 client whose paginator returns ``objects``."""
    client = MagicMock(name="s3_client")
    paginator = MagicMock(name="paginator")
    paginator.paginate.return_value = [{"Contents": objects}]
    client.get_paginator.return_value = paginator
    return client


def test_sync_resume_checkpoint_downloads_each_object(tmp_path):
    objects = [
        {"Key": "runs/v4/checkpoint-100/pytorch_model.bin"},
        {"Key": "runs/v4/checkpoint-100/trainer_state.json"},
        {"Key": "runs/v4/dataset_snapshot.pkl"},
        # Directory placeholder — should be skipped.
        {"Key": "runs/v4/checkpoint-100/"},
    ]
    s3 = _build_fake_s3_client(objects)

    dest = sync_resume_checkpoint(
        "s3://bucket/runs/v4/",
        job_name="continuation",
        s3_client=s3,
        local_root=tmp_path,
    )

    assert dest == tmp_path / "continuation"
    # Three real files downloaded; the trailing-slash placeholder is skipped.
    assert s3.download_file.call_count == 3
    s3.download_file.assert_any_call(
        "bucket",
        "runs/v4/checkpoint-100/pytorch_model.bin",
        str(tmp_path / "continuation/checkpoint-100/pytorch_model.bin"),
    )
    s3.download_file.assert_any_call(
        "bucket",
        "runs/v4/dataset_snapshot.pkl",
        str(tmp_path / "continuation/dataset_snapshot.pkl"),
    )


def test_sync_resume_checkpoint_creates_intermediate_dirs(tmp_path):
    objects = [{"Key": "runs/v4/checkpoint-100/sub/deep/file.bin"}]
    s3 = _build_fake_s3_client(objects)

    sync_resume_checkpoint(
        "s3://bucket/runs/v4/",
        job_name="job",
        s3_client=s3,
        local_root=tmp_path,
    )

    expected_local = (
        tmp_path / "job/checkpoint-100/sub/deep/file.bin"
    )
    # The parent dirs were created so download_file can write the file.
    assert expected_local.parent.exists()
    s3.download_file.assert_called_once_with(
        "bucket",
        "runs/v4/checkpoint-100/sub/deep/file.bin",
        str(expected_local),
    )


@pytest.mark.parametrize("bad_job_name", ["../escape", "..", "/abs/path", ""])
def test_sync_resume_checkpoint_rejects_traversal_job_name(
    tmp_path, bad_job_name
):
    """job_name must resolve to a subdirectory of local_root."""
    s3 = _build_fake_s3_client([])
    with pytest.raises(ValueError, match="Invalid job_name"):
        sync_resume_checkpoint(
            "s3://bucket/runs/v4/",
            job_name=bad_job_name,
            s3_client=s3,
            local_root=tmp_path,
        )
    s3.download_file.assert_not_called()


def test_sync_resume_checkpoint_skips_traversal_keys(tmp_path, caplog):
    """S3 keys whose normalized rel path escapes the dest dir are skipped."""
    objects = [
        {"Key": "runs/v4/checkpoint-100/legit.bin"},
        {"Key": "runs/v4/../../../etc/passwd"},
        {"Key": "runs/v4/sub/../../escape.bin"},
    ]
    s3 = _build_fake_s3_client(objects)

    with caplog.at_level("WARNING"):
        sync_resume_checkpoint(
            "s3://bucket/runs/v4/",
            job_name="job",
            s3_client=s3,
            local_root=tmp_path,
        )

    # Only the legitimate key was downloaded; the two traversal attempts
    # were skipped before any download_file call.
    assert s3.download_file.call_count == 1
    s3.download_file.assert_called_once_with(
        "bucket",
        "runs/v4/checkpoint-100/legit.bin",
        str(tmp_path / "job/checkpoint-100/legit.bin"),
    )
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("suspicious key" in m or "escapes" in m for m in warnings)


def test_sync_resume_checkpoint_empty_prefix_is_warning_not_error(
    tmp_path, caplog
):
    s3 = _build_fake_s3_client([])  # No objects at the prefix.

    with caplog.at_level("WARNING"):
        sync_resume_checkpoint(
            "s3://bucket/missing/",
            job_name="job",
            s3_client=s3,
            local_root=tmp_path,
        )

    s3.download_file.assert_not_called()
    assert any(
        "no objects found" in r.message for r in caplog.records
    ), "expected a warning when no objects matched the resume prefix"


def test_allowlist_numpy_pickle_globals_registers_expected_items(monkeypatch):
    """torch.load(weights_only=True) needs these numpy globals to unpickle
    HF Trainer's optimizer.pt files — the helper must register at least
    numpy.ndarray and numpy.dtype, plus _reconstruct from whichever module
    path this version of numpy uses."""
    captured: list = []

    import torch.serialization

    monkeypatch.setattr(
        torch.serialization,
        "add_safe_globals",
        lambda items: captured.append(list(items)),
    )

    _allowlist_numpy_pickle_globals()

    assert len(captured) == 1, "expected one call to add_safe_globals"
    names = {getattr(item, "__qualname__", getattr(item, "__name__", str(item)))
             for item in captured[0]}

    import numpy as np

    # Always-present essentials
    assert np.ndarray in captured[0]
    assert np.dtype in captured[0]
    # _reconstruct path varies by numpy version — verify by name not module.
    assert any(
        "_reconstruct" in n for n in names
    ), f"expected _reconstruct in {names}"


def test_sync_resume_checkpoint_allowlists_when_files_downloaded(tmp_path):
    """The sync function should register numpy globals on the happy path
    (files actually downloaded), so HF Trainer's later torch.load succeeds.
    Skip when there are no files — registering would be wasted work."""
    objects = [{"Key": "runs/v6/checkpoint-1/optimizer.pt"}]
    s3 = _build_fake_s3_client(objects)

    import torch.serialization

    calls = []
    orig = torch.serialization.add_safe_globals
    torch.serialization.add_safe_globals = lambda items: calls.append(items)
    try:
        sync_resume_checkpoint(
            "s3://bucket/runs/v6/",
            job_name="job",
            s3_client=s3,
            local_root=tmp_path,
        )
    finally:
        torch.serialization.add_safe_globals = orig

    assert len(calls) == 1, "allowlist should be invoked once when files exist"


def test_sync_resume_checkpoint_skips_allowlist_when_empty(tmp_path):
    """If the resume prefix is empty (zero downloads), there's nothing to
    unpickle later — skip the allowlist call to keep the no-op cheap."""
    s3 = _build_fake_s3_client([])

    import torch.serialization

    calls = []
    orig = torch.serialization.add_safe_globals
    torch.serialization.add_safe_globals = lambda items: calls.append(items)
    try:
        sync_resume_checkpoint(
            "s3://bucket/empty/",
            job_name="job",
            s3_client=s3,
            local_root=tmp_path,
        )
    finally:
        torch.serialization.add_safe_globals = orig

    assert calls == [], "allowlist must not run when no files downloaded"

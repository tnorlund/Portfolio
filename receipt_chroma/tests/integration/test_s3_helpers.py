"""Integration tests for S3 helper functions using moto."""

import tempfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from receipt_chroma.s3.helpers import (
    download_snapshot_from_s3,
    upload_snapshot_with_hash,
)


@pytest.fixture
def s3_bucket(request):
    """Create a mock S3 bucket using moto."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = request.param
        s3.create_bucket(Bucket=bucket_name)
        yield bucket_name


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.mark.integration
class TestDownloadSnapshotFromS3:
    """Test downloading snapshots from S3."""

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_download_snapshot_success(self, s3_bucket, temp_dir):
        """Test successful snapshot download."""
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            snapshot_key = "snapshots/test-snapshot"

            # Upload test files
            s3.put_object(
                Bucket=s3_bucket,
                Key=f"{snapshot_key}/file1.txt",
                Body=b"content1",
            )
            s3.put_object(
                Bucket=s3_bucket,
                Key=f"{snapshot_key}/subdir/file2.txt",
                Body=b"content2",
            )

            # Download snapshot
            result = download_snapshot_from_s3(
                bucket=s3_bucket,
                snapshot_key=snapshot_key,
                local_snapshot_path=temp_dir,
            )

            assert result["status"] == "downloaded"
            assert result["file_count"] == 2
            assert result["total_size_bytes"] > 0

            # Verify files were downloaded
            local_path = Path(temp_dir)
            assert (local_path / "file1.txt").exists()
            assert (local_path / "subdir" / "file2.txt").exists()

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_download_snapshot_not_found(self, s3_bucket, temp_dir):
        """Test downloading non-existent snapshot."""
        with mock_aws():
            result = download_snapshot_from_s3(
                bucket=s3_bucket,
                snapshot_key="snapshots/non-existent",
                local_snapshot_path=temp_dir,
            )

            assert result["status"] == "failed"
            assert "error" in result

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_download_snapshot_with_verify_integrity(
        self, s3_bucket, temp_dir
    ):
        """Test download with integrity verification."""
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            snapshot_key = "snapshots/test-snapshot"

            # Upload test file
            s3.put_object(
                Bucket=s3_bucket,
                Key=f"{snapshot_key}/file1.txt",
                Body=b"content1",
            )

            # Download with verification
            result = download_snapshot_from_s3(
                bucket=s3_bucket,
                snapshot_key=snapshot_key,
                local_snapshot_path=temp_dir,
                verify_integrity=True,
            )

            assert result["status"] == "downloaded"
            assert result["file_count"] == 1


@pytest.mark.integration
class TestUploadSnapshotWithHash:
    """Test uploading snapshots to S3 with hash."""

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_upload_snapshot_success(self, s3_bucket, temp_dir):
        """Test successful snapshot upload."""
        with mock_aws():
            # Create local snapshot files
            snapshot_path = Path(temp_dir) / "snapshot"
            snapshot_path.mkdir()
            (snapshot_path / "file1.txt").write_text("content1")
            (snapshot_path / "subdir").mkdir()
            (snapshot_path / "subdir" / "file2.txt").write_text("content2")

            # Upload snapshot
            result = upload_snapshot_with_hash(
                local_snapshot_path=str(snapshot_path),
                bucket=s3_bucket,
                snapshot_key="snapshots/test-snapshot",
            )

            assert result["status"] == "uploaded"
            assert result["file_count"] == 2
            assert result["total_size_bytes"] > 0
            assert result["hash"] is not None

            # Verify files were uploaded
            s3 = boto3.client("s3", region_name="us-east-1")
            objects = s3.list_objects_v2(
                Bucket=s3_bucket, Prefix="snapshots/test-snapshot"
            )
            assert objects["KeyCount"] >= 2  # Files + hash file

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_upload_snapshot_without_hash(self, s3_bucket, temp_dir):
        """Test upload without hash calculation."""
        with mock_aws():
            # Create local snapshot files
            snapshot_path = Path(temp_dir) / "snapshot"
            snapshot_path.mkdir()
            (snapshot_path / "file1.txt").write_text("content1")

            # Upload without hash
            result = upload_snapshot_with_hash(
                local_snapshot_path=str(snapshot_path),
                bucket=s3_bucket,
                snapshot_key="snapshots/test-snapshot",
                calculate_hash=False,
            )

            assert result["status"] == "uploaded"
            assert result["hash"] is None

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_upload_snapshot_with_metadata(self, s3_bucket, temp_dir):
        """Test upload with custom metadata."""
        with mock_aws():
            snapshot_path = Path(temp_dir) / "snapshot"
            snapshot_path.mkdir()
            (snapshot_path / "file1.txt").write_text("content1")

            result = upload_snapshot_with_hash(
                local_snapshot_path=str(snapshot_path),
                bucket=s3_bucket,
                snapshot_key="snapshots/test-snapshot",
                metadata={"key": "value"},
            )

            assert result["status"] == "uploaded"

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_upload_snapshot_clear_destination(self, s3_bucket, temp_dir):
        """Test upload with destination clearing."""
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            snapshot_key = "snapshots/test-snapshot"

            # Upload initial file
            s3.put_object(
                Bucket=s3_bucket,
                Key=f"{snapshot_key}/old_file.txt",
                Body=b"old content",
            )

            # Create new snapshot
            snapshot_path = Path(temp_dir) / "snapshot"
            snapshot_path.mkdir()
            (snapshot_path / "new_file.txt").write_text("new content")

            # Upload with clearing
            result = upload_snapshot_with_hash(
                local_snapshot_path=str(snapshot_path),
                bucket=s3_bucket,
                snapshot_key=snapshot_key,
                clear_destination=True,
            )

            assert result["status"] == "uploaded"
            # Verify old file is gone
            objects = s3.list_objects_v2(Bucket=s3_bucket, Prefix=snapshot_key)
            assert "old_file.txt" not in [
                obj["Key"] for obj in objects.get("Contents", [])
            ]

    @pytest.mark.parametrize(
        "s3_bucket", ["test-snapshot-bucket"], indirect=True
    )
    def test_upload_snapshot_path_not_exists(self, s3_bucket):
        """Test upload with non-existent path."""
        with mock_aws():
            result = upload_snapshot_with_hash(
                local_snapshot_path="/non/existent/path",
                bucket=s3_bucket,
                snapshot_key="snapshots/test-snapshot",
            )

            assert result["status"] == "failed"
            assert "error" in result

"""
Unit tests for atomic S3 operations in chroma_s3_helpers.

These tests use real S3 operations with moto to test actual business logic
rather than mocked functionality.
"""

import unittest
import tempfile
import shutil
import os
import json
from unittest.mock import MagicMock
from datetime import datetime, timezone
from botocore.exceptions import ClientError

import boto3
from moto import mock_aws

from receipt_label.utils.chroma_s3_helpers import (
    upload_snapshot_atomic,
    download_snapshot_atomic,
    _cleanup_s3_prefix,
    _cleanup_old_snapshot_versions,
)


class TestAtomicS3Operations(unittest.TestCase):
    """Test atomic S3 operations for ChromaDB snapshots."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.bucket = "test-chromadb-bucket"
        self.collection = "lines"
        self.temp_dir = tempfile.mkdtemp()
        
        # Create mock lock manager
        self.mock_lock_manager = MagicMock()
        self.mock_lock_manager.validate_ownership.return_value = True
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @mock_aws
    def test_upload_snapshot_atomic_success(self):
        """Test successful atomic snapshot upload with real S3 operations."""
        # Create real S3 bucket with moto
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=self.bucket)
        
        # Create real snapshot directory
        test_snapshot = os.path.join(self.temp_dir, "test_snapshot")
        os.makedirs(test_snapshot)
        
        # Create mock ChromaDB files
        with open(os.path.join(test_snapshot, "chroma.sqlite3"), "w") as f:
            f.write("mock chromadb data")
        with open(os.path.join(test_snapshot, "data.bin"), "w") as f:
            f.write("mock vector data")
        
        # Test real atomic upload
        result = upload_snapshot_atomic(
            local_path=test_snapshot,
            bucket=self.bucket,
            collection=self.collection,
            lock_manager=self.mock_lock_manager
        )
        
        # Verify result
        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(result["collection"], self.collection)
        self.assertIn("version_id", result)
        self.assertIn("hash", result)
        
        # Verify lock validation was called
        self.mock_lock_manager.validate_ownership.assert_called()
        
        # Verify real S3 operations
        objects = s3_client.list_objects_v2(Bucket=self.bucket)
        self.assertGreater(len(objects.get("Contents", [])), 0)
        
        # Verify versioned snapshot exists
        object_keys = [obj["Key"] for obj in objects["Contents"]]
        versioned_keys = [key for key in object_keys if "timestamped" in key]
        self.assertGreater(len(versioned_keys), 0)
        
        # Verify pointer file exists
        pointer_key = f"{self.collection}/snapshot/latest-pointer.txt"
        pointer_response = s3_client.get_object(Bucket=self.bucket, Key=pointer_key)
        pointer_content = pointer_response["Body"].read().decode("utf-8")
        
        # Verify pointer contains just the version ID (not the full path)
        self.assertTrue(pointer_content.startswith("20251023_"))  # Today's date
        self.assertFalse("/" in pointer_content)  # Should not contain path separators
        
        # Verify the versioned snapshot actually exists
        version_id = pointer_content.strip()
        versioned_prefix = f"{self.collection}/snapshot/timestamped/{version_id}/"
        versioned_objects = s3_client.list_objects_v2(
            Bucket=self.bucket,
            Prefix=versioned_prefix
        )
        self.assertGreater(len(versioned_objects.get("Contents", [])), 0)
    
    @mock_aws
    def test_upload_snapshot_atomic_lock_validation_fails(self):
        """Test upload failure when lock validation fails with real S3."""
        # Create real S3 bucket with moto
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=self.bucket)
        
        # Mock lock manager to fail validation
        self.mock_lock_manager.validate_ownership.return_value = False
        
        result = upload_snapshot_atomic(
            local_path=self.temp_dir,
            bucket=self.bucket,
            collection=self.collection,
            lock_manager=self.mock_lock_manager
        )
        
        # Verify failure
        self.assertEqual(result["status"], "error")
        self.assertIn("Lock validation failed", result["error"])
        self.assertEqual(result["collection"], self.collection)
        
        # Verify no S3 objects were created
        objects = s3_client.list_objects_v2(Bucket=self.bucket)
        self.assertEqual(len(objects.get("Contents", [])), 0)
    
    
    @mock_aws
    def test_download_snapshot_atomic_success(self):
        """Test successful atomic snapshot download with real S3 operations."""
        # Create real S3 bucket with moto
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=self.bucket)
        
        # Create a versioned snapshot structure
        test_version_id = "20250826_143052"
        versioned_prefix = f"{self.collection}/snapshot/timestamped/{test_version_id}/"
        
        # Upload test snapshot files
        s3_client.put_object(
            Bucket=self.bucket,
            Key=f"{versioned_prefix}chroma.sqlite3",
            Body="mock chromadb data"
        )
        s3_client.put_object(
            Bucket=self.bucket,
            Key=f"{versioned_prefix}data.bin",
            Body="mock vector data"
        )
        
        # Create pointer file pointing to versioned snapshot (just the version ID)
        pointer_key = f"{self.collection}/snapshot/latest-pointer.txt"
        s3_client.put_object(
            Bucket=self.bucket,
            Key=pointer_key,
            Body=test_version_id  # Just the version ID, not the full path
        )
        
        # Test real atomic download
        result = download_snapshot_atomic(
            bucket=self.bucket,
            collection=self.collection,
            local_path=self.temp_dir
        )
        
        # Verify result
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(result["collection"], self.collection)
        self.assertEqual(result["version_id"], test_version_id)
        
        # Verify files were downloaded locally
        downloaded_files = []
        for root, dirs, files in os.walk(self.temp_dir):
            for file in files:
                downloaded_files.append(file)
        
        self.assertIn("chroma.sqlite3", downloaded_files)
        self.assertIn("data.bin", downloaded_files)
        
        # Verify file contents
        with open(os.path.join(self.temp_dir, "chroma.sqlite3"), "r") as f:
            content = f.read()
            self.assertEqual(content, "mock chromadb data")
    
    @mock_aws
    def test_download_snapshot_atomic_fallback_to_latest(self):
        """Test fallback to /latest/ path when pointer doesn't exist with real S3."""
        # Create real S3 bucket with moto
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=self.bucket)
        
        # Create /latest/ snapshot structure (no pointer file)
        latest_prefix = f"{self.collection}/snapshot/latest/"
        
        # Upload test snapshot files to /latest/
        s3_client.put_object(
            Bucket=self.bucket,
            Key=f"{latest_prefix}chroma.sqlite3",
            Body="mock chromadb data latest"
        )
        s3_client.put_object(
            Bucket=self.bucket,
            Key=f"{latest_prefix}data.bin",
            Body="mock vector data latest"
        )
        
        # Test real atomic download (should fallback to /latest/)
        result = download_snapshot_atomic(
            bucket=self.bucket,
            collection=self.collection,
            local_path=self.temp_dir
        )
        
        # Verify result uses fallback
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(result["collection"], self.collection)
        self.assertEqual(result["version_id"], "latest-direct")
        
        # Verify files were downloaded locally
        downloaded_files = []
        for root, dirs, files in os.walk(self.temp_dir):
            for file in files:
                downloaded_files.append(file)
        
        self.assertIn("chroma.sqlite3", downloaded_files)
        self.assertIn("data.bin", downloaded_files)
        
        # Verify file contents from /latest/
        with open(os.path.join(self.temp_dir, "chroma.sqlite3"), "r") as f:
            content = f.read()
            self.assertEqual(content, "mock chromadb data latest")
    
    @mock_aws
    def test_cleanup_s3_prefix(self):
        """Test S3 prefix cleanup helper with real S3 operations."""
        # Create real S3 bucket with moto
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=self.bucket)
        
        # Upload test objects
        test_prefix = "test/prefix/"
        s3_client.put_object(
            Bucket=self.bucket,
            Key=f"{test_prefix}file1.txt",
            Body="content1"
        )
        s3_client.put_object(
            Bucket=self.bucket,
            Key=f"{test_prefix}file2.txt",
            Body="content2"
        )
        s3_client.put_object(
            Bucket=self.bucket,
            Key="other/file.txt",  # Should not be deleted
            Body="other content"
        )
        
        # Verify objects exist before cleanup
        objects_before = s3_client.list_objects_v2(Bucket=self.bucket)
        self.assertEqual(len(objects_before.get("Contents", [])), 3)
        
        # Test cleanup
        _cleanup_s3_prefix(s3_client, self.bucket, test_prefix)
        
        # Verify only prefix objects were deleted
        objects_after = s3_client.list_objects_v2(Bucket=self.bucket)
        remaining_keys = [obj["Key"] for obj in objects_after.get("Contents", [])]
        
        self.assertEqual(len(remaining_keys), 1)
        self.assertEqual(remaining_keys[0], "other/file.txt")
    
    @mock_aws
    def test_cleanup_old_snapshot_versions(self):
        """Test cleanup of old snapshot versions with real S3 operations."""
        # Create real S3 bucket with moto
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.create_bucket(Bucket=self.bucket)
        
        # Create multiple versioned snapshots
        versions = [
            "20250826_120000",
            "20250826_130000", 
            "20250826_140000",
            "20250826_150000"
        ]
        
        for version in versions:
            prefix = f"{self.collection}/snapshot/timestamped/{version}/"
            s3_client.put_object(
                Bucket=self.bucket,
                Key=f"{prefix}chroma.sqlite3",
                Body=f"data for {version}"
            )
        
        # Verify all versions exist
        objects_before = s3_client.list_objects_v2(Bucket=self.bucket)
        self.assertEqual(len(objects_before.get("Contents", [])), 4)
        
        # Test cleanup (keep 2 newest versions)
        _cleanup_old_snapshot_versions(s3_client, self.bucket, self.collection, keep_versions=2)
        
        # Verify only newest 2 versions remain
        objects_after = s3_client.list_objects_v2(Bucket=self.bucket)
        remaining_keys = [obj["Key"] for obj in objects_after.get("Contents", [])]
        
        # Should keep: 20250826_150000, 20250826_140000
        # Should delete: 20250826_130000, 20250826_120000
        self.assertEqual(len(remaining_keys), 2)
        
        expected_remaining = [
            f"{self.collection}/snapshot/timestamped/20250826_150000/chroma.sqlite3",
            f"{self.collection}/snapshot/timestamped/20250826_140000/chroma.sqlite3"
        ]
        
        for expected_key in expected_remaining:
            self.assertIn(expected_key, remaining_keys)


if __name__ == '__main__':
    unittest.main()
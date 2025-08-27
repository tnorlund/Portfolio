"""
Unit tests for atomic S3 operations in chroma_s3_helpers.
"""

import unittest
import tempfile
import shutil
import os
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timezone
from botocore.exceptions import ClientError

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
    
    @patch('receipt_label.utils.chroma_s3_helpers.boto3.client')
    @patch('receipt_label.utils.chroma_s3_helpers.upload_snapshot_with_hash')
    def test_upload_snapshot_atomic_success(self, mock_upload, mock_boto3):
        """Test successful atomic snapshot upload."""
        
        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_boto3.return_value = mock_s3_client
        
        # Mock upload_snapshot_with_hash
        mock_upload.return_value = {"status": "uploaded", "hash": "abc123"}
        
        # Create test directory with files (like ChromaDB snapshot)
        test_snapshot = os.path.join(self.temp_dir, "test_snapshot")
        os.makedirs(test_snapshot)
        
        # Create mock ChromaDB files
        with open(os.path.join(test_snapshot, "chroma.sqlite3"), "w") as f:
            f.write("mock chromadb data")
        with open(os.path.join(test_snapshot, "data.bin"), "w") as f:
            f.write("mock vector data")
        
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
        self.assertEqual(result["hash"], "abc123")
        
        # Verify lock validation was called
        self.mock_lock_manager.validate_ownership.assert_called()
        
        # Verify upload was called with versioned path
        mock_upload.assert_called_once()
        call_args = mock_upload.call_args[1]
        self.assertIn("snapshot_key", call_args)
        self.assertTrue(call_args["snapshot_key"].startswith("lines/snapshot/timestamped/"))
        self.assertTrue(call_args["snapshot_key"].endswith("/"))
        
        # Verify pointer file was created
        expected_pointer_key = "lines/snapshot/latest-pointer.txt"
        mock_s3_client.put_object.assert_called_once()
        call_args = mock_s3_client.put_object.call_args[1]
        self.assertEqual(call_args["Key"], expected_pointer_key)
        # Verify body contains version ID (timestamp format)
        body = call_args["Body"].decode('utf-8')
        self.assertTrue(body.startswith("20250826_"))  # Today's date
    
    @patch('receipt_label.utils.chroma_s3_helpers.boto3.client')
    def test_upload_snapshot_atomic_lock_validation_fails(self, mock_boto3):
        """Test upload failure when lock validation fails."""
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
        
        # Verify S3 client was created but no S3 operations were performed
        mock_boto3.assert_called_once_with('s3')
        mock_s3_client = mock_boto3.return_value
        mock_s3_client.put_object.assert_not_called()
    
    @patch('receipt_label.utils.chroma_s3_helpers.boto3.client')
    @patch('receipt_label.utils.chroma_s3_helpers.upload_snapshot_with_hash')
    @patch('receipt_label.utils.chroma_s3_helpers._cleanup_s3_prefix')
    def test_upload_snapshot_atomic_lock_fails_before_promotion(
        self, mock_cleanup, mock_upload, mock_boto3
    ):
        """Test upload cleanup when lock fails before promotion."""        
        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_boto3.return_value = mock_s3_client
        
        # Mock successful upload but lock failure before promotion
        mock_upload.return_value = {"status": "uploaded", "hash": "abc123"}
        self.mock_lock_manager.validate_ownership.side_effect = [True, False]  # Pass first, fail second
        
        result = upload_snapshot_atomic(
            local_path=self.temp_dir,
            bucket=self.bucket,
            collection=self.collection,
            lock_manager=self.mock_lock_manager
        )
        
        # Verify failure
        self.assertEqual(result["status"], "error")
        self.assertIn("Lock validation failed before atomic promotion", result["error"])
        
        # Verify cleanup was called (exact key doesn't matter, just that cleanup happened)
        mock_cleanup.assert_called_once()
        call_args = mock_cleanup.call_args[0]
        self.assertEqual(call_args[1], self.bucket)  # bucket argument
        self.assertTrue(call_args[2].startswith("lines/snapshot/timestamped/"))  # versioned key
        
        # Verify no pointer file was created
        mock_s3_client.put_object.assert_not_called()
    
    @patch('receipt_label.utils.chroma_s3_helpers.boto3.client')
    @patch('receipt_label.utils.chroma_s3_helpers.download_snapshot_from_s3')
    def test_download_snapshot_atomic_success(self, mock_download, mock_boto3):
        """Test successful atomic snapshot download."""
        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_boto3.return_value = mock_s3_client
        
        # Mock pointer file content
        test_version_id = "20250826_143052"
        mock_response = {'Body': MagicMock()}
        mock_response['Body'].read.return_value = test_version_id.encode('utf-8')
        mock_s3_client.get_object.return_value = mock_response
        
        # Mock successful download
        mock_download.return_value = {"status": "downloaded"}
        
        result = download_snapshot_atomic(
            bucket=self.bucket,
            collection=self.collection,
            local_path=self.temp_dir
        )
        
        # Verify result
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(result["collection"], self.collection)
        self.assertEqual(result["version_id"], test_version_id)
        
        # Verify pointer was read
        expected_pointer_key = "lines/snapshot/latest-pointer.txt"
        mock_s3_client.get_object.assert_called_once_with(
            Bucket=self.bucket, Key=expected_pointer_key
        )
        
        # Verify download was called with versioned path
        expected_versioned_key = f"lines/snapshot/timestamped/{test_version_id}/"
        mock_download.assert_called_once()
        call_args = mock_download.call_args[1]
        self.assertEqual(call_args["snapshot_key"], expected_versioned_key)
    
    @patch('receipt_label.utils.chroma_s3_helpers.boto3.client')
    @patch('receipt_label.utils.chroma_s3_helpers.download_snapshot_from_s3')
    def test_download_snapshot_atomic_fallback_to_latest(self, mock_download, mock_boto3):
        """Test fallback to /latest/ path when pointer doesn't exist."""
        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_boto3.return_value = mock_s3_client
        
        # Mock NoSuchKey error for pointer file
        error_response = {'Error': {'Code': 'NoSuchKey'}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, 'GetObject')
        
        # Mock successful download
        mock_download.return_value = {"status": "downloaded"}
        
        result = download_snapshot_atomic(
            bucket=self.bucket,
            collection=self.collection,
            local_path=self.temp_dir
        )
        
        # Verify result uses fallback
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(result["collection"], self.collection)
        self.assertEqual(result["version_id"], "latest-direct")
        
        # Verify download was called with /latest/ path
        expected_latest_key = "lines/snapshot/latest/"
        mock_download.assert_called_once()
        call_args = mock_download.call_args[1]
        self.assertEqual(call_args["snapshot_key"], expected_latest_key)
    
    def test_cleanup_s3_prefix(self):
        """Test S3 prefix cleanup helper."""
        # Mock S3 client
        mock_s3_client = MagicMock()
        
        # Mock paginator with test objects
        mock_paginator = MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'test/prefix/file1.txt'},
                    {'Key': 'test/prefix/file2.txt'},
                ]
            }
        ]
        
        _cleanup_s3_prefix(mock_s3_client, self.bucket, "test/prefix/")
        
        # Verify delete_objects was called
        expected_delete_keys = [
            {'Key': 'test/prefix/file1.txt'},
            {'Key': 'test/prefix/file2.txt'},
        ]
        mock_s3_client.delete_objects.assert_called_once_with(
            Bucket=self.bucket,
            Delete={'Objects': expected_delete_keys}
        )
    
    @patch('receipt_label.utils.chroma_s3_helpers._cleanup_s3_prefix')
    def test_cleanup_old_snapshot_versions(self, mock_cleanup):
        """Test cleanup of old snapshot versions."""
        # Mock S3 client
        mock_s3_client = MagicMock()
        
        # Mock paginator with test versions
        mock_paginator = MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'CommonPrefixes': [
                    {'Prefix': 'lines/snapshot/timestamped/20250826_120000/'},
                    {'Prefix': 'lines/snapshot/timestamped/20250826_130000/'},
                    {'Prefix': 'lines/snapshot/timestamped/20250826_140000/'},
                    {'Prefix': 'lines/snapshot/timestamped/20250826_150000/'},
                ]
            }
        ]
        
        _cleanup_old_snapshot_versions(mock_s3_client, self.bucket, self.collection, keep_versions=2)
        
        # Verify cleanup was called for the 2 oldest versions
        # (Keep newest 2: 20250826_150000, 20250826_140000)
        # (Delete oldest 2: 20250826_130000, 20250826_120000)
        expected_calls = [
            unittest.mock.call(mock_s3_client, self.bucket, 'lines/snapshot/timestamped/20250826_130000/'),
            unittest.mock.call(mock_s3_client, self.bucket, 'lines/snapshot/timestamped/20250826_120000/'),
        ]
        mock_cleanup.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(mock_cleanup.call_count, 2)


if __name__ == '__main__':
    unittest.main()
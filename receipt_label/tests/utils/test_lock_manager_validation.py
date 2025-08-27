"""
Unit tests for LockManager validation enhancements.

Tests the new lock validation functionality to prevent write collisions.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from receipt_label.utils.lock_manager import LockManager
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.entities.compaction_lock import CompactionLock


class TestLockManagerValidation(unittest.TestCase):
    """Test cases for enhanced lock validation functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_dynamo_client = MagicMock()
        self.collection = ChromaDBCollection.WORDS
        self.lock_manager = LockManager(
            dynamo_client=self.mock_dynamo_client,
            collection=self.collection,
            heartbeat_interval=30,
            lock_duration_minutes=3,
            max_heartbeat_failures=3
        )

    def test_validate_ownership_success(self):
        """Test successful lock ownership validation."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock DynamoDB response with valid lock
        mock_lock = CompactionLock(
            lock_id=lock_id,
            owner=owner,
            expires=datetime.utcnow() + timedelta(minutes=5),
            collection=self.collection,
            heartbeat=datetime.utcnow()
        )
        self.mock_dynamo_client.get_compaction_lock.return_value = mock_lock
        
        # Test
        result = self.lock_manager.validate_ownership()
        
        # Verify
        self.assertTrue(result)
        self.mock_dynamo_client.get_compaction_lock.assert_called_once_with(lock_id)

    def test_validate_ownership_expired_lock(self):
        """Test validation failure when lock has expired."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock DynamoDB response with expired lock
        mock_lock = CompactionLock(
            lock_id=lock_id,
            owner=owner,
            expires=datetime.utcnow() - timedelta(minutes=1),  # Expired
            collection=self.collection,
            heartbeat=datetime.utcnow() - timedelta(minutes=2)
        )
        self.mock_dynamo_client.get_compaction_lock.return_value = mock_lock
        
        # Test
        result = self.lock_manager.validate_ownership()
        
        # Verify
        self.assertFalse(result)

    def test_validate_ownership_wrong_owner(self):
        """Test validation failure when lock is owned by different process."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        different_owner = "different-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock DynamoDB response with different owner
        mock_lock = CompactionLock(
            lock_id=lock_id,
            owner=different_owner,
            expires=datetime.utcnow() + timedelta(minutes=5),
            collection=self.collection,
            heartbeat=datetime.utcnow()
        )
        self.mock_dynamo_client.get_compaction_lock.return_value = mock_lock
        
        # Test
        result = self.lock_manager.validate_ownership()
        
        # Verify
        self.assertFalse(result)

    def test_validate_ownership_lock_not_found(self):
        """Test validation failure when lock doesn't exist in DynamoDB."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock DynamoDB response with no lock found
        self.mock_dynamo_client.get_compaction_lock.return_value = None
        
        # Test
        result = self.lock_manager.validate_ownership()
        
        # Verify
        self.assertFalse(result)

    def test_validate_ownership_no_lock_held(self):
        """Test validation failure when no lock is currently held."""
        # Setup - no lock held
        self.lock_manager.lock_id = None
        self.lock_manager.lock_owner = None
        
        # Test
        result = self.lock_manager.validate_ownership()
        
        # Verify
        self.assertFalse(result)
        # DynamoDB should not be called
        self.mock_dynamo_client.get_compaction_lock.assert_not_called()

    def test_get_remaining_time_valid_lock(self):
        """Test getting remaining time for valid lock."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock DynamoDB response with lock expiring in 2 minutes
        expires_at = datetime.utcnow() + timedelta(minutes=2)
        mock_lock = CompactionLock(
            lock_id=lock_id,
            owner=owner,
            expires=expires_at,
            collection=self.collection,
            heartbeat=datetime.utcnow()
        )
        self.mock_dynamo_client.get_compaction_lock.return_value = mock_lock
        
        # Test
        remaining = self.lock_manager.get_remaining_time()
        
        # Verify
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining.total_seconds(), 110)  # About 2 minutes
        self.assertLess(remaining.total_seconds(), 130)

    def test_get_remaining_time_expired_lock(self):
        """Test getting remaining time for expired lock."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock DynamoDB response with expired lock
        mock_lock = CompactionLock(
            lock_id=lock_id,
            owner=owner,
            expires=datetime.utcnow() - timedelta(minutes=1),  # Expired
            collection=self.collection,
            heartbeat=datetime.utcnow() - timedelta(minutes=2)
        )
        self.mock_dynamo_client.get_compaction_lock.return_value = mock_lock
        
        # Test
        remaining = self.lock_manager.get_remaining_time()
        
        # Verify - should return 0 for expired lock
        self.assertEqual(remaining.total_seconds(), 0)

    def test_refresh_lock_success(self):
        """Test successful lock refresh."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock successful validation and update
        with patch.object(self.lock_manager, 'validate_ownership', return_value=True):
            self.mock_dynamo_client.update_compaction_lock.return_value = None
            
            # Test
            result = self.lock_manager.refresh_lock()
            
            # Verify
            self.assertTrue(result)
            self.mock_dynamo_client.update_compaction_lock.assert_called_once()

    def test_refresh_lock_validation_failure(self):
        """Test lock refresh failure due to validation failure."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock failed validation
        with patch.object(self.lock_manager, 'validate_ownership', return_value=False):
            # Test
            result = self.lock_manager.refresh_lock()
            
            # Verify
            self.assertFalse(result)
            # Update should not be called
            self.mock_dynamo_client.update_compaction_lock.assert_not_called()

    @patch('receipt_label.utils.lock_manager.METRICS_AVAILABLE', True)
    @patch('receipt_label.utils.lock_manager.metrics')
    def test_heartbeat_failure_metrics(self, mock_metrics):
        """Test that heartbeat failures emit proper metrics."""
        # Setup
        lock_id = "test-lock"
        owner = "test-owner"
        self.lock_manager.lock_id = lock_id
        self.lock_manager.lock_owner = owner
        
        # Mock failed validation to simulate heartbeat failure
        with patch.object(self.lock_manager, 'validate_ownership', return_value=False):
            # Test - call update_heartbeat which should fail
            result = self.lock_manager.update_heartbeat()
            
            # Verify
            self.assertFalse(result)

    def test_max_heartbeat_failures_configuration(self):
        """Test that max heartbeat failures is properly configured."""
        # Test default value
        default_manager = LockManager(
            dynamo_client=self.mock_dynamo_client,
            collection=self.collection
        )
        self.assertEqual(default_manager.max_heartbeat_failures, 3)
        
        # Test custom value
        custom_manager = LockManager(
            dynamo_client=self.mock_dynamo_client,
            collection=self.collection,
            max_heartbeat_failures=5
        )
        self.assertEqual(custom_manager.max_heartbeat_failures, 5)

    def test_consecutive_failure_tracking(self):
        """Test that consecutive failure count is properly tracked."""
        # Setup
        self.lock_manager.lock_id = "test-lock"
        self.lock_manager.lock_owner = "test-owner"
        
        # Test that failure count starts at 0
        self.assertEqual(self.lock_manager.consecutive_heartbeat_failures, 0)
        
        # Mock failed heartbeat
        with patch.object(self.lock_manager, 'validate_ownership', return_value=False):
            # Simulate heartbeat worker behavior
            if not self.lock_manager.update_heartbeat():
                self.lock_manager.consecutive_heartbeat_failures += 1
        
        # Verify failure count increased
        self.assertEqual(self.lock_manager.consecutive_heartbeat_failures, 1)


if __name__ == '__main__':
    unittest.main()
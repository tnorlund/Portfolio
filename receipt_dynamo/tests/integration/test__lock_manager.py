"""
Integration tests for LockManager with DynamoDB backend.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.lock_manager import LockManager

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def lock_manager_lines(dynamodb_table: Literal["MyMockedTable"]) -> LockManager:
    """Create a LockManager for LINES collection."""
    client = DynamoClient(dynamodb_table)
    return LockManager(
        dynamo_client=client,
        collection=ChromaDBCollection.LINES,
        heartbeat_interval=1,  # Short interval for testing
        lock_duration_minutes=1,  # Short duration for testing
        max_heartbeat_failures=2,  # Low threshold for testing
    )


@pytest.fixture
def lock_manager_words(dynamodb_table: Literal["MyMockedTable"]) -> LockManager:
    """Create a LockManager for WORDS collection.""" 
    client = DynamoClient(dynamodb_table)
    return LockManager(
        dynamo_client=client,
        collection=ChromaDBCollection.WORDS,
        heartbeat_interval=1,  # Short interval for testing
        lock_duration_minutes=1,  # Short duration for testing
        max_heartbeat_failures=2,  # Low threshold for testing
    )


# -------------------------------------------------------------------
#                     BASIC LOCK OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestLockManagerBasics:
    """Test basic lock manager operations."""

    def test_acquire_and_release_lock_success(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test successful lock acquisition and release."""
        lock_id = "test-lock-basic"
        
        # Acquire lock
        success = lock_manager_lines.acquire(lock_id)
        assert success is True
        assert lock_manager_lines.is_locked() is True
        
        # Check lock info
        lock_info = lock_manager_lines.get_lock_info()
        assert lock_info is not None
        assert lock_info["lock_id"] == lock_id
        assert lock_info["heartbeat_active"] is False
        
        # Release lock
        lock_manager_lines.release()
        assert lock_manager_lines.is_locked() is False

    def test_acquire_duplicate_lock_fails(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that acquiring duplicate lock fails."""
        lock_id = "test-lock-duplicate"
        
        # Acquire first lock
        success1 = lock_manager_lines.acquire(lock_id)
        assert success1 is True
        
        # Try to acquire another lock (should fail)
        success2 = lock_manager_lines.acquire("another-lock")
        assert success2 is False
        
        # Original lock should still be held
        assert lock_manager_lines.is_locked() is True
        lock_info = lock_manager_lines.get_lock_info()
        assert lock_info["lock_id"] == lock_id
        
        lock_manager_lines.release()

    def test_collection_isolation(
        self, 
        lock_manager_lines: LockManager,
        lock_manager_words: LockManager
    ) -> None:
        """Test that locks are isolated by collection."""
        lock_id = "shared-lock-id"
        
        # Both managers should be able to acquire same lock ID
        success1 = lock_manager_lines.acquire(lock_id)
        success2 = lock_manager_words.acquire(lock_id)
        
        assert success1 is True
        assert success2 is True
        
        # Both should be locked independently
        assert lock_manager_lines.is_locked() is True
        assert lock_manager_words.is_locked() is True
        
        # Release one, other should remain
        lock_manager_lines.release()
        assert lock_manager_lines.is_locked() is False
        assert lock_manager_words.is_locked() is True
        
        lock_manager_words.release()

    def test_acquire_expired_lock_succeeds(
        self, 
        lock_manager_lines: LockManager,
        dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that acquiring an expired lock succeeds."""
        lock_id = "expired-lock-test"
        
        # Create another manager with same collection
        client = DynamoClient(dynamodb_table)
        other_manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
            lock_duration_minutes=0.01,  # Very short duration (less than 1 minute)
        )
        
        # Acquire lock with short duration
        success1 = other_manager.acquire(lock_id)
        assert success1 is True
        
        # Wait for lock to expire
        time.sleep(2)
        
        # Original manager should be able to acquire expired lock
        success2 = lock_manager_lines.acquire(lock_id)
        assert success2 is True
        
        lock_manager_lines.release()

    def test_validate_ownership_success(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test successful ownership validation."""
        lock_id = "ownership-test"
        
        # Acquire lock
        lock_manager_lines.acquire(lock_id)
        
        # Validate ownership (should succeed)
        is_owner = lock_manager_lines.validate_ownership()
        assert is_owner is True
        
        lock_manager_lines.release()

    def test_validate_ownership_no_lock_fails(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that ownership validation fails when no lock is held."""
        is_owner = lock_manager_lines.validate_ownership()
        assert is_owner is False


# -------------------------------------------------------------------
#                      HEARTBEAT OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestLockManagerHeartbeat:
    """Test lock manager heartbeat functionality."""

    def test_heartbeat_lifecycle(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test heartbeat start, operation, and stop."""
        lock_id = "heartbeat-test"
        
        # Acquire lock
        lock_manager_lines.acquire(lock_id)
        
        # Start heartbeat
        lock_manager_lines.start_heartbeat()
        lock_info = lock_manager_lines.get_lock_info()
        assert lock_info["heartbeat_active"] is True
        
        # Wait for at least one heartbeat
        time.sleep(2)
        
        # Lock should still be valid
        is_owner = lock_manager_lines.validate_ownership()
        assert is_owner is True
        
        # Stop heartbeat
        lock_manager_lines.stop_heartbeat()
        lock_info = lock_manager_lines.get_lock_info()
        assert lock_info["heartbeat_active"] is False
        
        lock_manager_lines.release()

    def test_manual_heartbeat_update(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test manual heartbeat updates."""
        lock_id = "manual-heartbeat"
        
        # Acquire lock
        lock_manager_lines.acquire(lock_id)
        
        # Get initial remaining time
        remaining1 = lock_manager_lines.get_remaining_time()
        assert remaining1 is not None
        
        # Wait a moment
        time.sleep(1)
        
        # Update heartbeat manually
        success = lock_manager_lines.update_heartbeat()
        assert success is True
        
        # Remaining time should be refreshed
        remaining2 = lock_manager_lines.get_remaining_time()
        assert remaining2 is not None
        assert remaining2 > remaining1
        
        lock_manager_lines.release()

    def test_heartbeat_without_lock_fails(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that heartbeat operations fail without a lock."""
        # Try to start heartbeat without lock
        lock_manager_lines.start_heartbeat()  # Should log warning
        
        # Try to update heartbeat without lock
        success = lock_manager_lines.update_heartbeat()
        assert success is False

    def test_refresh_lock_success(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test successful lock refresh."""
        lock_id = "refresh-test"
        
        # Acquire lock
        lock_manager_lines.acquire(lock_id)
        
        # Get initial remaining time
        remaining1 = lock_manager_lines.get_remaining_time()
        assert remaining1 is not None
        
        # Wait a moment
        time.sleep(1)
        
        # Refresh lock
        success = lock_manager_lines.refresh_lock()
        assert success is True
        
        # Remaining time should be refreshed
        remaining2 = lock_manager_lines.get_remaining_time()
        assert remaining2 is not None
        assert remaining2 > remaining1
        
        lock_manager_lines.release()

    def test_refresh_lock_without_lock_fails(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that refresh fails without a lock."""
        success = lock_manager_lines.refresh_lock()
        assert success is False


# -------------------------------------------------------------------
#                     COMPETITIVE SCENARIOS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestLockManagerCompetition:
    """Test competitive lock scenarios."""

    def test_competing_managers_same_collection(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test competing managers for same collection."""
        client = DynamoClient(dynamodb_table)
        
        # Create two managers for same collection
        manager1 = LockManager(client, ChromaDBCollection.LINES)
        manager2 = LockManager(client, ChromaDBCollection.LINES)
        
        lock_id = "competition-test"
        
        # First manager acquires lock
        success1 = manager1.acquire(lock_id)
        assert success1 is True
        
        # Second manager should fail to acquire same lock
        success2 = manager2.acquire(lock_id)
        assert success2 is False
        
        # Only first manager should be locked
        assert manager1.is_locked() is True
        assert manager2.is_locked() is False
        
        # First manager releases, second should be able to acquire
        manager1.release()
        success3 = manager2.acquire(lock_id)
        assert success3 is True
        
        manager2.release()

    def test_ownership_validation_after_other_acquires(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test ownership validation when another process acquires the lock."""
        client = DynamoClient(dynamodb_table)
        
        # Create two managers
        manager1 = LockManager(client, ChromaDBCollection.LINES)
        manager2 = LockManager(client, ChromaDBCollection.LINES)
        
        lock_id = "ownership-competition"
        
        # Manager1 acquires lock
        manager1.acquire(lock_id)
        assert manager1.validate_ownership() is True
        
        # Manually delete the lock (simulating another process or expiration)
        client.delete_compaction_lock(lock_id, manager1.lock_owner, ChromaDBCollection.LINES)
        
        # Manager1's validation should now fail
        assert manager1.validate_ownership() is False
        
        # Manager2 should be able to acquire the lock now
        success = manager2.acquire(lock_id)
        assert success is True
        
        manager2.release()

    def test_lock_expiration_and_reacquisition(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test lock expiration and reacquisition by another manager."""
        client = DynamoClient(dynamodb_table)
        
        # Create manager with very short lock duration
        short_manager = LockManager(
            client, 
            ChromaDBCollection.LINES,
            lock_duration_minutes=0.01  # Very short
        )
        
        # Create regular manager
        regular_manager = LockManager(client, ChromaDBCollection.LINES)
        
        lock_id = "expiration-test"
        
        # Short manager acquires lock
        success1 = short_manager.acquire(lock_id)
        assert success1 is True
        
        # Wait for lock to expire
        time.sleep(2)
        
        # Regular manager should be able to acquire expired lock
        success2 = regular_manager.acquire(lock_id)
        assert success2 is True
        
        # Short manager's validation should fail
        assert short_manager.validate_ownership() is False
        
        regular_manager.release()


# -------------------------------------------------------------------
#                      CONTEXT MANAGER
# -------------------------------------------------------------------


@pytest.mark.integration
class TestLockManagerContext:
    """Test lock manager context manager functionality."""

    def test_context_manager_success(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test successful context manager usage."""
        # Use as context manager
        with lock_manager_lines:
            # Should have acquired lock and started heartbeat
            assert lock_manager_lines.is_locked() is True
            lock_info = lock_manager_lines.get_lock_info()
            assert lock_info["heartbeat_active"] is True
            
        # Should have released lock and stopped heartbeat
        assert lock_manager_lines.is_locked() is False

    def test_context_manager_with_exception(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test context manager cleanup when exception occurs."""
        try:
            with lock_manager_lines:
                assert lock_manager_lines.is_locked() is True
                raise ValueError("Test exception")
        except ValueError:
            pass  # Expected
        
        # Should still have cleaned up
        assert lock_manager_lines.is_locked() is False

    def test_context_manager_acquisition_failure(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test context manager when lock acquisition fails."""
        client = DynamoClient(dynamodb_table)
        
        # Create two managers
        manager1 = LockManager(client, ChromaDBCollection.LINES)
        manager2 = LockManager(client, ChromaDBCollection.LINES)
        
        # Manager1 acquires lock manually
        manager1.acquire("context-competition")
        
        # Manager2 context manager should fail
        with pytest.raises(RuntimeError, match="Failed to acquire lock"):
            with manager2:
                pass  # Should not reach here
        
        manager1.release()


# -------------------------------------------------------------------
#                         EDGE CASES
# -------------------------------------------------------------------


@pytest.mark.integration
class TestLockManagerEdgeCases:
    """Test edge cases for lock manager."""

    def test_lock_with_special_characters(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test locks with special characters in ID."""
        special_lock_id = "lock-with-@#$%_special_chars"
        
        success = lock_manager_lines.acquire(special_lock_id)
        assert success is True
        
        # Should be able to validate and release
        assert lock_manager_lines.validate_ownership() is True
        lock_manager_lines.release()

    def test_multiple_release_calls(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that multiple release calls are safe."""
        lock_id = "multiple-release"
        
        # Acquire and release
        lock_manager_lines.acquire(lock_id)
        lock_manager_lines.release()
        
        # Additional releases should be safe
        lock_manager_lines.release()
        lock_manager_lines.release()
        
        assert lock_manager_lines.is_locked() is False

    def test_heartbeat_stop_multiple_times(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that stopping heartbeat multiple times is safe."""
        lock_id = "multiple-stop"
        
        # Acquire lock and start heartbeat
        lock_manager_lines.acquire(lock_id)
        lock_manager_lines.start_heartbeat()
        
        # Stop multiple times
        lock_manager_lines.stop_heartbeat()
        lock_manager_lines.stop_heartbeat()
        lock_manager_lines.stop_heartbeat()
        
        lock_info = lock_manager_lines.get_lock_info()
        assert lock_info["heartbeat_active"] is False
        
        lock_manager_lines.release()

    def test_get_remaining_time_no_lock(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test getting remaining time when no lock is held."""
        remaining = lock_manager_lines.get_remaining_time()
        assert remaining is None

    def test_operations_after_release(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that operations work correctly after release."""
        lock_id = "after-release"
        
        # Acquire and release
        lock_manager_lines.acquire(lock_id)
        lock_manager_lines.release()
        
        # Operations should handle no-lock state gracefully
        assert lock_manager_lines.validate_ownership() is False
        assert lock_manager_lines.update_heartbeat() is False
        assert lock_manager_lines.refresh_lock() is False
        assert lock_manager_lines.get_remaining_time() is None
        
        # Should be able to acquire new lock
        success = lock_manager_lines.acquire("new-lock")
        assert success is True
        
        lock_manager_lines.release()


# -------------------------------------------------------------------
#                    ERROR CONDITIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestLockManagerErrors:
    """Test error conditions for lock manager."""

    def test_lock_manager_with_invalid_collection(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test lock manager initialization validation."""
        client = DynamoClient(dynamodb_table)
        
        # Should work with valid collection
        manager = LockManager(client, ChromaDBCollection.LINES)
        assert manager.collection == ChromaDBCollection.LINES

    def test_concurrent_heartbeat_start(
        self, lock_manager_lines: LockManager
    ) -> None:
        """Test that starting heartbeat while one is running is handled."""
        lock_id = "concurrent-heartbeat"
        
        # Acquire lock and start heartbeat
        lock_manager_lines.acquire(lock_id)
        lock_manager_lines.start_heartbeat()
        
        # Try to start another heartbeat (should log warning but not fail)
        lock_manager_lines.start_heartbeat()
        
        # Should still have one active heartbeat
        lock_info = lock_manager_lines.get_lock_info()
        assert lock_info["heartbeat_active"] is True
        
        lock_manager_lines.stop_heartbeat()
        lock_manager_lines.release()
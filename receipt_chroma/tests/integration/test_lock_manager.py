"""Integration tests for LockManager using moto to mock DynamoDB."""

import time
from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest
from receipt_chroma.lock_manager import LockManager

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection


@pytest.mark.integration
class TestLockManagerOperations:
    """Test LockManager basic operations."""

    def test_acquire_lock_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        lock_manager_lines: LockManager,
    ) -> None:
        """Test successful lock acquisition."""
        lock_id = "test-lock-1"
        assert lock_manager_lines.acquire(lock_id) is True
        assert lock_manager_lines.is_locked() is True
        assert lock_manager_lines.lock_id == lock_id
        assert lock_manager_lines.lock_owner is not None

        # Verify lock exists in DynamoDB
        client = DynamoClient(dynamodb_table)
        lock = client.get_compaction_lock(lock_id, ChromaDBCollection.LINES)
        assert lock is not None
        assert lock.lock_id == lock_id
        assert lock.owner == lock_manager_lines.lock_owner

    def test_acquire_lock_already_held(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test that acquiring a second lock fails when one is already held."""
        assert lock_manager_lines.acquire("lock-1") is True
        assert lock_manager_lines.acquire("lock-2") is False
        assert lock_manager_lines.lock_id == "lock-1"

    def test_release_lock_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        lock_manager_lines: LockManager,
    ) -> None:
        """Test successful lock release."""
        lock_id = "test-lock-release"
        assert lock_manager_lines.acquire(lock_id) is True

        lock_manager_lines.release()

        assert lock_manager_lines.is_locked() is False
        assert lock_manager_lines.lock_id is None
        assert lock_manager_lines.lock_owner is None

        # Verify lock removed from DynamoDB
        client = DynamoClient(dynamodb_table)
        lock = client.get_compaction_lock(lock_id, ChromaDBCollection.LINES)
        assert lock is None

    def test_release_lock_idempotent(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test that release is idempotent."""
        assert lock_manager_lines.acquire("test-lock") is True
        lock_manager_lines.release()
        lock_manager_lines.release()  # Should not raise
        assert lock_manager_lines.is_locked() is False

    def test_acquire_duplicate_lock_fails(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ) -> None:
        """Test that two managers cannot acquire the same lock."""
        client = DynamoClient(dynamodb_table)
        manager1 = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )
        manager2 = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        lock_id = "duplicate-lock"
        assert manager1.acquire(lock_id) is True
        assert manager2.acquire(lock_id) is False  # Should fail

    def test_get_lock_info(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test getting lock information."""
        assert lock_manager_lines.get_lock_info() is None

        assert lock_manager_lines.acquire("info-lock") is True
        info = lock_manager_lines.get_lock_info()
        assert info is not None
        assert info["lock_id"] == "info-lock"
        assert info["owner"] == lock_manager_lines.lock_owner
        assert info["heartbeat_interval"] == 1
        assert info["lock_duration_minutes"] == 5
        assert info["heartbeat_active"] is False  # Not started yet

    def test_validate_ownership_success(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test ownership validation when lock is valid."""
        assert lock_manager_lines.acquire("ownership-lock") is True
        assert lock_manager_lines.validate_ownership() is True

    def test_validate_ownership_no_lock(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test ownership validation when no lock is held."""
        assert lock_manager_lines.validate_ownership() is False

    def test_validate_ownership_expired(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ) -> None:
        """Test ownership validation when lock has expired."""
        from uuid import uuid4

        from receipt_dynamo.entities.compaction_lock import CompactionLock

        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        # Create an expired lock directly in DynamoDB
        owner_uuid = str(uuid4())
        expired_lock = CompactionLock(
            lock_id="expired-lock",
            owner=owner_uuid,
            expires=datetime.now(timezone.utc) - timedelta(minutes=1),
            collection=ChromaDBCollection.LINES,
        )
        client.add_compaction_lock(expired_lock)

        # Try to validate as if we own it
        manager.lock_id = "expired-lock"
        manager.lock_owner = owner_uuid
        assert manager.validate_ownership() is False

    def test_get_remaining_time(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test getting remaining lock time."""
        assert lock_manager_lines.get_remaining_time() is None

        assert lock_manager_lines.acquire("time-lock") is True
        remaining = lock_manager_lines.get_remaining_time()
        assert remaining is not None
        assert remaining.total_seconds() > 0
        assert remaining.total_seconds() < 300  # Less than 5 minutes

    def test_refresh_lock_success(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test refreshing a valid lock."""
        assert lock_manager_lines.acquire("refresh-lock") is True
        initial_remaining = lock_manager_lines.get_remaining_time()

        # Wait a bit then refresh (give time for lock to age slightly)
        time.sleep(0.2)
        assert lock_manager_lines.refresh_lock() is True

        # Remaining time should be refreshed (close to full duration)
        refreshed_remaining = lock_manager_lines.get_remaining_time()
        assert refreshed_remaining is not None
        # The refreshed time should be greater than or equal to initial
        # (refresh extends the lock duration, so it should be >= initial minus
        # small timing diff)
        # Allow for small timing differences due to test execution speed
        assert (
            refreshed_remaining.total_seconds()
            >= initial_remaining.total_seconds() - 1.0
        )

    def test_refresh_lock_no_lock(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test refreshing when no lock is held."""
        assert lock_manager_lines.refresh_lock() is False

    def test_context_manager(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ) -> None:
        """Test LockManager as a context manager."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        with manager:
            assert manager.is_locked() is True
            assert manager.lock_id == "context_managed_lock"
            # Heartbeat should be started
            assert manager.heartbeat_thread is not None
            assert manager.heartbeat_thread.is_alive()

        # After context exit, lock should be released
        assert manager.is_locked() is False
        assert (
            manager.heartbeat_thread is None or not manager.heartbeat_thread.is_alive()
        )


@pytest.mark.integration
class TestLockManagerHeartbeat:
    """Test LockManager heartbeat functionality."""

    def test_start_heartbeat(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test starting heartbeat thread."""
        assert lock_manager_lines.acquire("heartbeat-lock") is True
        assert lock_manager_lines.heartbeat_thread is None

        lock_manager_lines.start_heartbeat()
        assert lock_manager_lines.heartbeat_thread is not None
        assert lock_manager_lines.heartbeat_thread.is_alive()

        lock_manager_lines.stop_heartbeat()
        time.sleep(0.1)  # Give thread time to stop
        # Thread may be None after stop_heartbeat
        if lock_manager_lines.heartbeat_thread is not None:
            assert not lock_manager_lines.heartbeat_thread.is_alive()

    def test_start_heartbeat_no_lock(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test that starting heartbeat without a lock does nothing."""
        lock_manager_lines.start_heartbeat()
        assert lock_manager_lines.heartbeat_thread is None

    def test_heartbeat_updates_lock(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        lock_manager_lines: LockManager,
    ) -> None:
        """Test that heartbeat thread updates the lock."""
        assert lock_manager_lines.acquire("heartbeat-update-lock") is True
        client = DynamoClient(dynamodb_table)

        # Get initial heartbeat
        initial_lock = client.get_compaction_lock(
            "heartbeat-update-lock", ChromaDBCollection.LINES
        )
        initial_heartbeat = initial_lock.heartbeat

        # Start heartbeat and wait
        lock_manager_lines.start_heartbeat()
        time.sleep(1.5)  # Wait for at least one heartbeat update

        # Get updated lock
        updated_lock = client.get_compaction_lock(
            "heartbeat-update-lock", ChromaDBCollection.LINES
        )
        updated_heartbeat = updated_lock.heartbeat

        # Heartbeat should be updated
        assert updated_heartbeat is not None
        if isinstance(initial_heartbeat, str) and isinstance(updated_heartbeat, str):
            initial_dt = datetime.fromisoformat(
                initial_heartbeat.replace("Z", "+00:00")
            )
            updated_dt = datetime.fromisoformat(
                updated_heartbeat.replace("Z", "+00:00")
            )
            assert updated_dt > initial_dt

        lock_manager_lines.stop_heartbeat()

    def test_heartbeat_failure_handling(
        self,
        lock_manager_lines: LockManager,
        mocker,
    ) -> None:
        """Test that heartbeat failures are tracked."""
        assert lock_manager_lines.acquire("failure-lock") is True
        lock_manager_lines.max_heartbeat_failures = 2

        # Mock update_heartbeat to fail
        mocker.patch.object(lock_manager_lines, "update_heartbeat", return_value=False)

        lock_manager_lines.start_heartbeat()
        time.sleep(2.5)  # Wait for multiple failures

        # After max failures, lock should be released
        assert lock_manager_lines.is_locked() is False
        lock_manager_lines.stop_heartbeat()

    def test_stop_heartbeat_no_thread(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test stopping heartbeat when no thread exists."""
        lock_manager_lines.stop_heartbeat()  # Should not raise

    def test_update_heartbeat_manual(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test manual heartbeat update."""
        assert lock_manager_lines.acquire("manual-heartbeat") is True
        assert lock_manager_lines.update_heartbeat() is True

    def test_update_heartbeat_no_lock(
        self,
        lock_manager_lines: LockManager,
    ) -> None:
        """Test manual heartbeat update without a lock."""
        assert lock_manager_lines.update_heartbeat() is False


@pytest.mark.integration
class TestLockManagerCollections:
    """Test LockManager with different collections."""

    def test_separate_collections_same_lock_id(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ) -> None:
        """Test that same lock_id can exist for different collections."""
        client = DynamoClient(dynamodb_table)
        manager_lines = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )
        manager_words = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.WORDS,
        )

        lock_id = "shared-lock-id"
        assert manager_lines.acquire(lock_id) is True
        assert manager_words.acquire(lock_id) is True  # Different collection

        # Both should be locked
        assert manager_lines.is_locked() is True
        assert manager_words.is_locked() is True

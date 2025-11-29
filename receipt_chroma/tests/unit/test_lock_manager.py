"""Unit tests for LockManager (without DynamoDB)."""

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma.lock_manager import LockManager


@pytest.fixture
def mock_dynamo_client():
    """Create a mock DynamoClient."""
    client = MagicMock()
    return client


@pytest.fixture
def lock_manager(mock_dynamo_client):
    """Create a LockManager with mocked DynamoDB client."""
    return LockManager(
        dynamo_client=mock_dynamo_client,
        collection=ChromaDBCollection.LINES,
        heartbeat_interval=1,
        lock_duration_minutes=5,
    )


class TestLockManagerUnit:
    """Unit tests for LockManager."""

    def test_init(self, mock_dynamo_client):
        """Test LockManager initialization."""
        manager = LockManager(
            dynamo_client=mock_dynamo_client,
            collection=ChromaDBCollection.LINES,
            heartbeat_interval=30,
            lock_duration_minutes=10,
            max_heartbeat_failures=5,
        )

        assert manager.dynamo_client == mock_dynamo_client
        assert manager.collection == ChromaDBCollection.LINES
        assert manager.heartbeat_interval == 30
        assert manager.lock_duration_minutes == 10
        assert manager.max_heartbeat_failures == 5
        assert manager.lock_id is None
        assert manager.lock_owner is None
        assert manager.is_locked() is False

    def test_acquire_lock_success(self, lock_manager, mock_dynamo_client):
        """Test successful lock acquisition."""
        mock_dynamo_client.add_compaction_lock.return_value = None

        result = lock_manager.acquire("test-lock")

        assert result is True
        assert lock_manager.is_locked() is True
        assert lock_manager.lock_id == "test-lock"
        assert lock_manager.lock_owner is not None
        mock_dynamo_client.add_compaction_lock.assert_called_once()

    def test_acquire_lock_already_held(self, lock_manager, mock_dynamo_client):
        """Test that acquiring a second lock fails."""
        mock_dynamo_client.add_compaction_lock.return_value = None

        assert lock_manager.acquire("lock-1") is True
        assert lock_manager.acquire("lock-2") is False
        assert lock_manager.lock_id == "lock-1"

    def test_acquire_lock_failure(self, lock_manager, mock_dynamo_client):
        """Test lock acquisition failure."""
        from botocore.exceptions import ClientError

        mock_dynamo_client.add_compaction_lock.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "DB error",
                }
            },
            "add_compaction_lock",
        )

        result = lock_manager.acquire("test-lock")

        assert result is False
        assert lock_manager.is_locked() is False

    def test_release_lock_success(self, lock_manager, mock_dynamo_client):
        """Test successful lock release."""
        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.delete_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        lock_manager.release()

        assert lock_manager.is_locked() is False
        assert lock_manager.lock_id is None
        assert lock_manager.lock_owner is None
        mock_dynamo_client.delete_compaction_lock.assert_called_once()

    def test_release_lock_idempotent(self, lock_manager):
        """Test that release is idempotent."""
        lock_manager.release()  # Should not raise
        lock_manager.release()  # Should not raise

    def test_release_lock_error(self, lock_manager, mock_dynamo_client):
        """Test lock release with error."""
        from botocore.exceptions import ClientError

        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.delete_compaction_lock.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "Error"}},
            "delete_compaction_lock",
        )

        lock_manager.acquire("test-lock")
        lock_manager.release()

        # State should still be cleared even on error
        assert lock_manager.is_locked() is False

    def test_start_heartbeat_no_lock(self, lock_manager):
        """Test starting heartbeat without a lock."""
        lock_manager.start_heartbeat()
        assert lock_manager.heartbeat_thread is None

    def test_start_heartbeat_success(self, lock_manager, mock_dynamo_client):
        """Test starting heartbeat thread."""
        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        lock_manager.start_heartbeat()

        assert lock_manager.heartbeat_thread is not None
        assert lock_manager.heartbeat_thread.is_alive()

        lock_manager.stop_heartbeat()
        time.sleep(0.1)

    def test_stop_heartbeat(self, lock_manager, mock_dynamo_client):
        """Test stopping heartbeat thread."""
        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        lock_manager.start_heartbeat()
        assert lock_manager.heartbeat_thread.is_alive()

        lock_manager.stop_heartbeat()
        time.sleep(0.1)
        # Thread may be None after stop_heartbeat
        if lock_manager.heartbeat_thread is not None:
            assert not lock_manager.heartbeat_thread.is_alive()

    def test_update_heartbeat_success(self, lock_manager, mock_dynamo_client):
        """Test successful heartbeat update."""
        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        result = lock_manager.update_heartbeat()

        assert result is True
        mock_dynamo_client.update_compaction_lock.assert_called_once()

    def test_update_heartbeat_no_lock(self, lock_manager):
        """Test heartbeat update without a lock."""
        result = lock_manager.update_heartbeat()
        assert result is False

    def test_update_heartbeat_failure(self, lock_manager, mock_dynamo_client):
        """Test heartbeat update failure."""
        from botocore.exceptions import ClientError

        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "Error"}},
            "update_compaction_lock",
        )

        lock_manager.acquire("test-lock")
        result = lock_manager.update_heartbeat()

        assert result is False

    def test_is_locked(self, lock_manager, mock_dynamo_client):
        """Test is_locked method."""
        assert lock_manager.is_locked() is False

        mock_dynamo_client.add_compaction_lock.return_value = None
        lock_manager.acquire("test-lock")
        assert lock_manager.is_locked() is True

        lock_manager.release()
        assert lock_manager.is_locked() is False

    def test_get_lock_info_no_lock(self, lock_manager):
        """Test getting lock info when no lock is held."""
        assert lock_manager.get_lock_info() is None

    def test_get_lock_info_with_lock(self, lock_manager, mock_dynamo_client):
        """Test getting lock info when lock is held."""
        mock_dynamo_client.add_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        info = lock_manager.get_lock_info()

        assert info is not None
        assert info["lock_id"] == "test-lock"
        assert info["owner"] == lock_manager.lock_owner
        assert info["heartbeat_interval"] == 1
        assert info["lock_duration_minutes"] == 5

    def test_validate_ownership_no_lock(self, lock_manager):
        """Test ownership validation without a lock."""
        assert lock_manager.validate_ownership() is False

    def test_validate_ownership_success(
        self, lock_manager, mock_dynamo_client
    ):
        """Test successful ownership validation."""
        from receipt_dynamo.entities.compaction_lock import CompactionLock

        mock_dynamo_client.add_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        owner = lock_manager.lock_owner

        # Mock get_compaction_lock to return a valid lock
        valid_lock = CompactionLock(
            lock_id="test-lock",
            owner=owner,
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )
        mock_dynamo_client.get_compaction_lock.return_value = valid_lock

        assert lock_manager.validate_ownership() is True

    def test_validate_ownership_expired(
        self, lock_manager, mock_dynamo_client
    ):
        """Test ownership validation with expired lock."""
        from receipt_dynamo.entities.compaction_lock import CompactionLock

        mock_dynamo_client.add_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        owner = lock_manager.lock_owner

        # Mock get_compaction_lock to return an expired lock
        expired_lock = CompactionLock(
            lock_id="test-lock",
            owner=owner,
            expires=datetime.now(timezone.utc) - timedelta(minutes=1),
            collection=ChromaDBCollection.LINES,
        )
        mock_dynamo_client.get_compaction_lock.return_value = expired_lock

        assert lock_manager.validate_ownership() is False

    def test_validate_ownership_wrong_owner(
        self, lock_manager, mock_dynamo_client
    ):
        """Test ownership validation with wrong owner."""
        from uuid import uuid4

        from receipt_dynamo.entities.compaction_lock import CompactionLock

        mock_dynamo_client.add_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")

        # Mock get_compaction_lock to return a lock with different owner
        wrong_lock = CompactionLock(
            lock_id="test-lock",
            owner=str(uuid4()),  # Different UUID
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )
        mock_dynamo_client.get_compaction_lock.return_value = wrong_lock

        assert lock_manager.validate_ownership() is False

    def test_get_remaining_time_no_lock(self, lock_manager):
        """Test getting remaining time without a lock."""
        assert lock_manager.get_remaining_time() is None

    def test_get_remaining_time_success(
        self, lock_manager, mock_dynamo_client
    ):
        """Test getting remaining time successfully."""
        from receipt_dynamo.entities.compaction_lock import CompactionLock

        mock_dynamo_client.add_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        owner = lock_manager.lock_owner

        # Mock get_compaction_lock to return a valid lock
        valid_lock = CompactionLock(
            lock_id="test-lock",
            owner=owner,
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )
        mock_dynamo_client.get_compaction_lock.return_value = valid_lock

        remaining = lock_manager.get_remaining_time()
        assert remaining is not None
        assert remaining.total_seconds() > 0

    def test_refresh_lock_success(self, lock_manager, mock_dynamo_client):
        """Test successful lock refresh."""
        from receipt_dynamo.entities.compaction_lock import CompactionLock

        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        owner = lock_manager.lock_owner

        # Mock validate_ownership to succeed
        valid_lock = CompactionLock(
            lock_id="test-lock",
            owner=owner,
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )
        mock_dynamo_client.get_compaction_lock.return_value = valid_lock

        result = lock_manager.refresh_lock()
        assert result is True
        mock_dynamo_client.update_compaction_lock.assert_called_once()

    def test_refresh_lock_no_lock(self, lock_manager):
        """Test refreshing without a lock."""
        assert lock_manager.refresh_lock() is False

    def test_refresh_lock_validation_fails(
        self, lock_manager, mock_dynamo_client
    ):
        """Test refresh when ownership validation fails."""
        mock_dynamo_client.add_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")

        # Mock validate_ownership to fail
        mock_dynamo_client.get_compaction_lock.return_value = None

        result = lock_manager.refresh_lock()
        assert result is False

    def test_context_manager_success(self, lock_manager, mock_dynamo_client):
        """Test LockManager as context manager."""
        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.delete_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.return_value = None

        with lock_manager:
            assert lock_manager.is_locked() is True
            assert lock_manager.lock_id == "context_managed_lock"
            assert lock_manager.heartbeat_thread is not None

        assert lock_manager.is_locked() is False

    def test_context_manager_acquire_fails(
        self, lock_manager, mock_dynamo_client
    ):
        """Test context manager when lock acquisition fails."""
        from botocore.exceptions import ClientError

        mock_dynamo_client.add_compaction_lock.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Error",
                }
            },
            "add_compaction_lock",
        )

        with pytest.raises(RuntimeError, match="Failed to acquire lock"):
            with lock_manager:
                pass

    def test_heartbeat_worker_stops_on_signal(
        self, lock_manager, mock_dynamo_client
    ):
        """Test that heartbeat worker stops when signaled."""
        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.return_value = None

        lock_manager.acquire("test-lock")
        lock_manager.start_heartbeat()

        # Signal stop
        lock_manager.stop_heartbeat_event.set()
        time.sleep(0.2)

        # Thread should stop
        assert not lock_manager.heartbeat_thread.is_alive()

    def test_heartbeat_worker_max_failures(
        self, lock_manager, mock_dynamo_client
    ):
        """Test heartbeat worker stops after max failures."""
        from botocore.exceptions import ClientError

        mock_dynamo_client.add_compaction_lock.return_value = None
        mock_dynamo_client.update_compaction_lock.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "Error"}},
            "update_compaction_lock",
        )

        lock_manager.max_heartbeat_failures = 2
        lock_manager.acquire("test-lock")
        lock_manager.start_heartbeat()

        # Wait for failures
        time.sleep(2.5)

        # Lock should be released after max failures
        assert lock_manager.is_locked() is False
        lock_manager.stop_heartbeat()

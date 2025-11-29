"""Additional integration tests to improve LockManager coverage."""

import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.entities.compaction_lock import CompactionLock

from receipt_chroma.lock_manager import LockManager


@pytest.mark.integration
class TestLockManagerEdgeCases:
    """Test LockManager edge cases and error paths."""

    def test_validate_ownership_lock_not_found(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ):
        """Test ownership validation when lock doesn't exist (line 381)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.lock_id = "nonexistent-lock"
        manager.lock_owner = str(uuid4())

        # Lock doesn't exist in DynamoDB
        assert manager.validate_ownership() is False

    def test_validate_ownership_expires_string_parsing(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ):
        """Test ownership validation with string expiration dates (lines 404-410)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        # Acquire lock - this sets the owner
        manager.acquire("test-lock")
        owner = manager.lock_owner

        # Get the lock and verify it has string expires
        lock = client.get_compaction_lock(
            "test-lock", ChromaDBCollection.LINES
        )
        assert isinstance(lock.expires, str)
        assert lock.owner == owner  # Verify owner matches

        # Should validate successfully
        assert manager.validate_ownership() is True

    def test_validate_ownership_invalid_expires_type(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test ownership validation with invalid expires type (line 432)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Mock get_compaction_lock to return a lock with invalid expires
        mock_lock = CompactionLock(
            lock_id="test-lock",
            owner=manager.lock_owner,
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )
        # Manually set expires to invalid type
        mock_lock.expires = 12345  # type: ignore[assignment]

        mocker.patch.object(
            client, "get_compaction_lock", return_value=mock_lock
        )

        assert manager.validate_ownership() is False

    def test_get_remaining_time_error_handling(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test error handling in get_remaining_time (lines 449-455, 494-500)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Mock get_compaction_lock to raise an exception
        mocker.patch.object(
            client,
            "get_compaction_lock",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "DynamoDB error",
                    }
                },
                "get_compaction_lock",
            ),
        )

        # Should return None on error
        assert manager.get_remaining_time() is None

    def test_get_remaining_time_string_expires(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ):
        """Test get_remaining_time with string expires (lines 490-495)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Get the lock (which has string expires)
        lock = client.get_compaction_lock(
            manager.lock_id, ChromaDBCollection.LINES
        )
        assert isinstance(lock.expires, str)

        # Should work with string expires
        remaining = manager.get_remaining_time()
        assert remaining is not None
        assert remaining.total_seconds() > 0

    def test_get_remaining_time_expired_lock(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ):
        """Test get_remaining_time with expired lock."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        # Create an expired lock
        expired_lock = CompactionLock(
            lock_id="expired-lock",
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) - timedelta(minutes=1),
            collection=ChromaDBCollection.LINES,
        )
        client.add_compaction_lock(expired_lock)

        manager.lock_id = "expired-lock"
        manager.lock_owner = expired_lock.owner

        # Should return timedelta(0) for expired lock
        remaining = manager.get_remaining_time()
        assert remaining is not None
        assert remaining.total_seconds() == 0

    def test_heartbeat_worker_stop_signal(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ):
        """Test heartbeat worker stops on signal (lines 303-304)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
            heartbeat_interval=2,  # Longer interval
        )

        manager.acquire("test-lock")
        manager.start_heartbeat()

        # Signal stop immediately
        manager.stop_heartbeat_event.set()
        time.sleep(0.2)

        # Thread should stop
        if manager.heartbeat_thread is not None:
            assert not manager.heartbeat_thread.is_alive()

    def test_heartbeat_worker_multiple_failures(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test heartbeat worker with multiple failures (lines 268-306)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
            heartbeat_interval=1,
            max_heartbeat_failures=2,
        )

        manager.acquire("test-lock")

        # Mock update_heartbeat to fail
        mocker.patch.object(manager, "update_heartbeat", return_value=False)

        manager.start_heartbeat()

        # Wait for failures
        time.sleep(2.5)

        # Lock should be released after max failures
        assert manager.is_locked() is False
        manager.stop_heartbeat()

    def test_stop_heartbeat_timeout(
        self,
        dynamodb_table: Literal["MyMockedTable"],
    ):
        """Test stop_heartbeat with thread that doesn't stop (line 212)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
            heartbeat_interval=1,
        )

        manager.acquire("test-lock")
        manager.start_heartbeat()

        # Mock thread.join to not return (simulating stuck thread)
        original_join = manager.heartbeat_thread.join
        call_count = {"count": 0}

        def mock_join(timeout=None):
            call_count["count"] += 1
            if call_count["count"] == 1:
                # First call - actually join
                original_join(timeout=timeout)
            # Otherwise don't join (simulating stuck)

        manager.heartbeat_thread.join = mock_join  # type: ignore[assignment]

        # Stop heartbeat - should handle timeout gracefully
        manager.stop_heartbeat()

        # Should have attempted to join
        assert call_count["count"] > 0

    def test_release_clears_state_on_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test that release clears state even on error (lines 178-179)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Mock delete to raise an error
        mocker.patch.object(
            client,
            "delete_compaction_lock",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "Delete error",
                    }
                },
                "delete_compaction_lock",
            ),
        )

        # Release should still clear state (lines 178-179)
        manager.release()

        assert manager.is_locked() is False
        assert manager.lock_id is None
        assert manager.lock_owner is None

    def test_validate_ownership_exception_handling(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test exception handling in validate_ownership (lines 404-410)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Mock get_compaction_lock to raise an exception
        mocker.patch.object(
            client,
            "get_compaction_lock",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "DynamoDB error",
                    }
                },
                "get_compaction_lock",
            ),
        )

        # Should return False on exception (lines 404-410)
        assert manager.validate_ownership() is False

    def test_get_remaining_time_exception_handling(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test exception handling in get_remaining_time (lines 432, 440, 494-500)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Mock get_compaction_lock to raise an exception
        mocker.patch.object(
            client,
            "get_compaction_lock",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "DynamoDB error",
                    }
                },
                "get_compaction_lock",
            ),
        )

        # Should return None on exception (lines 494-500)
        assert manager.get_remaining_time() is None

    def test_refresh_lock_exception_handling(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test exception handling in refresh_lock (lines 494-500)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
        )

        manager.acquire("test-lock")

        # Mock update_compaction_lock to raise an exception
        mocker.patch.object(
            client,
            "update_compaction_lock",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "Update error",
                    }
                },
                "update_compaction_lock",
            ),
        )

        # Should return False on exception (lines 494-500)
        assert manager.refresh_lock() is False

    def test_stop_heartbeat_thread_timeout(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        mocker,
    ):
        """Test stop_heartbeat with thread timeout (line 212)."""
        client = DynamoClient(dynamodb_table)
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.LINES,
            heartbeat_interval=1,
        )

        manager.acquire("test-lock")
        manager.start_heartbeat()

        # Mock thread.join to simulate timeout
        original_join = manager.heartbeat_thread.join
        call_count = {"count": 0}

        def mock_join(timeout=None):
            call_count["count"] += 1
            # Simulate timeout - thread doesn't stop
            time.sleep(0.1)
            # Don't actually join

        manager.heartbeat_thread.join = mock_join  # type: ignore[assignment]

        # Stop heartbeat - should handle timeout (line 212)
        manager.stop_heartbeat()

        # Should have attempted to join
        assert call_count["count"] > 0

"""
Integration tests for CompactionLock operations in DynamoDB.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Type
from unittest.mock import patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.compaction_lock import CompactionLock

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def example_compaction_lock() -> CompactionLock:
    """Create a sample CompactionLock for testing."""
    return CompactionLock(
        lock_id="test-lock-id",
        owner=str(uuid4()),
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
        collection=ChromaDBCollection.LINES,
        heartbeat=datetime.now(timezone.utc),
    )


@pytest.fixture
def minimal_compaction_lock() -> CompactionLock:
    """Create a minimal CompactionLock for testing."""
    return CompactionLock(
        lock_id="minimal-lock",
        owner=str(uuid4()),
        expires="2024-12-01T12:00:00Z",
        collection=ChromaDBCollection.WORDS,
    )


@pytest.fixture
def example_compaction_locks() -> List[CompactionLock]:
    """Create a list of CompactionLocks for batch testing."""
    now = datetime.now(timezone.utc)
    return [
        CompactionLock(
            lock_id="lock-1",
            owner=str(uuid4()),
            expires=now + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        ),
        CompactionLock(
            lock_id="lock-2",
            owner=str(uuid4()),
            expires=now + timedelta(minutes=10),
            collection=ChromaDBCollection.WORDS,
        ),
        CompactionLock(
            lock_id="lock-3",
            owner=str(uuid4()),
            expires=now + timedelta(minutes=15),
            collection=ChromaDBCollection.LINES,
        ),
    ]


# -------------------------------------------------------------------
#                    SUCCESSFUL OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestCompactionLockOperations:
    """Test compaction lock CRUD operations."""

    def test_add_compaction_lock_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test successful addition of a compaction lock."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(example_compaction_lock)

        result = client.get_compaction_lock(
            example_compaction_lock.lock_id, example_compaction_lock.collection
        )
        assert result == example_compaction_lock

    def test_add_compaction_lock_minimal(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        minimal_compaction_lock: CompactionLock,
    ) -> None:
        """Test addition of a minimal compaction lock."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(minimal_compaction_lock)

        result = client.get_compaction_lock(
            minimal_compaction_lock.lock_id, minimal_compaction_lock.collection
        )
        assert result == minimal_compaction_lock
        assert result.heartbeat is None

    def test_get_compaction_lock_not_found(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that getting a non-existent lock returns None."""
        client = DynamoClient(dynamodb_table)
        result = client.get_compaction_lock(
            "NON_EXISTENT_LOCK", ChromaDBCollection.LINES
        )
        assert result is None

    def test_update_compaction_lock_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test successful update of a compaction lock."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(example_compaction_lock)

        # Update with new heartbeat and expiration
        updated_lock = CompactionLock(
            lock_id=example_compaction_lock.lock_id,
            owner=example_compaction_lock.owner,
            expires=datetime.now(timezone.utc) + timedelta(minutes=10),
            collection=example_compaction_lock.collection,
            heartbeat=datetime.now(timezone.utc),
        )

        client.update_compaction_lock(updated_lock)
        result = client.get_compaction_lock(
            example_compaction_lock.lock_id, example_compaction_lock.collection
        )

        assert result == updated_lock
        assert result.heartbeat != example_compaction_lock.heartbeat

    def test_delete_compaction_lock_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test successful deletion of a compaction lock."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(example_compaction_lock)

        client.delete_compaction_lock(
            example_compaction_lock.lock_id,
            example_compaction_lock.owner,
            example_compaction_lock.collection,
        )

        result = client.get_compaction_lock(
            example_compaction_lock.lock_id, example_compaction_lock.collection
        )
        assert result is None

    def test_add_lock_with_expired_lock_exists(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test that adding a lock succeeds when existing lock is expired."""
        client = DynamoClient(dynamodb_table)

        # Create an expired lock
        expired_lock = CompactionLock(
            lock_id=example_compaction_lock.lock_id,
            owner=str(uuid4()),  # Different owner
            expires=datetime.now(timezone.utc) - timedelta(minutes=1),  # Expired
            collection=example_compaction_lock.collection,
        )
        client.add_compaction_lock(expired_lock)

        # Should be able to add new lock with same ID because old one is expired
        client.add_compaction_lock(example_compaction_lock)

        result = client.get_compaction_lock(
            example_compaction_lock.lock_id, example_compaction_lock.collection
        )
        assert result == example_compaction_lock
        assert result.owner != expired_lock.owner

    def test_collection_isolation(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that locks are isolated by collection."""
        client = DynamoClient(dynamodb_table)

        # Create locks with same ID but different collections
        lock_id = "shared-lock-id"
        lines_lock = CompactionLock(
            lock_id=lock_id,
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )
        words_lock = CompactionLock(
            lock_id=lock_id,
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.WORDS,
        )

        # Both should be addable
        client.add_compaction_lock(lines_lock)
        client.add_compaction_lock(words_lock)

        # Both should be retrievable independently
        lines_result = client.get_compaction_lock(lock_id, ChromaDBCollection.LINES)
        words_result = client.get_compaction_lock(lock_id, ChromaDBCollection.WORDS)

        assert lines_result == lines_lock
        assert words_result == words_lock
        assert lines_result.owner != words_result.owner


# -------------------------------------------------------------------
#                        ERROR CONDITIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestCompactionLockErrors:
    """Test error conditions for compaction lock operations."""

    def test_add_duplicate_active_lock_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test that adding a duplicate active lock raises error."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(example_compaction_lock)

        # Try to add duplicate with same ID and collection
        duplicate_lock = CompactionLock(
            lock_id=example_compaction_lock.lock_id,
            owner=str(uuid4()),  # Different owner
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=example_compaction_lock.collection,
        )

        with pytest.raises(EntityAlreadyExistsError, match="already exists"):
            client.add_compaction_lock(duplicate_lock)

    def test_update_non_existent_lock_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test that updating a non-existent lock raises error."""
        client = DynamoClient(dynamodb_table)

        with pytest.raises(EntityNotFoundError, match="not found"):
            client.update_compaction_lock(example_compaction_lock)

    def test_delete_non_existent_lock_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that deleting a non-existent lock raises error."""
        client = DynamoClient(dynamodb_table)

        with pytest.raises(
            EntityNotFoundError,
            match="Lock 'NON_EXISTENT' for collection 'lines' not found",
        ):
            client.delete_compaction_lock(
                "NON_EXISTENT", str(uuid4()), ChromaDBCollection.LINES
            )

    def test_delete_lock_wrong_owner_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test that deleting with wrong owner raises error."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(example_compaction_lock)

        wrong_owner = str(uuid4())
        with pytest.raises(
            EntityValidationError,
            match=f"Cannot delete lock '{example_compaction_lock.lock_id}' for collection 'lines' - owned by {example_compaction_lock.owner}",
        ):
            client.delete_compaction_lock(
                example_compaction_lock.lock_id,
                wrong_owner,
                example_compaction_lock.collection,
            )


# -------------------------------------------------------------------
#                       VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestCompactionLockValidation:
    """Test validation for compaction lock operations."""

    def test_add_compaction_lock_none_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding None raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError, match="lock cannot be None"):
            client.add_compaction_lock(None)  # type: ignore

    def test_add_compaction_lock_wrong_type_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding wrong type raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(
            EntityValidationError,
            match="lock must be an instance of CompactionLock",
        ):
            client.add_compaction_lock("not-a-lock")  # type: ignore

    def test_get_compaction_lock_empty_lock_id_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that getting with empty lock_id raises error."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError, match="lock_id cannot be empty"):
            client.get_compaction_lock("", ChromaDBCollection.LINES)

    def test_delete_compaction_lock_empty_params_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that delete with empty parameters raises error."""
        client = DynamoClient(dynamodb_table)

        # Empty lock_id
        with pytest.raises(EntityValidationError, match="lock_id cannot be empty"):
            client.delete_compaction_lock("", str(uuid4()), ChromaDBCollection.LINES)

        # Empty owner
        with pytest.raises(EntityValidationError, match="owner cannot be empty"):
            client.delete_compaction_lock("test-lock", "", ChromaDBCollection.LINES)


# -------------------------------------------------------------------
#                         LIST OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestCompactionLockListing:
    """Test listing operations for compaction locks."""

    def test_list_compaction_locks_empty(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test listing when no locks exist."""
        client = DynamoClient(dynamodb_table)
        results, pagination_token = client.list_compaction_locks()
        assert results == []
        assert pagination_token is None

    def test_list_compaction_locks_with_data(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_locks: List[CompactionLock],
    ) -> None:
        """Test listing compaction locks."""
        client = DynamoClient(dynamodb_table)

        for lock in example_compaction_locks:
            client.add_compaction_lock(lock)

        results, pagination_token = client.list_compaction_locks()
        assert len(results) == 3
        assert pagination_token is None

        # Verify all locks are present
        result_ids = {lock.lock_id for lock in results}
        expected_ids = {lock.lock_id for lock in example_compaction_locks}
        assert result_ids == expected_ids

    def test_list_compaction_locks_pagination(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_locks: List[CompactionLock],
    ) -> None:
        """Test pagination through compaction locks."""
        client = DynamoClient(dynamodb_table)

        for lock in example_compaction_locks:
            client.add_compaction_lock(lock)

        # Get first page
        first_results, first_key = client.list_compaction_locks(limit=2)
        assert len(first_results) == 2
        assert first_key is not None

        # Get second page
        second_results, second_key = client.list_compaction_locks(
            limit=2, last_evaluated_key=first_key
        )
        assert len(second_results) == 1
        assert second_key is None

        # Verify no overlap
        first_ids = {lock.lock_id for lock in first_results}
        second_ids = {lock.lock_id for lock in second_results}
        assert first_ids.isdisjoint(second_ids)

    def test_list_active_compaction_locks(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test listing only active (non-expired) locks."""
        client = DynamoClient(dynamodb_table)
        now = datetime.now(timezone.utc)

        # Create expired lock
        expired_lock = CompactionLock(
            lock_id="expired-lock",
            owner=str(uuid4()),
            expires=now - timedelta(minutes=1),
            collection=ChromaDBCollection.LINES,
        )

        # Create active lock
        active_lock = CompactionLock(
            lock_id="active-lock",
            owner=str(uuid4()),
            expires=now + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )

        client.add_compaction_lock(expired_lock)
        client.add_compaction_lock(active_lock)

        # Only active lock should be returned
        results, _ = client.list_active_compaction_locks()
        assert len(results) == 1
        assert results[0].lock_id == "active-lock"


# -------------------------------------------------------------------
#                     CLEANUP OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
class TestCompactionLockCleanup:
    """Test cleanup operations for compaction locks."""

    def test_cleanup_expired_locks_none_expired(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
    ) -> None:
        """Test cleanup when no locks are expired."""
        client = DynamoClient(dynamodb_table)
        client.add_compaction_lock(example_compaction_lock)

        count = client.cleanup_expired_locks()
        assert count == 0

        # Lock should still exist
        result = client.get_compaction_lock(
            example_compaction_lock.lock_id, example_compaction_lock.collection
        )
        assert result is not None

    def test_cleanup_expired_locks_some_expired(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test cleanup when some locks are expired."""
        client = DynamoClient(dynamodb_table)
        now = datetime.now(timezone.utc)

        # Create expired locks
        expired_locks = [
            CompactionLock(
                lock_id=f"expired-{i}",
                owner=str(uuid4()),
                expires=now - timedelta(minutes=i + 1),
                collection=ChromaDBCollection.LINES,
            )
            for i in range(2)
        ]

        # Create active lock
        active_lock = CompactionLock(
            lock_id="active",
            owner=str(uuid4()),
            expires=now + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )

        for lock in expired_locks + [active_lock]:
            client.add_compaction_lock(lock)

        # Cleanup should remove expired locks
        count = client.cleanup_expired_locks()
        assert count == 2

        # Only active lock should remain
        active_result = client.get_compaction_lock("active", ChromaDBCollection.LINES)
        assert active_result is not None

        for expired in expired_locks:
            expired_result = client.get_compaction_lock(
                expired.lock_id, expired.collection
            )
            assert expired_result is None


# -------------------------------------------------------------------
#                    ERROR HANDLING TESTS
# -------------------------------------------------------------------


ERROR_SCENARIOS = [
    (
        "ProvisionedThroughputExceededException",
        DynamoDBThroughputError,
        "Throughput exceeded",
    ),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    (
        "ResourceNotFoundException",
        OperationError,
        "DynamoDB resource not found",
    ),
    (
        "ItemCollectionSizeLimitExceededException",
        DynamoDBError,
        "DynamoDB error during",
    ),
    ("TransactionConflictException", DynamoDBError, "DynamoDB error during"),
    ("RequestLimitExceeded", DynamoDBError, "DynamoDB error during"),
    ("ServiceUnavailable", DynamoDBServerError, "DynamoDB server error"),
    ("UnknownError", DynamoDBError, "DynamoDB error during"),
]


@pytest.mark.integration
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
class TestCompactionLockErrorHandling:
    """Test error handling for CompactionLock operations."""

    def test_add_compaction_lock_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB client errors are properly handled in add operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "put_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem",
            ),
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.add_compaction_lock(example_compaction_lock)

    def test_update_compaction_lock_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_compaction_lock: CompactionLock,
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB client errors are properly handled in update operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "put_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem",
            ),
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.update_compaction_lock(example_compaction_lock)

    def test_get_compaction_lock_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB client errors are properly handled in get operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "get_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "GetItem",
            ),
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.get_compaction_lock("test-lock", ChromaDBCollection.LINES)

    def test_delete_compaction_lock_client_errors(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        error_code: str,
        expected_exception: Type[Exception],
        error_match: str,
    ) -> None:
        """Test that DynamoDB client errors are properly handled in delete operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "delete_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "DeleteItem",
            ),
        ):
            with pytest.raises(expected_exception, match=error_match):
                client.delete_compaction_lock(
                    "test-lock", str(uuid4()), ChromaDBCollection.LINES
                )


# -------------------------------------------------------------------
#                          EDGE CASES
# -------------------------------------------------------------------


@pytest.mark.integration
class TestCompactionLockEdgeCases:
    """Test edge cases for compaction lock operations."""

    def test_lock_with_special_characters_in_id(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test locks with special characters in lock_id."""
        client = DynamoClient(dynamodb_table)

        special_lock = CompactionLock(
            lock_id="lock-with-@#$%_special_chars",
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )

        client.add_compaction_lock(special_lock)
        result = client.get_compaction_lock(
            special_lock.lock_id, special_lock.collection
        )
        assert result == special_lock

    def test_lock_with_very_long_id(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test locks with very long lock_id."""
        client = DynamoClient(dynamodb_table)

        long_id = "a" * 500  # Very long ID
        long_lock = CompactionLock(
            lock_id=long_id,
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.WORDS,
        )

        client.add_compaction_lock(long_lock)
        result = client.get_compaction_lock(long_id, ChromaDBCollection.WORDS)
        assert result == long_lock
        assert result.lock_id == long_id

    def test_lock_id_with_hash_symbols(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test locks with hash symbols in lock_id."""
        client = DynamoClient(dynamodb_table)

        hash_lock = CompactionLock(
            lock_id="lock#with#hash#symbols",
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) + timedelta(minutes=5),
            collection=ChromaDBCollection.LINES,
        )

        client.add_compaction_lock(hash_lock)
        result = client.get_compaction_lock(hash_lock.lock_id, hash_lock.collection)
        assert result == hash_lock
        assert "#" in result.lock_id

    def test_concurrent_lock_operations(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that concurrent operations on different locks work."""
        client = DynamoClient(dynamodb_table)

        # Create locks for different collections with same ID
        lock_id = "concurrent-test"
        locks = [
            CompactionLock(
                lock_id=lock_id,
                owner=str(uuid4()),
                expires=datetime.now(timezone.utc) + timedelta(minutes=5),
                collection=collection,
            )
            for collection in [
                ChromaDBCollection.LINES,
                ChromaDBCollection.WORDS,
            ]
        ]

        # Add both locks simultaneously
        for lock in locks:
            client.add_compaction_lock(lock)

        # Verify both exist independently
        for lock in locks:
            result = client.get_compaction_lock(lock.lock_id, lock.collection)
            assert result == lock

        # Delete one, verify the other remains
        client.delete_compaction_lock(
            locks[0].lock_id, locks[0].owner, locks[0].collection
        )

        deleted_result = client.get_compaction_lock(
            locks[0].lock_id, locks[0].collection
        )
        assert deleted_result is None

        remaining_result = client.get_compaction_lock(
            locks[1].lock_id, locks[1].collection
        )
        assert remaining_result == locks[1]

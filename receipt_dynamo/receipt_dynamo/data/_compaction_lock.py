"""
Accessor methods for CompactionLock items in DynamoDB.

This module provides methods for managing distributed locks used to coordinate
Chroma compaction jobs across multiple workers.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DeleteRequestTypeDef,
    FlattenedStandardMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.entities.compaction_lock import (
    CompactionLock,
    item_to_compaction_lock,
)

if TYPE_CHECKING:
    pass


class _CompactionLock(FlattenedStandardMixin):
    """Accessor methods for CompactionLock items in DynamoDB."""

    @handle_dynamodb_errors("add_compaction_lock")
    def add_compaction_lock(self, lock: CompactionLock) -> None:
        """
        Adds a compaction lock with conditional check.

        The lock will only be added if:
        - No lock exists with this ID, OR
        - The existing lock has expired

        Args:
            lock: The CompactionLock to add

        Raises:
            EntityAlreadyExistsError: If lock is held by another owner and not
                expired
            EntityValidationError: If lock is None or wrong type
        """
        if lock is None:
            raise EntityValidationError("lock cannot be None")
        if not isinstance(lock, CompactionLock):
            raise EntityValidationError("lock must be an instance of CompactionLock")

        # Since _add_entity doesn't support complex conditions with expression
        # values, we need to handle this at the DynamoDB client level
        now = datetime.now(timezone.utc).isoformat()

        put_params = {
            "TableName": self.table_name,
            "Item": lock.to_item(),
            "ConditionExpression": (
                "attribute_not_exists(PK) OR expires < :now"
            ),
            "ExpressionAttributeValues": {":now": {"S": now}},
        }

        # The decorator will handle ConditionalCheckFailedException and raise
        # EntityAlreadyExistsError because this is an "add_" operation
        self._client.put_item(**put_params)

    @handle_dynamodb_errors("delete_compaction_lock")
    def delete_compaction_lock(self, lock_id: str, owner: str, collection: "ChromaDBCollection") -> None:
        """
        Deletes a compaction lock if owned by the specified owner.

        Args:
            lock_id: The ID of the lock to delete
            owner: The UUID of the owner deleting the lock
            collection: The ChromaDB collection this lock protects

        Raises:
            EntityNotFoundError: If lock doesn't exist
            EntityValidationError: If owner doesn't match
        """
        if not lock_id:
            raise EntityValidationError("lock_id cannot be empty")
        if not owner:
            raise EntityValidationError("owner cannot be empty")

        # Use low-level client for conditional delete
        # Note: 'owner' is a reserved keyword in DynamoDB, 
        # so we use ExpressionAttributeNames
        delete_params = {
            "TableName": self.table_name,
            "Key": {"PK": {"S": f"LOCK#{collection.value}#{lock_id}"}, "SK": {"S": "LOCK"}},
            "ConditionExpression": "#owner = :owner",
            "ExpressionAttributeNames": {"#owner": "owner"},
            "ExpressionAttributeValues": {":owner": {"S": owner}},
        }

        try:
            self._client.delete_item(**delete_params)
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                # Check if lock exists and who owns it
                existing_lock = self.get_compaction_lock(lock_id, collection)
                if existing_lock is None:
                    raise EntityNotFoundError(
                        f"Lock '{lock_id}' for collection '{collection.value}' not found"
                    ) from e
                raise EntityValidationError(
                    f"Cannot delete lock '{lock_id}' for collection '{collection.value}' - "
                    f"owned by {existing_lock.owner}"
                ) from e
            raise

    @handle_dynamodb_errors("update_compaction_lock")
    def update_compaction_lock(self, lock: CompactionLock) -> None:
        """
        Updates a compaction lock (typically to refresh heartbeat).

        Args:
            lock: The CompactionLock to update

        Raises:
            EntityNotFoundError: If lock doesn't exist
            EntityValidationError: If lock is None or wrong type
        """
        if lock is None:
            raise EntityValidationError("lock cannot be None")
        if not isinstance(lock, CompactionLock):
            raise EntityValidationError("lock must be an instance of CompactionLock")

        # _update_entity does a PUT, which replaces the entire item
        # We use attribute_exists to ensure the lock still exists
        self._update_entity(
            lock,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_compaction_lock")
    def get_compaction_lock(self, lock_id: str, collection: "ChromaDBCollection") -> Optional[CompactionLock]:
        """
        Retrieves a compaction lock by ID and collection.

        Args:
            lock_id: The ID of the lock to retrieve
            collection: The ChromaDB collection this lock protects

        Returns:
            The CompactionLock if found, None otherwise
        """
        if not lock_id:
            raise EntityValidationError("lock_id cannot be empty")

        return self._get_entity(
            primary_key=f"LOCK#{collection.value}#{lock_id}",
            sort_key="LOCK",
            entity_class=CompactionLock,
            converter_func=item_to_compaction_lock,
        )

    @handle_dynamodb_errors("list_compaction_locks")
    def list_compaction_locks(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[CompactionLock], Optional[Dict[str, Any]]]:
        """
        Lists all compaction locks.

        Args:
            limit: Maximum number of locks to return
            last_evaluated_key: Pagination token

        Returns:
            Tuple of (locks, pagination_token)
        """
        return self._query_by_type(
            entity_type="COMPACTION_LOCK",
            converter_func=item_to_compaction_lock,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_active_compaction_locks")
    def list_active_compaction_locks(
        self,
        collection: Optional[ChromaDBCollection] = None,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[CompactionLock], Optional[Dict[str, Any]]]:
        """
        Lists all active (non-expired) compaction locks.

        Args:
            collection: Optional collection filter. If None, returns locks from all collections.
            limit: Maximum number of locks to return
            last_evaluated_key: Pagination token

        Returns:
            Tuple of (locks, pagination_token)
        """
        now = datetime.now(timezone.utc).isoformat()

        if collection is not None:
            # Query specific collection
            return self._query_entities(
                index_name="GSI1",
                key_condition_expression="GSI1PK = :pk AND GSI1SK > :sk",
                expression_attribute_names=None,  # No reserved keywords
                expression_attribute_values={
                    ":pk": {"S": f"LOCK#{collection.value}"},
                    ":sk": {"S": f"EXPIRES#{now}"},
                },
                converter_func=item_to_compaction_lock,
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            )
        else:
            # Query all collections and combine results
            all_locks = []
            for coll in ChromaDBCollection:
                locks, _ = self._query_entities(
                    index_name="GSI1",
                    key_condition_expression="GSI1PK = :pk AND GSI1SK > :sk",
                    expression_attribute_names=None,
                    expression_attribute_values={
                        ":pk": {"S": f"LOCK#{coll.value}"},
                        ":sk": {"S": f"EXPIRES#{now}"},
                    },
                    converter_func=item_to_compaction_lock,
                    limit=None,  # Get all for this collection
                )
                all_locks.extend(locks)
            
            # Apply limit if specified
            if limit is not None:
                all_locks = all_locks[:limit]
            
            return all_locks, None  # No pagination for combined results

    @handle_dynamodb_errors("cleanup_expired_locks")
    def cleanup_expired_locks(self) -> int:
        """
        Removes all expired locks from the table.

        Note: This is typically unnecessary as DynamoDB TTL will handle
        cleanup, but can be useful for immediate cleanup or testing.

        Returns:
            Number of locks removed
        """
        now = datetime.now(timezone.utc).isoformat()

        # Query for expired locks from all collections
        all_expired_locks = []
        for collection in ChromaDBCollection:
            expired_locks, _ = self._query_entities(
                index_name="GSI1",
                key_condition_expression="GSI1PK = :pk AND GSI1SK < :sk",
                expression_attribute_names=None,  # No reserved keywords
                expression_attribute_values={
                    ":pk": {"S": f"LOCK#{collection.value}"},
                    ":sk": {"S": f"EXPIRES#{now}"},
                },
                converter_func=item_to_compaction_lock,
                limit=None,  # Get all expired locks
            )
            all_expired_locks.extend(expired_locks)

        if not all_expired_locks:
            return 0

        # Batch delete expired locks
        delete_requests = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=lock.key)
            )
            for lock in all_expired_locks
        ]

        self._batch_write_with_retry(delete_requests)
        return len(all_expired_locks)

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
from receipt_dynamo.entities.compaction_lock import (
    CompactionLock,
    item_to_compaction_lock,
)

if TYPE_CHECKING:
    pass


class _CompactionLock(FlattenedStandardMixin):
    """Accessor methods for CompactionLock items in DynamoDB."""

    @handle_dynamodb_errors("acquire_compaction_lock")
    def acquire_compaction_lock(
        self, lock: CompactionLock, force: bool = False
    ) -> None:
        """
        Attempts to acquire a compaction lock.
        
        Args:
            lock: The CompactionLock to acquire
            force: If True, overwrites existing lock regardless of expiry
            
        Raises:
            EntityAlreadyExistsError: If lock is held by another owner and not expired
        """
        if lock is None:
            raise EntityValidationError("lock cannot be None")
        if not isinstance(lock, CompactionLock):
            raise EntityValidationError(
                "lock must be an instance of the CompactionLock class."
            )
        
        try:
            if force:
                # Force acquire - no condition
                self._put_entity(lock.to_item())
            else:
                # Conditional acquire - only if lock doesn't exist or is expired
                now = datetime.now(timezone.utc).isoformat()
                self._put_entity(
                    lock.to_item(),
                    condition_expression="attribute_not_exists(PK) OR expires < :now",
                    expression_attribute_values={":now": {"S": now}},
                )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise EntityAlreadyExistsError(
                    f"Lock '{lock.lock_id}' is held by another owner"
                ) from e
            raise

    @handle_dynamodb_errors("release_compaction_lock")
    def release_compaction_lock(self, lock_id: str, owner: str) -> None:
        """
        Releases a compaction lock if owned by the specified owner.
        
        Args:
            lock_id: The ID of the lock to release
            owner: The UUID of the owner releasing the lock
            
        Raises:
            EntityNotFoundError: If lock doesn't exist
            EntityValidationError: If owner doesn't match
        """
        if not lock_id:
            raise EntityValidationError("lock_id cannot be empty")
        if not owner:
            raise EntityValidationError("owner cannot be empty")
        
        try:
            # Delete only if we own the lock
            self._delete_entity_by_key(
                primary_key=f"LOCK#{lock_id}",
                sort_key="LOCK",
                condition_expression="owner = :owner",
                expression_attribute_values={":owner": {"S": owner}},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Check if lock exists and who owns it
                existing_lock = self.get_compaction_lock(lock_id)
                if existing_lock is None:
                    raise EntityNotFoundError(f"Lock '{lock_id}' not found") from e
                raise EntityValidationError(
                    f"Cannot release lock '{lock_id}' - owned by {existing_lock.owner}"
                ) from e
            raise

    @handle_dynamodb_errors("update_compaction_lock_heartbeat")
    def update_compaction_lock_heartbeat(
        self, lock_id: str, owner: str, new_heartbeat: Optional[datetime] = None
    ) -> None:
        """
        Updates the heartbeat timestamp for a lock.
        
        Args:
            lock_id: The ID of the lock to update
            owner: The UUID of the owner updating the heartbeat
            new_heartbeat: The new heartbeat timestamp (defaults to now)
            
        Raises:
            EntityNotFoundError: If lock doesn't exist
            EntityValidationError: If owner doesn't match
        """
        if not lock_id:
            raise EntityValidationError("lock_id cannot be empty")
        if not owner:
            raise EntityValidationError("owner cannot be empty")
        
        if new_heartbeat is None:
            new_heartbeat = datetime.now(timezone.utc)
        
        heartbeat_str = new_heartbeat.isoformat()
        
        try:
            self._update_entity_by_key(
                primary_key=f"LOCK#{lock_id}",
                sort_key="LOCK",
                update_expression="SET heartbeat = :heartbeat",
                condition_expression="owner = :owner",
                expression_attribute_values={
                    ":heartbeat": {"S": heartbeat_str},
                    ":owner": {"S": owner},
                },
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                existing_lock = self.get_compaction_lock(lock_id)
                if existing_lock is None:
                    raise EntityNotFoundError(f"Lock '{lock_id}' not found") from e
                raise EntityValidationError(
                    f"Cannot update heartbeat for lock '{lock_id}' - owned by {existing_lock.owner}"
                ) from e
            raise

    @handle_dynamodb_errors("get_compaction_lock")
    def get_compaction_lock(self, lock_id: str) -> Optional[CompactionLock]:
        """
        Retrieves a compaction lock by ID.
        
        Args:
            lock_id: The ID of the lock to retrieve
            
        Returns:
            The CompactionLock if found, None otherwise
        """
        if not lock_id:
            raise EntityValidationError("lock_id cannot be empty")
            
        return self._get_entity(
            primary_key=f"LOCK#{lock_id}",
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
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[CompactionLock], Optional[Dict[str, Any]]]:
        """
        Lists all active (non-expired) compaction locks.
        
        Args:
            limit: Maximum number of locks to return
            last_evaluated_key: Pagination token
            
        Returns:
            Tuple of (locks, pagination_token)
        """
        now = datetime.now(timezone.utc).isoformat()
        
        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk AND GSI1SK > :sk",
            expression_attribute_values={
                ":pk": {"S": "LOCK"},
                ":sk": {"S": f"EXPIRES#{now}"},
            },
            converter_func=item_to_compaction_lock,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("cleanup_expired_locks")
    def cleanup_expired_locks(self) -> int:
        """
        Removes all expired locks from the table.
        
        Note: This is typically unnecessary as DynamoDB TTL will handle cleanup,
        but can be useful for immediate cleanup or testing.
        
        Returns:
            Number of locks removed
        """
        now = datetime.now(timezone.utc).isoformat()
        
        # Query for expired locks
        expired_locks, _ = self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk AND GSI1SK < :sk",
            expression_attribute_values={
                ":pk": {"S": "LOCK"},
                ":sk": {"S": f"EXPIRES#{now}"},
            },
            converter_func=item_to_compaction_lock,
            limit=None,  # Get all expired locks
        )
        
        if not expired_locks:
            return 0
        
        # Batch delete expired locks
        delete_requests = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=lock.key)
            )
            for lock in expired_locks
        ]
        
        self._batch_write_with_retry(delete_requests)
        return len(expired_locks)
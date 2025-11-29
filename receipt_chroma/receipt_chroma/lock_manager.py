"""
Distributed lock manager with heartbeat support for DynamoDB.

This module provides a thread-safe lock manager that maintains distributed
locks in DynamoDB with automatic heartbeat updates for long-running tasks.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from botocore.exceptions import BotoCoreError, ClientError
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities.compaction_lock import CompactionLock

logger = logging.getLogger(__name__)

LOCK_EXCEPTIONS = (
    ClientError,
    BotoCoreError,
    ValueError,
    EntityAlreadyExistsError,
)


class LockManager:
    """
    Manages distributed locks with heartbeat support for long-running
    operations.

    This class provides:
    - Distributed lock acquisition via DynamoDB
    - Automatic heartbeat updates to extend lock duration
    - Thread-safe operation management
    - Configurable timeouts and intervals

    Example:
        ```python
        lock_manager = LockManager(dynamo_client, collection)

        if lock_manager.acquire("my-operation"):
            try:
                lock_manager.start_heartbeat()
                # Do long-running work here
                perform_operation()
            finally:
                lock_manager.stop_heartbeat()
                lock_manager.release()
        ```

    Attributes:
        dynamo_client: DynamoDB client for lock operations
        collection: ChromaDB collection this lock protects
        heartbeat_interval: Seconds between heartbeat updates
        lock_duration_minutes: Initial lock duration in minutes
    """

    def __init__(
        self,
        dynamo_client: DynamoClient,
        collection: ChromaDBCollection,
        *,
        heartbeat_interval: int = 60,
        lock_duration_minutes: int = 5,
        max_heartbeat_failures: int = 3,
    ) -> None:
        """
        Initialize the lock manager.

        Args:
            dynamo_client: DynamoDB client for lock operations
            collection: ChromaDB collection this lock protects (lines or words)
            heartbeat_interval: Seconds between heartbeat updates
            lock_duration_minutes: Initial lock duration in minutes
            max_heartbeat_failures: Consecutive heartbeat failures allowed
        """
        self.dynamo_client = dynamo_client
        self.collection = collection
        self.heartbeat_interval = heartbeat_interval
        self.lock_duration_minutes = lock_duration_minutes
        self.max_heartbeat_failures = max_heartbeat_failures

        # Lock state
        self.lock_id: Optional[str] = None
        self.lock_owner: Optional[str] = None

        # Heartbeat thread management
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.stop_heartbeat_event = threading.Event()

        # Heartbeat failure tracking
        self.consecutive_heartbeat_failures = 0

        # Thread safety (using RLock to allow recursive acquisition)
        self._lock = threading.RLock()

    @staticmethod
    def _parse_expiration_timestamp(expires: Any) -> Optional[datetime]:
        """
        Convert a CompactionLock expires value into a datetime object.

        Returns:
            Parsed datetime or None if parsing fails.
        """
        if isinstance(expires, datetime):
            return expires
        if isinstance(expires, str):
            try:
                return datetime.fromisoformat(expires.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def acquire(self, lock_id: str = "chromadb_compaction_lock") -> bool:
        """
        Acquire a distributed lock.

        Args:
            lock_id: Identifier for the lock (default:
                "chromadb_compaction_lock")

        Returns:
            True if lock was acquired, False otherwise
        """
        with self._lock:
            if self.lock_id is not None:
                logger.warning(
                    "Cannot acquire lock %s - already holding lock %s",
                    lock_id,
                    self.lock_id,
                )
                return False

            owner = str(uuid.uuid4())

            try:
                lock = CompactionLock(
                    lock_id=lock_id,
                    owner=owner,
                    expires=datetime.now(timezone.utc)
                    + timedelta(minutes=self.lock_duration_minutes),
                    collection=self.collection,
                    heartbeat=datetime.now(timezone.utc),
                )

                self.dynamo_client.add_compaction_lock(lock)

                logger.info(
                    "Acquired lock: %s with owner %s (duration: %d min)",
                    lock_id,
                    owner,
                    self.lock_duration_minutes,
                )

                self.lock_id = lock_id
                self.lock_owner = owner
                return True

            except LOCK_EXCEPTIONS as exc:
                logger.info("Failed to acquire lock %s: %s", lock_id, exc)
                return False

    def release(self) -> None:
        """
        Release the currently held lock.

        This method is idempotent and safe to call multiple times.
        """
        with self._lock:
            if not self.lock_id or not self.lock_owner:
                logger.debug("No lock to release")
                return

            try:
                self.dynamo_client.delete_compaction_lock(
                    self.lock_id, self.lock_owner, self.collection
                )
                logger.info("Released lock: %s", self.lock_id)

            except LOCK_EXCEPTIONS as exc:
                logger.exception("Error releasing lock %s", self.lock_id)
            finally:
                # Clear state even if delete fails
                self.lock_id = None
                self.lock_owner = None

    def start_heartbeat(self) -> None:
        """
        Start the heartbeat thread to keep the lock alive.

        The heartbeat thread will periodically update the lock's expiration
        time to prevent it from timing out during long operations.
        """
        with self._lock:
            if not self.lock_id or not self.lock_owner:
                logger.warning("Cannot start heartbeat - no lock held")
                return

            if self.heartbeat_thread and self.heartbeat_thread.is_alive():
                logger.warning("Heartbeat thread already running")
                return

            self.stop_heartbeat_event.clear()
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_worker,
                name=f"heartbeat-{self.lock_id}",
                daemon=True,
            )
            self.heartbeat_thread.start()

            logger.info(
                "Started heartbeat thread for lock %s (interval: %ds)",
                self.lock_id,
                self.heartbeat_interval,
            )

    def stop_heartbeat(self) -> None:
        """
        Stop the heartbeat thread.

        This method will signal the heartbeat thread to stop and wait
        for it to terminate gracefully.
        """
        if not self.heartbeat_thread:
            return

        logger.info("Stopping heartbeat thread")
        self.stop_heartbeat_event.set()

        # Wait for thread to stop (max 2 seconds)
        self.heartbeat_thread.join(timeout=2.0)

        if self.heartbeat_thread.is_alive():
            logger.warning("Heartbeat thread did not stop gracefully")
        else:
            logger.info("Heartbeat thread stopped successfully")

        self.heartbeat_thread = None

    def update_heartbeat(self) -> bool:
        """
        Manually update the heartbeat for the current lock.

        This can be called directly if you want to update the heartbeat
        outside of the automatic thread.

        Returns:
            True if heartbeat was updated, False otherwise
        """
        with self._lock:
            if not self.lock_id or not self.lock_owner:
                logger.warning("Cannot update heartbeat - no lock held")
                return False

            try:
                # Create updated lock with extended expiration
                updated_lock = CompactionLock(
                    lock_id=self.lock_id,
                    owner=self.lock_owner,
                    expires=datetime.now(timezone.utc)
                    + timedelta(minutes=self.lock_duration_minutes),
                    collection=self.collection,
                    heartbeat=datetime.now(timezone.utc),
                )

                self.dynamo_client.update_compaction_lock(updated_lock)

                logger.debug(
                    "Updated heartbeat for lock %s at %s",
                    self.lock_id,
                    datetime.now(timezone.utc).isoformat(),
                )
                return True

            except LOCK_EXCEPTIONS as exc:
                logger.error(
                    "Failed to update heartbeat for lock %s: %s",
                    self.lock_id,
                    exc,
                )
                return False

    def _heartbeat_worker(self) -> None:
        """
        Worker thread that updates the lock heartbeat periodically.

        This method runs in a separate thread and updates the heartbeat
        at regular intervals until stop_heartbeat() is called.
        """
        logger.info("Heartbeat worker thread started")

        while not self.stop_heartbeat_event.is_set():
            # Update heartbeat
            if self.update_heartbeat():
                # Reset failure counter on successful heartbeat
                with self._lock:
                    self.consecutive_heartbeat_failures = 0
            else:
                with self._lock:
                    self.consecutive_heartbeat_failures += 1
                    logger.error(
                        "Heartbeat update failed (attempt %d/%d); lock risk",
                        self.consecutive_heartbeat_failures,
                        self.max_heartbeat_failures,
                    )

                    # Auto-release lock after too many failures
                    if (
                        self.consecutive_heartbeat_failures
                        >= self.max_heartbeat_failures
                    ):
                        logger.critical(
                            "Max heartbeat failures (%d); releasing %s",
                            self.max_heartbeat_failures,
                            self.lock_id,
                        )

                        # Stop heartbeat thread and clear lock state
                        self.stop_heartbeat_event.set()
                        self.lock_id = None
                        self.lock_owner = None
                        break

            # Wait for next interval or stop signal
            if self.stop_heartbeat_event.wait(self.heartbeat_interval):
                break  # Stop signal received

        logger.info("Heartbeat worker thread stopped")

    def is_locked(self) -> bool:
        """
        Check if this manager currently holds a lock.

        Returns:
            True if a lock is held, False otherwise
        """
        with self._lock:
            return self.lock_id is not None

    def get_lock_info(self) -> Optional[dict]:
        """
        Get information about the currently held lock.

        Returns:
            Dictionary with lock information or None if no lock is held
        """
        with self._lock:
            if not self.lock_id:
                return None

            return {
                "lock_id": self.lock_id,
                "owner": self.lock_owner,
                "heartbeat_interval": self.heartbeat_interval,
                "lock_duration_minutes": self.lock_duration_minutes,
                "heartbeat_active": bool(
                    self.heartbeat_thread and self.heartbeat_thread.is_alive()
                ),
            }

    def validate_ownership(self) -> bool:
        """
        Verify that we still own the current lock.

        This method checks with DynamoDB to ensure the lock hasn't expired
        or been acquired by another process.

        Returns:
            True if we still own the lock, False otherwise
        """
        with self._lock:
            if not self.lock_id or not self.lock_owner:
                logger.warning("Cannot validate ownership - no lock held")
                return False

            try:
                current_lock = self.dynamo_client.get_compaction_lock(
                    self.lock_id, self.collection
                )
            except LOCK_EXCEPTIONS as exc:
                logger.exception(
                    "Failed to validate ownership for lock %s", self.lock_id
                )
                return False

            is_valid = True
            if current_lock is None:
                logger.warning("Lock %s no longer exists", self.lock_id)
                is_valid = False
            elif current_lock.owner != self.lock_owner:
                logger.warning(
                    "Lock %s ownership mismatch: expected %s, found %s",
                    self.lock_id,
                    self.lock_owner,
                    current_lock.owner,
                )
                is_valid = False
            else:
                expires_dt = self._parse_expiration_timestamp(
                    current_lock.expires
                )
                if expires_dt is None:
                    logger.error(
                        "Invalid expiration value for lock %s: %s",
                        self.lock_id,
                        current_lock.expires,
                    )
                    is_valid = False
                else:
                    now = datetime.now(timezone.utc)
                    if expires_dt <= now:
                        logger.warning(
                            "Lock %s has expired: %s <= %s",
                            self.lock_id,
                            expires_dt.isoformat(),
                            now.isoformat(),
                        )
                        is_valid = False

            if is_valid:
                logger.debug("Lock ownership validated for %s", self.lock_id)
            return is_valid

    def get_remaining_time(self) -> Optional[timedelta]:
        """
        Get the remaining time before the lock expires.

        Returns:
            Timedelta representing remaining time, or None if no lock is held
            or an error occurs.
        """
        with self._lock:
            if not self.lock_id or not self.lock_owner:
                return None

            try:
                current_lock = self.dynamo_client.get_compaction_lock(
                    self.lock_id, self.collection
                )

                if (
                    current_lock is None
                    or current_lock.owner != self.lock_owner
                ):
                    return None

                expires_dt = self._parse_expiration_timestamp(
                    current_lock.expires
                )
                if expires_dt is None:
                    logger.error(
                        "Invalid expiration value for lock %s: %s",
                        self.lock_id,
                        current_lock.expires,
                    )
                    return None

                remaining = expires_dt - datetime.now(timezone.utc)
                return (
                    remaining
                    if remaining.total_seconds() > 0
                    else timedelta(0)
                )

            except LOCK_EXCEPTIONS as exc:
                logger.error(
                    "Failed to get remaining time for lock %s: %s",
                    self.lock_id,
                    exc,
                )
                return None

    def refresh_lock(self) -> bool:
        """
        Atomically refresh the lock with ownership validation.

        This is safer than update_heartbeat() as it includes ownership checks.

        Returns:
            True if lock was refreshed, False otherwise
        """
        with self._lock:
            if not self.lock_id or not self.lock_owner:
                logger.warning("Cannot refresh lock - no lock held")
                return False

            try:
                # First validate we still own it
                if not self.validate_ownership():
                    logger.error(
                        "Cannot refresh lock - ownership validation failed"
                    )
                    return False

                # Create updated lock with extended expiration
                updated_lock = CompactionLock(
                    lock_id=self.lock_id,
                    owner=self.lock_owner,
                    expires=datetime.now(timezone.utc)
                    + timedelta(minutes=self.lock_duration_minutes),
                    collection=self.collection,
                    heartbeat=datetime.now(timezone.utc),
                )

                self.dynamo_client.update_compaction_lock(updated_lock)

                logger.info("Successfully refreshed lock %s", self.lock_id)
                return True

            except LOCK_EXCEPTIONS as exc:
                logger.error(
                    "Failed to refresh lock %s: %s",
                    self.lock_id,
                    exc,
                )
                return False

    def __enter__(self) -> "LockManager":
        """Context manager entry - acquire lock."""
        default_lock_id = "context_managed_lock"
        if not self.acquire(default_lock_id):
            raise RuntimeError(f"Failed to acquire lock: {default_lock_id}")
        self.start_heartbeat()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> Literal[False]:
        """Context manager exit - release lock."""
        self.stop_heartbeat()
        self.release()
        return False  # Don't suppress exceptions

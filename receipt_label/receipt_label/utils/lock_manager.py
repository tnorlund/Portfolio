"""
Distributed lock manager with heartbeat support for DynamoDB.

This module provides a thread-safe lock manager that maintains distributed locks
in DynamoDB with automatic heartbeat updates to prevent timeout during long-running
operations.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_lock import CompactionLock

logger = logging.getLogger(__name__)


class LockManager:
    """
    Manages distributed locks with heartbeat support for long-running operations.
    
    This class provides:
    - Distributed lock acquisition via DynamoDB
    - Automatic heartbeat updates to extend lock duration
    - Thread-safe operation management
    - Configurable timeouts and intervals
    
    Example:
        ```python
        lock_manager = LockManager(dynamo_client)
        
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
        heartbeat_interval: Seconds between heartbeat updates
        lock_duration_minutes: Initial lock duration in minutes
    """
    
    def __init__(
        self,
        dynamo_client: DynamoClient,
        heartbeat_interval: int = 60,
        lock_duration_minutes: int = 5,
    ) -> None:
        """
        Initialize the lock manager.
        
        Args:
            dynamo_client: DynamoDB client for lock operations
            heartbeat_interval: Seconds between heartbeat updates (default: 60)
            lock_duration_minutes: Initial lock duration in minutes (default: 5)
        """
        self.dynamo_client = dynamo_client
        self.heartbeat_interval = heartbeat_interval
        self.lock_duration_minutes = lock_duration_minutes
        
        # Lock state
        self.lock_id: Optional[str] = None
        self.lock_owner: Optional[str] = None
        
        # Heartbeat thread management
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.stop_heartbeat_event = threading.Event()
        
        # Thread safety
        self._lock = threading.Lock()
    
    def acquire(self, lock_id: str = "chromadb_compaction_lock") -> bool:
        """
        Acquire a distributed lock.
        
        Args:
            lock_id: Identifier for the lock (default: "chromadb_compaction_lock")
            
        Returns:
            True if lock was acquired, False otherwise
        """
        with self._lock:
            if self.lock_id is not None:
                logger.warning(
                    "Cannot acquire lock %s - already holding lock %s",
                    lock_id, self.lock_id
                )
                return False
            
            owner = str(uuid.uuid4())
            
            try:
                lock = CompactionLock(
                    lock_id=lock_id,
                    owner=owner,
                    expires=datetime.utcnow() + timedelta(minutes=self.lock_duration_minutes),
                    heartbeat=datetime.utcnow(),
                )
                
                self.dynamo_client.add_compaction_lock(lock)
                
                logger.info(
                    "Acquired lock: %s with owner %s (duration: %d min)",
                    lock_id, owner, self.lock_duration_minutes
                )
                
                self.lock_id = lock_id
                self.lock_owner = owner
                return True
                
            except Exception as e:
                logger.info(
                    "Failed to acquire lock %s: %s",
                    lock_id, str(e)
                )
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
                    self.lock_id, 
                    self.lock_owner
                )
                logger.info("Released lock: %s", self.lock_id)
                
            except Exception as e:
                logger.error(
                    "Error releasing lock %s: %s",
                    self.lock_id, str(e)
                )
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
                daemon=True
            )
            self.heartbeat_thread.start()
            
            logger.info(
                "Started heartbeat thread for lock %s (interval: %ds)",
                self.lock_id, self.heartbeat_interval
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
                updated_lock = CompactionLock(
                    lock_id=self.lock_id,
                    owner=self.lock_owner,
                    expires=datetime.utcnow() + timedelta(minutes=self.lock_duration_minutes),
                    heartbeat=datetime.utcnow(),
                )
                
                self.dynamo_client.update_compaction_lock(updated_lock)
                
                logger.debug(
                    "Updated heartbeat for lock %s at %s",
                    self.lock_id,
                    datetime.utcnow().isoformat()
                )
                return True
                
            except Exception as e:
                logger.error(
                    "Failed to update heartbeat for lock %s: %s",
                    self.lock_id, str(e)
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
            if not self.update_heartbeat():
                logger.error("Heartbeat update failed - lock may be lost")
            
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
    
    def __enter__(self):
        """Context manager entry - acquire lock."""
        default_lock_id = "context_managed_lock"
        if not self.acquire(default_lock_id):
            raise RuntimeError(f"Failed to acquire lock: {default_lock_id}")
        self.start_heartbeat()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release lock."""
        self.stop_heartbeat()
        self.release()
        return False  # Don't suppress exceptions
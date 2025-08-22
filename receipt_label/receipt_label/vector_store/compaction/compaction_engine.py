"""
Main compaction engine for vector store data.

This module provides the high-level compaction engine that orchestrates
the entire compaction process: acquiring locks, processing deltas,
creating snapshots, and managing the compaction lifecycle.
"""

import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..client.factory import VectorClient
from ..storage.snapshot_manager import SnapshotManager
from .delta_processor import DeltaProcessor

# Import lock manager with fallback
try:
    from receipt_label.utils.lock_manager import LockManager

    LOCK_MANAGER_AVAILABLE = True
except ImportError:
    LOCK_MANAGER_AVAILABLE = False
    LockManager = None

logger = logging.getLogger(__name__)


class CompactionEngine:
    """
    High-level engine for vector store compaction operations.

    This class orchestrates the complete compaction process:
    1. Acquire distributed lock
    2. Download current snapshot (if exists)
    3. Process delta files
    4. Create new snapshot
    5. Upload to S3
    6. Cleanup old deltas
    7. Release lock
    """

    def __init__(
        self,
        bucket_name: str,
        collection_name: str,
        database_name: Optional[str] = None,
        snapshot_prefix: str = "snapshots/",
        delta_prefix: str = "deltas/",
        lock_manager: Optional[Any] = None,
        s3_client: Optional[Any] = None,
    ):
        """
        Initialize the compaction engine.

        Args:
            bucket_name: S3 bucket for vector store data
            collection_name: Name of the collection to compact
            database_name: Optional database name for organization
            snapshot_prefix: S3 prefix for snapshots (default: "snapshots/")
            delta_prefix: S3 prefix for deltas (default: "deltas/")
            lock_manager: Optional lock manager instance
            s3_client: Optional boto3 S3 client
        """
        self.bucket_name = bucket_name
        self.collection_name = collection_name
        self.database_name = database_name
        self.snapshot_prefix = snapshot_prefix
        self.delta_prefix = delta_prefix

        # Initialize components
        self.snapshot_manager = SnapshotManager(
            bucket_name=bucket_name,
            s3_prefix=snapshot_prefix,
            s3_client=s3_client,
        )
        self.delta_processor = DeltaProcessor(
            bucket_name=bucket_name,
            delta_prefix=delta_prefix,
            s3_client=s3_client,
        )
        self.lock_manager = lock_manager

        # Compaction state
        self._lock_acquired = False
        self._lock_id = None
        self._temp_directories = []

    def run_compaction(
        self,
        max_deltas: Optional[int] = None,
        force_snapshot: bool = False,
        cleanup_on_failure: bool = True,
        lock_timeout_seconds: int = 3600,  # 1 hour default
    ) -> Dict[str, Any]:
        """
        Run the complete compaction process.

        Args:
            max_deltas: Optional limit on number of deltas to process
            force_snapshot: Whether to create snapshot even if no deltas processed
            cleanup_on_failure: Whether to cleanup temp directories on failure
            lock_timeout_seconds: Timeout for the distributed lock

        Returns:
            Dict with comprehensive compaction results

        Raises:
            RuntimeError: If compaction fails at any stage
        """
        start_time = time.time()

        logger.info(
            "Starting compaction for collection '%s' (database: %s)",
            self.collection_name,
            self.database_name,
        )

        try:
            # Step 1: Acquire distributed lock
            lock_result = self._acquire_compaction_lock(lock_timeout_seconds)

            # Step 2: List available deltas
            delta_list = self.delta_processor.list_delta_files(
                collection_name=self.collection_name,
                database_name=self.database_name,
                limit=max_deltas,
            )

            # Step 3: Download current snapshot (if exists)
            snapshot_restore_result = self._restore_current_snapshot()

            # Step 4: Process deltas
            delta_processing_result = None
            if delta_list or force_snapshot:
                delta_processing_result = self._process_deltas(
                    delta_list, snapshot_restore_result["client"]
                )
            else:
                logger.info(
                    "No deltas to process and force_snapshot=False, skipping compaction"
                )
                return self._build_result(
                    status="skipped",
                    reason="no_deltas_and_no_force",
                    start_time=start_time,
                    lock_result=lock_result,
                    snapshot_restore_result=snapshot_restore_result,
                )

            # Step 5: Create and upload new snapshot
            snapshot_creation_result = self._create_new_snapshot(
                snapshot_restore_result["client"],
                snapshot_restore_result["local_directory"],
            )

            # Step 6: Cleanup processed deltas
            delta_cleanup_result = None
            if delta_processing_result and delta_processing_result.get(
                "processed_deltas"
            ):
                processed_prefixes = [
                    delta["delta_prefix"]
                    for delta in delta_processing_result["processed_deltas"]
                ]
                delta_cleanup_result = (
                    self.delta_processor.delete_processed_deltas(
                        processed_prefixes
                    )
                )

            # Step 7: Release lock
            self._release_compaction_lock()

            # Build final result
            result = self._build_result(
                status="success",
                start_time=start_time,
                lock_result=lock_result,
                snapshot_restore_result=snapshot_restore_result,
                delta_processing_result=delta_processing_result,
                snapshot_creation_result=snapshot_creation_result,
                delta_cleanup_result=delta_cleanup_result,
                delta_list=delta_list,
            )

            logger.info(
                "Compaction completed successfully: %d deltas processed, %d items merged",
                result.get("deltas_processed", 0),
                result.get("total_items_merged", 0),
            )

            return result

        except Exception as e:
            logger.error("Compaction failed: %s", e)

            # Cleanup on failure
            if cleanup_on_failure:
                self._cleanup_temp_directories()

            # Always try to release lock
            try:
                self._release_compaction_lock()
            except Exception as lock_e:
                logger.warning(
                    "Failed to release lock during error cleanup: %s", lock_e
                )

            # Build error result
            error_result = self._build_result(
                status="failed",
                error=str(e),
                start_time=start_time,
            )

            raise RuntimeError(f"Compaction failed: {e}") from e

        finally:
            # Always cleanup temp directories
            self._cleanup_temp_directories()

    def _acquire_compaction_lock(self, timeout_seconds: int) -> Dict[str, Any]:
        """Acquire distributed lock for compaction."""
        if not LOCK_MANAGER_AVAILABLE or not self.lock_manager:
            logger.warning(
                "Lock manager not available, proceeding without locking"
            )
            return {"status": "no_lock_manager", "acquired": False}

        try:
            # Create lock ID based on collection and database
            if self.database_name:
                lock_id = (
                    f"compaction-{self.database_name}-{self.collection_name}"
                )
            else:
                lock_id = f"compaction-{self.collection_name}"

            logger.info("Acquiring compaction lock: %s", lock_id)

            success = self.lock_manager.acquire_lock(
                lock_id=lock_id,
                timeout_seconds=timeout_seconds,
            )

            if success:
                self._lock_acquired = True
                self._lock_id = lock_id
                logger.info(
                    "Successfully acquired compaction lock: %s", lock_id
                )
                return {
                    "status": "acquired",
                    "lock_id": lock_id,
                    "timeout_seconds": timeout_seconds,
                    "acquired": True,
                }
            else:
                raise RuntimeError(
                    f"Failed to acquire compaction lock: {lock_id}"
                )

        except Exception as e:
            logger.error("Failed to acquire compaction lock: %s", e)
            raise RuntimeError(f"Lock acquisition failed: {e}")

    def _release_compaction_lock(self) -> None:
        """Release the compaction lock if acquired."""
        if not self._lock_acquired or not self._lock_id:
            return

        if not LOCK_MANAGER_AVAILABLE or not self.lock_manager:
            return

        try:
            logger.info("Releasing compaction lock: %s", self._lock_id)
            self.lock_manager.release_lock(self._lock_id)
            self._lock_acquired = False
            self._lock_id = None
            logger.info("Successfully released compaction lock")
        except Exception as e:
            logger.error("Failed to release compaction lock: %s", e)

    def _restore_current_snapshot(self) -> Dict[str, Any]:
        """Download and restore the current snapshot if it exists."""
        logger.info(
            "Restoring current snapshot for collection '%s'",
            self.collection_name,
        )

        # Create temporary directory for snapshot
        snapshot_temp_dir = tempfile.mkdtemp(
            prefix=f"snapshot_{self.collection_name}_"
        )
        self._temp_directories.append(snapshot_temp_dir)

        try:
            # Try to restore existing snapshot
            restore_result = self.snapshot_manager.restore_snapshot(
                collection_name=self.collection_name,
                local_directory=snapshot_temp_dir,
                database_name=self.database_name,
                download_from_s3=True,
                verify_hash=True,
                create_client=True,
                client_mode="write",  # Need write mode for merging deltas
            )

            logger.info(
                "Successfully restored snapshot: %d items in collection",
                restore_result.get("collection_count", 0),
            )

            return restore_result

        except Exception as e:
            logger.info("No existing snapshot found or restore failed: %s", e)
            logger.info("Creating new empty collection")

            # Create new empty client for this collection
            client = VectorClient.create_chromadb_client(
                persist_directory=snapshot_temp_dir,
                mode="write",
            )

            # Create the collection (will be empty)
            collection = client.get_collection(
                self.collection_name,
                create_if_missing=True,
                metadata={
                    "created_by": "compaction_engine",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            return {
                "status": "new_collection",
                "collection_name": self.collection_name,
                "database_name": self.database_name,
                "local_directory": snapshot_temp_dir,
                "collection_count": 0,
                "client": client,
                "is_new": True,
            }

    def _process_deltas(
        self, delta_list: List[Dict[str, Any]], target_client: Any
    ) -> Dict[str, Any]:
        """Process all available deltas."""
        if not delta_list:
            logger.info("No deltas to process")
            return {
                "status": "no_deltas",
                "deltas_processed": 0,
                "total_items_merged": 0,
            }

        logger.info("Processing %d deltas", len(delta_list))

        return self.delta_processor.process_delta_batch(
            delta_list=delta_list,
            target_client=target_client,
            collection_name=self.collection_name,
            cleanup_temp_dirs=True,
        )

    def _create_new_snapshot(
        self, client: Any, local_directory: str
    ) -> Dict[str, Any]:
        """Create and upload a new snapshot."""
        logger.info(
            "Creating new snapshot for collection '%s'", self.collection_name
        )

        return self.snapshot_manager.create_snapshot(
            vector_client=client,
            collection_name=self.collection_name,
            local_directory=local_directory,
            database_name=self.database_name,
            upload_to_s3=True,
            include_metadata={
                "compaction_timestamp": datetime.now(timezone.utc).isoformat(),
                "compacted_by": "compaction_engine",
            },
        )

    def _cleanup_temp_directories(self) -> None:
        """Clean up all temporary directories created during compaction."""
        for temp_dir in self._temp_directories:
            try:
                if os.path.exists(temp_dir):
                    import shutil

                    shutil.rmtree(temp_dir)
                    logger.debug("Cleaned up temp directory: %s", temp_dir)
            except Exception as e:
                logger.warning(
                    "Failed to cleanup temp directory %s: %s", temp_dir, e
                )

        self._temp_directories.clear()

    def _build_result(
        self,
        status: str,
        start_time: float,
        error: Optional[str] = None,
        reason: Optional[str] = None,
        lock_result: Optional[Dict[str, Any]] = None,
        snapshot_restore_result: Optional[Dict[str, Any]] = None,
        delta_processing_result: Optional[Dict[str, Any]] = None,
        snapshot_creation_result: Optional[Dict[str, Any]] = None,
        delta_cleanup_result: Optional[Dict[str, Any]] = None,
        delta_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build a comprehensive result dictionary."""
        end_time = time.time()

        result = {
            "status": status,
            "collection_name": self.collection_name,
            "database_name": self.database_name,
            "bucket_name": self.bucket_name,
            "start_time": datetime.fromtimestamp(
                start_time, tz=timezone.utc
            ).isoformat(),
            "end_time": datetime.fromtimestamp(
                end_time, tz=timezone.utc
            ).isoformat(),
            "duration_seconds": end_time - start_time,
        }

        if error:
            result["error"] = error
        if reason:
            result["reason"] = reason

        # Add component results
        if lock_result:
            result["lock"] = lock_result
        if snapshot_restore_result:
            result["snapshot_restore"] = snapshot_restore_result
        if delta_processing_result:
            result["delta_processing"] = delta_processing_result
            # Extract summary metrics
            result["deltas_processed"] = delta_processing_result.get(
                "deltas_processed", 0
            )
            result["total_items_merged"] = delta_processing_result.get(
                "total_items_merged", 0
            )
            result["failed_deltas"] = delta_processing_result.get(
                "failed_deltas", 0
            )
        if snapshot_creation_result:
            result["snapshot_creation"] = snapshot_creation_result
        if delta_cleanup_result:
            result["delta_cleanup"] = delta_cleanup_result
        if delta_list:
            result["available_deltas"] = len(delta_list)

        return result

    # Additional utility methods

    def get_collection_status(self) -> Dict[str, Any]:
        """Get current status of the collection (snapshot and deltas)."""
        try:
            # List current snapshots
            snapshots = self.snapshot_manager.list_snapshots(
                collection_name=self.collection_name,
                database_name=self.database_name,
            )

            # List current deltas
            deltas = self.delta_processor.list_delta_files(
                collection_name=self.collection_name,
                database_name=self.database_name,
            )

            return {
                "status": "success",
                "collection_name": self.collection_name,
                "database_name": self.database_name,
                "snapshots_available": len(snapshots),
                "deltas_pending": len(deltas),
                "snapshots": snapshots,
                "deltas": deltas,
            }

        except Exception as e:
            logger.error("Failed to get collection status: %s", e)
            return {
                "status": "error",
                "error": str(e),
                "collection_name": self.collection_name,
                "database_name": self.database_name,
            }

    def estimate_compaction_size(self) -> Dict[str, Any]:
        """Estimate the size and impact of running compaction."""
        try:
            deltas = self.delta_processor.list_delta_files(
                collection_name=self.collection_name,
                database_name=self.database_name,
            )

            if not deltas:
                return {
                    "status": "no_compaction_needed",
                    "deltas_pending": 0,
                    "estimated_size_bytes": 0,
                }

            total_size = sum(delta.get("total_size", 0) for delta in deltas)
            total_files = sum(len(delta.get("files", [])) for delta in deltas)

            return {
                "status": "compaction_recommended",
                "deltas_pending": len(deltas),
                "total_delta_size_bytes": total_size,
                "total_delta_files": total_files,
                "estimated_processing_time_minutes": len(deltas)
                * 2,  # Rough estimate
            }

        except Exception as e:
            logger.error("Failed to estimate compaction size: %s", e)
            return {
                "status": "error",
                "error": str(e),
            }

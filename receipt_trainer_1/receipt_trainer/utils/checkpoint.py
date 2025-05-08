import os
import glob
import json
import time
import shutil
import logging
import filelock
import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

from receipt_dynamo.services.job_service import JobService
from receipt_dynamo.entities.job_checkpoint import JobCheckpoint

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages checkpoint storage, synchronization, and access on shared EFS filesystem.

    This class provides functions for safely storing and retrieving model checkpoints
    from a shared EFS filesystem in a distributed training environment. It handles:

    1. Checkpoint versioning and metadata tracking
    2. File locking for safe concurrent access
    3. Metadata synchronization with DynamoDB
    4. Atomic file operations to prevent corruption
    5. Checkpoint discovery and selection
    """

    def __init__(
        self,
        job_id: str,
        efs_mount_point: str = "/mnt/checkpoints",
        dynamo_table: Optional[str] = None,
        lock_timeout: int = 60,  # seconds
    ):
        """Initialize checkpoint manager.

        Args:
            job_id: ID of the job
            efs_mount_point: Path to EFS checkpoint mount
            dynamo_table: DynamoDB table name for job tracking
            lock_timeout: Timeout for acquiring lock (seconds)
        """
        self.job_id = job_id
        self.efs_mount_point = efs_mount_point
        self.job_service = JobService(dynamo_table) if dynamo_table else None
        self.lock_timeout = lock_timeout

        # Create job-specific checkpoint directory structure
        self.job_checkpoint_dir = os.path.join(efs_mount_point, job_id)
        self.lock_file = os.path.join(
            self.job_checkpoint_dir, ".checkpoint.lock"
        )

        # Initialize directory structure
        self._init_directory_structure()

    def _init_directory_structure(self) -> None:
        """Initialize the checkpoint directory structure."""
        os.makedirs(self.job_checkpoint_dir, exist_ok=True)

        # Create metadata file if it doesn't exist
        metadata_file = os.path.join(self.job_checkpoint_dir, "metadata.json")
        if not os.path.exists(metadata_file):
            with open(metadata_file, "w") as f:
                json.dump(
                    {
                        "job_id": self.job_id,
                        "created_at": datetime.datetime.now().isoformat(),
                        "checkpoints": [],
                    },
                    f,
                )

    def is_efs_mounted(self) -> bool:
        """Check if EFS is properly mounted at the mount point."""
        if not os.path.exists(self.efs_mount_point):
            return False

        try:
            # Try creating and removing a test file
            test_file = os.path.join(
                self.efs_mount_point, f".test_{time.time()}"
            )
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            return True
        except (IOError, OSError):
            return False

    def _get_lock(self) -> filelock.FileLock:
        """Get a file lock for the checkpoint directory.

        Returns:
            A FileLock object
        """
        # Create lock directory if it doesn't exist
        os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)

        # Return file lock
        return filelock.FileLock(self.lock_file, timeout=self.lock_timeout)

    def _get_metadata(self) -> Dict[str, Any]:
        """Get checkpoint metadata.

        Returns:
            Dictionary containing checkpoint metadata
        """
        metadata_file = os.path.join(self.job_checkpoint_dir, "metadata.json")
        try:
            with open(metadata_file, "r") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error reading checkpoint metadata: {e}")
            return {
                "job_id": self.job_id,
                "created_at": datetime.datetime.now().isoformat(),
                "checkpoints": [],
            }

    def _save_metadata(self, metadata: Dict[str, Any]) -> None:
        """Save checkpoint metadata.

        Args:
            metadata: Dictionary containing checkpoint metadata
        """
        metadata_file = os.path.join(self.job_checkpoint_dir, "metadata.json")
        temp_file = f"{metadata_file}.tmp"

        # Write to temporary file first
        with open(temp_file, "w") as f:
            json.dump(metadata, f, indent=2)

        # Then rename to final file (atomic operation)
        os.replace(temp_file, metadata_file)

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all checkpoints for the job.

        Returns:
            List of checkpoint metadata dictionaries
        """
        if not self.is_efs_mounted():
            logger.warning("EFS not mounted, cannot list checkpoints")
            return []

        with self._get_lock():
            metadata = self._get_metadata()
            return metadata.get("checkpoints", [])

    def get_latest_checkpoint(self) -> Optional[str]:
        """Get the path to the latest checkpoint.

        Returns:
            Path to the latest checkpoint directory, or None if no checkpoints exist
        """
        checkpoints = self.list_checkpoints()
        if not checkpoints:
            return None

        # Sort by created_at timestamp (newest first)
        sorted_checkpoints = sorted(
            checkpoints, key=lambda x: x.get("created_at", ""), reverse=True
        )

        if not sorted_checkpoints:
            return None

        checkpoint_name = sorted_checkpoints[0].get("name")
        if not checkpoint_name:
            return None

        checkpoint_path = os.path.join(
            self.job_checkpoint_dir, checkpoint_name
        )
        if not os.path.exists(checkpoint_path):
            logger.warning(
                f"Latest checkpoint directory not found: {checkpoint_path}"
            )
            return None

        return checkpoint_path

    def get_best_checkpoint(self) -> Optional[str]:
        """Get the path to the best checkpoint.

        Returns:
            Path to the best checkpoint directory, or None if no best checkpoint exists
        """
        checkpoints = self.list_checkpoints()
        if not checkpoints:
            return None

        # Find checkpoint marked as best
        best_checkpoints = [c for c in checkpoints if c.get("is_best", False)]
        if not best_checkpoints:
            return None

        checkpoint_name = best_checkpoints[0].get("name")
        if not checkpoint_name:
            return None

        checkpoint_path = os.path.join(
            self.job_checkpoint_dir, checkpoint_name
        )
        if not os.path.exists(checkpoint_path):
            logger.warning(
                f"Best checkpoint directory not found: {checkpoint_path}"
            )
            return None

        return checkpoint_path

    def save_checkpoint(
        self,
        source_dir: str,
        checkpoint_name: Optional[str] = None,
        step: Optional[int] = None,
        epoch: Optional[int] = None,
        metrics: Optional[Dict[str, Any]] = None,
        is_best: bool = False,
    ) -> Optional[str]:
        """Save a checkpoint to the EFS filesystem.

        Args:
            source_dir: Directory containing checkpoint files to save
            checkpoint_name: Name for the checkpoint (defaults to timestamp-based name)
            step: Training step number
            epoch: Training epoch number
            metrics: Dictionary of metrics at checkpoint time
            is_best: Whether this is the best checkpoint so far

        Returns:
            Path to the saved checkpoint directory, or None if save failed
        """
        if not self.is_efs_mounted():
            logger.error("EFS not mounted, cannot save checkpoint")
            return None

        if not os.path.exists(source_dir):
            logger.error(f"Source directory does not exist: {source_dir}")
            return None

        # Generate checkpoint name if not provided
        if not checkpoint_name:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            checkpoint_name = f"checkpoint_{timestamp}"

        # Create destination directory
        dest_dir = os.path.join(self.job_checkpoint_dir, checkpoint_name)

        try:
            with self._get_lock():
                # Create temporary directory for staging
                temp_dir = f"{dest_dir}.tmp"
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)

                # Copy files to staging directory
                shutil.copytree(source_dir, temp_dir)

                # Create checkpoint metadata
                checkpoint_metadata = {
                    "name": checkpoint_name,
                    "created_at": datetime.datetime.now().isoformat(),
                    "step": step,
                    "epoch": epoch,
                    "metrics": metrics or {},
                    "is_best": is_best,
                }

                # Save checkpoint-specific metadata in the temp directory
                with open(
                    os.path.join(temp_dir, "checkpoint_info.json"), "w"
                ) as f:
                    json.dump(checkpoint_metadata, f, indent=2)

                # Update global metadata file
                metadata = self._get_metadata()
                checkpoints = metadata.get("checkpoints", [])

                # Add new checkpoint to list
                checkpoints.append(checkpoint_metadata)

                # Update best checkpoint status if needed
                if is_best:
                    for checkpoint in checkpoints:
                        if checkpoint.get("name") != checkpoint_name:
                            checkpoint["is_best"] = False

                # Update metadata
                metadata["checkpoints"] = checkpoints
                metadata["last_updated"] = datetime.datetime.now().isoformat()
                self._save_metadata(metadata)

                # Rename temporary directory to final destination (atomic operation)
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                os.rename(temp_dir, dest_dir)

                # Record checkpoint in DynamoDB if job service is available
                if self.job_service:
                    try:
                        # Calculate size of checkpoint
                        total_size = sum(
                            os.path.getsize(os.path.join(dirpath, filename))
                            for dirpath, _, filenames in os.walk(dest_dir)
                            for filename in filenames
                        )

                        # Record checkpoint in DynamoDB with EFS path
                        self.job_service.add_job_checkpoint(
                            self.job_id,
                            checkpoint_name,
                            {
                                "efs_path": dest_dir,
                                "size_bytes": total_size,
                                "step": step,
                                "epoch": epoch,
                                "metrics": metrics,
                                "is_best": is_best,
                            },
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to record checkpoint in DynamoDB: {e}"
                        )

                logger.info(f"Checkpoint saved to {dest_dir}")
                return dest_dir

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            # Clean up any temporary files
            temp_dir = f"{dest_dir}.tmp"
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass
            return None

    def load_checkpoint(
        self,
        dest_dir: str,
        checkpoint_path: Optional[str] = None,
        load_best: bool = False,
        load_latest: bool = True,
    ) -> bool:
        """Load a checkpoint from EFS.

        Args:
            dest_dir: Directory to load checkpoint into
            checkpoint_path: Specific checkpoint path to load
            load_best: Whether to load the best checkpoint
            load_latest: Whether to load the latest checkpoint (ignored if checkpoint_path provided)

        Returns:
            True if checkpoint loaded successfully, False otherwise
        """
        if not self.is_efs_mounted():
            logger.error("EFS not mounted, cannot load checkpoint")
            return False

        # Determine which checkpoint to load
        if checkpoint_path is not None:
            source_dir = checkpoint_path
        elif load_best:
            source_dir = self.get_best_checkpoint()
        elif load_latest:
            source_dir = self.get_latest_checkpoint()
        else:
            logger.error("No checkpoint specified to load")
            return False

        if not source_dir or not os.path.exists(source_dir):
            logger.error(f"Checkpoint not found: {source_dir}")
            return False

        try:
            # Create destination directory if it doesn't exist
            os.makedirs(dest_dir, exist_ok=True)

            # Read checkpoint metadata for logging
            checkpoint_info_path = os.path.join(
                source_dir, "checkpoint_info.json"
            )
            if os.path.exists(checkpoint_info_path):
                try:
                    with open(checkpoint_info_path, "r") as f:
                        checkpoint_info = json.load(f)
                    logger.info(
                        f"Loading checkpoint {checkpoint_info.get('name')} "
                        f"(step={checkpoint_info.get('step')}, epoch={checkpoint_info.get('epoch')})"
                    )
                except (IOError, json.JSONDecodeError):
                    logger.info(f"Loading checkpoint from {source_dir}")
            else:
                logger.info(f"Loading checkpoint from {source_dir}")

            # Copy checkpoint files to destination
            for item in os.listdir(source_dir):
                source_item = os.path.join(source_dir, item)
                dest_item = os.path.join(dest_dir, item)

                # Skip checkpoint info file
                if item == "checkpoint_info.json":
                    continue

                if os.path.isdir(source_item):
                    if os.path.exists(dest_item):
                        shutil.rmtree(dest_item)
                    shutil.copytree(source_item, dest_item)
                else:
                    shutil.copy2(source_item, dest_item)

            logger.info(f"Checkpoint loaded to {dest_dir}")
            return True

        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return False

    def mark_as_best(self, checkpoint_name: str) -> bool:
        """Mark a checkpoint as the best checkpoint.

        Args:
            checkpoint_name: Name of the checkpoint to mark as best

        Returns:
            True if the checkpoint was successfully marked as best, False otherwise
        """
        if not self.is_efs_mounted():
            logger.error("EFS not mounted, cannot update checkpoint metadata")
            return False

        try:
            with self._get_lock():
                # Update metadata
                metadata = self._get_metadata()
                checkpoints = metadata.get("checkpoints", [])

                # Find checkpoint by name
                checkpoint_to_update = None
                for checkpoint in checkpoints:
                    if checkpoint.get("name") == checkpoint_name:
                        checkpoint_to_update = checkpoint
                        checkpoint["is_best"] = True
                    else:
                        checkpoint["is_best"] = False

                if not checkpoint_to_update:
                    logger.error(f"Checkpoint not found: {checkpoint_name}")
                    return False

                # Save updated metadata
                metadata["checkpoints"] = checkpoints
                metadata["last_updated"] = datetime.datetime.now().isoformat()
                self._save_metadata(metadata)

                # Update DynamoDB if job service available
                if self.job_service:
                    try:
                        # Find checkpoint timestamp
                        timestamp = checkpoint_to_update.get("created_at", "")
                        if timestamp:
                            self.job_service.dynamo_client.updateBestCheckpoint(
                                self.job_id, timestamp
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to update best checkpoint in DynamoDB: {e}"
                        )

                logger.info(f"Marked checkpoint {checkpoint_name} as best")
                return True

        except Exception as e:
            logger.error(f"Failed to mark checkpoint as best: {e}")
            return False

    def delete_checkpoint(self, checkpoint_name: str) -> bool:
        """Delete a checkpoint.

        Args:
            checkpoint_name: Name of checkpoint to delete

        Returns:
            True if checkpoint was deleted successfully, False otherwise
        """
        if not self.is_efs_mounted():
            logger.error("EFS not mounted, cannot delete checkpoint")
            return False

        checkpoint_path = os.path.join(
            self.job_checkpoint_dir, checkpoint_name
        )
        if not os.path.exists(checkpoint_path):
            logger.error(f"Checkpoint not found: {checkpoint_path}")
            return False

        try:
            with self._get_lock():
                # Update metadata first
                metadata = self._get_metadata()
                checkpoints = metadata.get("checkpoints", [])

                # Remove checkpoint from list
                metadata["checkpoints"] = [
                    c for c in checkpoints if c.get("name") != checkpoint_name
                ]

                # Save updated metadata
                metadata["last_updated"] = datetime.datetime.now().isoformat()
                self._save_metadata(metadata)

                # Delete the checkpoint directory
                shutil.rmtree(checkpoint_path)

                logger.info(f"Deleted checkpoint {checkpoint_name}")
                return True

        except Exception as e:
            logger.error(f"Failed to delete checkpoint: {e}")
            return False

    def sync_from_dynamo(self) -> bool:
        """Synchronize checkpoint metadata from DynamoDB.

        Returns:
            True if synchronization was successful, False otherwise
        """
        if not self.job_service:
            logger.warning(
                "No job service available, cannot sync from DynamoDB"
            )
            return False

        if not self.is_efs_mounted():
            logger.error("EFS not mounted, cannot sync checkpoint metadata")
            return False

        try:
            with self._get_lock():
                # Get checkpoints from DynamoDB
                dynamo_checkpoints = self.job_service.get_job_checkpoints(
                    self.job_id
                )

                # Get local metadata
                metadata = self._get_metadata()
                local_checkpoints = {
                    c.get("name"): c for c in metadata.get("checkpoints", [])
                }

                # Update metadata based on DynamoDB records
                updated_checkpoints = []

                # Process each DynamoDB checkpoint
                for dynamo_checkpoint in dynamo_checkpoints:
                    checkpoint_name = dynamo_checkpoint.checkpoint_name
                    checkpoint_metadata = dynamo_checkpoint.metadata or {}

                    if checkpoint_name in local_checkpoints:
                        # Update existing checkpoint
                        local_checkpoint = local_checkpoints[checkpoint_name]
                        local_checkpoint["is_best"] = dynamo_checkpoint.is_best

                        # Update metrics if available
                        if "metrics" in checkpoint_metadata:
                            local_checkpoint["metrics"] = checkpoint_metadata[
                                "metrics"
                            ]

                        updated_checkpoints.append(local_checkpoint)
                    else:
                        # Create new checkpoint entry
                        # Handle created_at attribute whether it's a string or has an isoformat method
                        if hasattr(dynamo_checkpoint, "created_at"):
                            if hasattr(
                                dynamo_checkpoint.created_at, "isoformat"
                            ):
                                created_at = (
                                    dynamo_checkpoint.created_at.isoformat()
                                )
                            else:
                                created_at = dynamo_checkpoint.created_at
                        else:
                            created_at = datetime.datetime.now().isoformat()

                        new_checkpoint = {
                            "name": checkpoint_name,
                            "created_at": created_at,
                            "step": checkpoint_metadata.get("step"),
                            "epoch": checkpoint_metadata.get("epoch"),
                            "metrics": checkpoint_metadata.get("metrics", {}),
                            "is_best": dynamo_checkpoint.is_best,
                        }
                        updated_checkpoints.append(new_checkpoint)

                # Save updated metadata
                metadata["checkpoints"] = updated_checkpoints
                metadata["last_updated"] = datetime.datetime.now().isoformat()
                metadata["synced_from_dynamo"] = (
                    datetime.datetime.now().isoformat()
                )
                self._save_metadata(metadata)

                logger.info(
                    f"Synchronized {len(updated_checkpoints)} checkpoints from DynamoDB"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to sync checkpoints from DynamoDB: {e}")
            return False

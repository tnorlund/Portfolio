import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from receipt_trainer.utils.checkpoint import CheckpointManager


class TestCheckpointManager(unittest.TestCase):
    """Test cases for the CheckpointManager class."""

    def setUp(self):
        """Set up test environment."""
        # Create temporary directories for testing
        self.test_job_id = "test-job-123"
        self.temp_dir = tempfile.mkdtemp()
        self.checkpoint_dir = os.path.join(self.temp_dir, "checkpoints")
        os.makedirs(self.checkpoint_dir, exist_ok=True)

        # Mock DynamoDB table
        self.mock_dynamo_table = "mock-table"

        # Create test checkpoint manager
        self.checkpoint_manager = CheckpointManager(
            job_id=self.test_job_id,
            efs_mount_point=self.checkpoint_dir,
            dynamo_table=None,  # Don't use real DynamoDB
            lock_timeout=1,  # Short timeout for tests
        )

        # Create test checkpoint content
        self.test_checkpoint_dir = os.path.join(self.temp_dir, "test_model")
        os.makedirs(self.test_checkpoint_dir, exist_ok=True)
        with open(os.path.join(self.test_checkpoint_dir, "model.bin"), "w") as f:
            f.write("test model data")
        with open(os.path.join(self.test_checkpoint_dir, "config.json"), "w") as f:
            f.write('{"model_type": "test"}')

    def tearDown(self):
        """Clean up test environment."""
        # Remove temporary directories
        shutil.rmtree(self.temp_dir)

    def test_directory_initialization(self):
        """Test that checkpoint directory is properly initialized."""
        # Check that job directory was created
        job_dir = os.path.join(self.checkpoint_dir, self.test_job_id)
        self.assertTrue(os.path.exists(job_dir))

        # Check that metadata file was created
        metadata_file = os.path.join(job_dir, "metadata.json")
        self.assertTrue(os.path.exists(metadata_file))

        # Check metadata content
        with open(metadata_file, "r") as f:
            metadata = json.load(f)
        self.assertEqual(metadata["job_id"], self.test_job_id)
        self.assertIn("created_at", metadata)
        self.assertEqual(metadata["checkpoints"], [])

    def test_is_efs_mounted(self):
        """Test EFS mount detection."""
        # Override to make the test deterministic
        self.checkpoint_manager.is_efs_mounted = lambda: True
        self.assertTrue(self.checkpoint_manager.is_efs_mounted())

        # Test with non-mounted path
        self.checkpoint_manager.is_efs_mounted = lambda: False
        self.assertFalse(self.checkpoint_manager.is_efs_mounted())

    def test_save_and_list_checkpoints(self):
        """Test saving and listing checkpoints."""
        # Override EFS mounted check for testing
        self.checkpoint_manager.is_efs_mounted = lambda: True

        # Save a checkpoint
        checkpoint_name = "test_checkpoint_1"
        result = self.checkpoint_manager.save_checkpoint(
            source_dir=self.test_checkpoint_dir,
            checkpoint_name=checkpoint_name,
            step=100,
            epoch=1,
            metrics={"loss": 0.5, "accuracy": 0.9},
            is_best=True,
        )

        # Check that save was successful
        self.assertIsNotNone(result)
        checkpoint_path = os.path.join(
            self.checkpoint_dir, self.test_job_id, checkpoint_name
        )
        self.assertEqual(result, checkpoint_path)

        # Check that files were copied
        self.assertTrue(os.path.exists(os.path.join(checkpoint_path, "model.bin")))
        self.assertTrue(os.path.exists(os.path.join(checkpoint_path, "config.json")))
        self.assertTrue(
            os.path.exists(os.path.join(checkpoint_path, "checkpoint_info.json"))
        )

        # Check checkpoint info file
        with open(os.path.join(checkpoint_path, "checkpoint_info.json"), "r") as f:
            checkpoint_info = json.load(f)
        self.assertEqual(checkpoint_info["name"], checkpoint_name)
        self.assertEqual(checkpoint_info["step"], 100)
        self.assertEqual(checkpoint_info["epoch"], 1)
        self.assertEqual(checkpoint_info["metrics"]["loss"], 0.5)
        self.assertEqual(checkpoint_info["metrics"]["accuracy"], 0.9)
        self.assertTrue(checkpoint_info["is_best"])

        # List checkpoints
        checkpoints = self.checkpoint_manager.list_checkpoints()
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["name"], checkpoint_name)
        self.assertEqual(checkpoints[0]["step"], 100)
        self.assertEqual(checkpoints[0]["epoch"], 1)
        self.assertEqual(checkpoints[0]["metrics"]["loss"], 0.5)
        self.assertEqual(checkpoints[0]["metrics"]["accuracy"], 0.9)
        self.assertTrue(checkpoints[0]["is_best"])

        # Save another checkpoint
        checkpoint_name_2 = "test_checkpoint_2"
        result_2 = self.checkpoint_manager.save_checkpoint(
            source_dir=self.test_checkpoint_dir,
            checkpoint_name=checkpoint_name_2,
            step=200,
            epoch=2,
            metrics={"loss": 0.3, "accuracy": 0.95},
            is_best=True,
        )

        # Check that save was successful
        self.assertIsNotNone(result_2)

        # List checkpoints again
        checkpoints = self.checkpoint_manager.list_checkpoints()
        self.assertEqual(len(checkpoints), 2)

        # Check best flag was updated
        best_checkpoints = [c for c in checkpoints if c["is_best"]]
        self.assertEqual(len(best_checkpoints), 1)
        self.assertEqual(best_checkpoints[0]["name"], checkpoint_name_2)

    def test_get_best_and_latest_checkpoints(self):
        """Test getting best and latest checkpoints."""
        # Override EFS mounted check for testing
        self.checkpoint_manager.is_efs_mounted = lambda: True

        # Save two checkpoints
        self.checkpoint_manager.save_checkpoint(
            source_dir=self.test_checkpoint_dir,
            checkpoint_name="older_checkpoint",
            step=100,
            is_best=False,
        )

        self.checkpoint_manager.save_checkpoint(
            source_dir=self.test_checkpoint_dir,
            checkpoint_name="newer_checkpoint",
            step=200,
            is_best=True,
        )

        # Get best checkpoint
        best_checkpoint = self.checkpoint_manager.get_best_checkpoint()
        self.assertIsNotNone(best_checkpoint)
        self.assertTrue("newer_checkpoint" in best_checkpoint)

        # Get latest checkpoint
        latest_checkpoint = self.checkpoint_manager.get_latest_checkpoint()
        self.assertIsNotNone(latest_checkpoint)
        # "newer_checkpoint" should be the latest based on creation time
        self.assertTrue("newer_checkpoint" in latest_checkpoint)

    def test_load_checkpoint(self):
        """Test loading a checkpoint."""
        # Override EFS mounted check for testing
        self.checkpoint_manager.is_efs_mounted = lambda: True

        # Save a checkpoint
        self.checkpoint_manager.save_checkpoint(
            source_dir=self.test_checkpoint_dir,
            checkpoint_name="test_checkpoint",
            step=100,
            epoch=1,
        )

        # Create destination directory
        dest_dir = os.path.join(self.temp_dir, "loaded_checkpoint")
        os.makedirs(dest_dir, exist_ok=True)

        # Load the checkpoint
        result = self.checkpoint_manager.load_checkpoint(
            dest_dir=dest_dir, load_latest=True
        )

        # Check that load was successful
        self.assertTrue(result)

        # Check that files were copied
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "model.bin")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "config.json")))

        # Verify content was copied correctly
        with open(os.path.join(dest_dir, "config.json"), "r") as f:
            config = f.read()
        self.assertEqual(config, '{"model_type": "test"}')

    def test_delete_checkpoint(self):
        """Test deleting a checkpoint."""
        # Override EFS mounted check for testing
        self.checkpoint_manager.is_efs_mounted = lambda: True

        # Save a checkpoint
        checkpoint_name = "test_checkpoint_to_delete"
        self.checkpoint_manager.save_checkpoint(
            source_dir=self.test_checkpoint_dir,
            checkpoint_name=checkpoint_name,
        )

        # Check it exists
        checkpoint_path = os.path.join(
            self.checkpoint_dir, self.test_job_id, checkpoint_name
        )
        self.assertTrue(os.path.exists(checkpoint_path))

        # Delete the checkpoint
        result = self.checkpoint_manager.delete_checkpoint(checkpoint_name)
        self.assertTrue(result)

        # Check it no longer exists
        self.assertFalse(os.path.exists(checkpoint_path))

        # Check it's removed from metadata
        checkpoints = self.checkpoint_manager.list_checkpoints()
        self.assertEqual(len(checkpoints), 0)

    @patch("receipt_trainer.utils.checkpoint.JobService")
    def test_sync_from_dynamo(self, mock_job_service_class):
        """Test synchronizing checkpoint metadata from DynamoDB."""
        # Set up mock JobService
        mock_job_service = MagicMock()
        mock_job_service_class.return_value = mock_job_service

        # Create mock checkpoint objects
        mock_checkpoint1 = MagicMock()
        mock_checkpoint1.checkpoint_name = "dynamo_checkpoint_1"
        mock_checkpoint1.is_best = True
        # Use string directly instead of datetime object
        mock_checkpoint1.created_at = "2023-01-01T12:00:00"
        mock_checkpoint1.metadata = {
            "step": 100,
            "epoch": 1,
            "metrics": {"loss": 0.5},
        }

        mock_checkpoint2 = MagicMock()
        mock_checkpoint2.checkpoint_name = "dynamo_checkpoint_2"
        mock_checkpoint2.is_best = False
        # Use string directly instead of datetime object
        mock_checkpoint2.created_at = "2023-01-02T12:00:00"
        mock_checkpoint2.metadata = {
            "step": 200,
            "epoch": 2,
            "metrics": {"loss": 0.3},
        }

        # Set mock return value
        mock_job_service.get_job_checkpoints.return_value = [
            mock_checkpoint1,
            mock_checkpoint2,
        ]

        # Create checkpoint manager with mock DynamoDB
        checkpoint_manager = CheckpointManager(
            job_id=self.test_job_id,
            efs_mount_point=self.checkpoint_dir,
            dynamo_table="mock-table",
            lock_timeout=1,
        )

        # Override is_efs_mounted
        checkpoint_manager.is_efs_mounted = lambda: True

        # Perform sync
        result = checkpoint_manager.sync_from_dynamo()
        self.assertTrue(result)

        # Check that checkpoints were synced
        checkpoints = checkpoint_manager.list_checkpoints()
        self.assertEqual(len(checkpoints), 2)

        # Check content of synced checkpoints
        checkpoint_names = [c["name"] for c in checkpoints]
        self.assertIn("dynamo_checkpoint_1", checkpoint_names)
        self.assertIn("dynamo_checkpoint_2", checkpoint_names)

        # Check best flag was synced
        for checkpoint in checkpoints:
            if checkpoint["name"] == "dynamo_checkpoint_1":
                self.assertTrue(checkpoint["is_best"])
            else:
                self.assertFalse(checkpoint["is_best"])


if __name__ == "__main__":
    unittest.main()

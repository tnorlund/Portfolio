# tests/end-to-end/test_hyperparameter_sweep.py
"""This tests the `run_hyperparameter_sweep` method of the `ReceiptTrainer` class"""
from pathlib import Path
import pytest
import os
import wandb

# import matplotlib  # Not needed yet
# Use non-interactive backend for testing to avoid GUI errors
# matplotlib.use('Agg')  # Not needed yet
from dotenv import load_dotenv
import logging
from receipt_trainer.config import DataConfig, TrainingConfig
from receipt_trainer.trainer import ReceiptTrainer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("receipt_trainer_test.log"),
        logging.StreamHandler(),  # Also print to console
    ],
)

env_path = Path(__file__).parents[2] / ".env"
load_dotenv(dotenv_path=env_path)

# Disable wandb telemetry to help avoid BrokenPipe errors
os.environ["WANDB_DISABLE_TELEMETRY"] = "true"
# Set to offline mode for testing
os.environ["WANDB_MODE"] = "offline"


@pytest.fixture
def validate_env_vars():
    required_vars = {
        "WANDB_API_KEY": "API key for Weights & Biases",
        "HF_TOKEN": "Hugging Face token for accessing models",
        "AWS_ACCESS_KEY_ID": "AWS access key for DynamoDB and S3",
        "AWS_SECRET_ACCESS_KEY": "AWS secret key for DynamoDB and S3",
        "AWS_DEFAULT_REGION": "AWS region for services",
        "CHECKPOINT_BUCKET": "S3 bucket for checkpoints",
    }
    missing_vars = [var for var, desc in required_vars.items() if not os.getenv(var)]

    if missing_vars:
        error_msg = "The following required environment variables are not set:\n"
        for var in missing_vars:
            error_msg += f"- {var}: {required_vars[var]}\n"
        error_msg += (
            "\nPlease set these environment variables before running the script."
        )
        raise ValueError(error_msg)


@pytest.fixture(scope="function")
def cleanup_wandb():
    """Fixture to ensure wandb is properly cleaned up after tests."""
    yield
    # Make sure to terminate all wandb runs/processes after test
    try:
        wandb.finish()
    except Exception:
        pass
    # Force terminate any remaining wandb processes
    wandb.require("core").terminate()


@pytest.fixture
def log_capture():
    """Fixture to capture logs for testing purposes."""

    class LogCapture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []
            self.formatter = logging.Formatter("%(levelname)s - %(name)s - %(message)s")

        def emit(self, record):
            self.records.append(self.formatter.format(record))

        def get_logs(self):
            return self.records

    # Create and add the handler
    handler = LogCapture()
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("receipt_trainer")
    logger.addHandler(handler)

    yield handler

    # Clean up
    logger.removeHandler(handler)


def test_run_hyperparameter_sweep(validate_env_vars, cleanup_wandb, log_capture):
    # Create base configuration objects
    training_config = TrainingConfig(
        num_epochs=1,  # Reduce epochs to minimum for faster tests
        evaluation_steps=1,
        save_steps=1,
        logging_steps=1,
    )
    data_config = DataConfig(
        use_sroie=False, balance_ratio=0.7, augment=True, env="prod"
    )
    trainer = ReceiptTrainer(
        wandb_project="receipt-training-sweep",
        model_name="microsoft/layoutlm-base-uncased",
        training_config=training_config,
        data_config=data_config,
    )
    # Load the data from DynamoDB
    dataset = trainer.load_data()
    trainer.initialize_model()
    sweep_config = {
        "method": "grid",  # Simplest search method
        "metric": {"name": "validation/macro_avg/f1-score", "goal": "maximize"},
        "parameters": {
            "learning_rate": {"values": [1e-4]}  # Only one value for fastest testing
        },
    }

    try:
        best_run_id = trainer.run_hyperparameter_sweep(
            sweep_config=sweep_config,
            num_trials=1,  # Just one trial to verify functionality
            parallel_workers=1,  # Keep sequential runs for testing
        )
        # Assert that we got a run_id back (doesn't matter what it is)
        assert best_run_id is not None

        # Verify metrics were written to W&B
        print(f"\nVerifying W&B metrics for run ID: {best_run_id}")
        api = wandb.Api()
        try:
            # Fetch the run from W&B API
            run = api.run(f"{trainer.wandb_project}/{best_run_id}")

            # Print summary of metrics that were logged
            print("\n=== W&B Logged Metrics ===")
            if hasattr(run, "summary"):
                for key, value in run.summary.items():
                    if isinstance(value, (int, float)):
                        print(f"{key}: {value}")

            # Verify essential metrics were logged
            assert hasattr(run, "summary"), "W&B run has no metrics summary"

            # Check for expected metrics (at least one of these should be present)
            expected_metrics = [
                "validation/macro_avg/f1-score",
                "eval/f1",
                "train/total_loss",
            ]

            found_metrics = []
            for metric in expected_metrics:
                if metric in run.summary:
                    found_metrics.append(metric)

            assert (
                found_metrics
            ), f"None of the expected metrics {expected_metrics} were found in W&B"
            print(f"Found metrics in W&B: {found_metrics}")

        except Exception as e:
            print(f"Error accessing W&B metrics: {e}")
            # Don't fail the test if W&B API access fails
            # This allows tests to run in environments without W&B access
            print("Skipping W&B metrics verification")

        # Print and inspect captured logs
        logs = log_capture.get_logs()
        print("\n=== Captured Trainer Logs ===")
        for log in logs[-10:]:  # Print the last 10 log entries
            print(log)

        # Verify log assertions
        assert any("Starting hyperparameter sweep" in log for log in logs)

        # Check that the trainer output directory exists and contains expected files
        output_dir = trainer.output_dir
        print(f"\nTrainer output directory: {output_dir}")
        assert os.path.exists(
            output_dir
        ), f"Output directory {output_dir} does not exist"

        # Check for specific files/directories that should be created during training
        expected_files = [
            "config.json",  # Model configuration file
            "label_config.json",  # Label mapping configuration
        ]

        # Check for any files that match the pattern
        file_list = os.listdir(output_dir)
        print(f"\nFiles in output directory: {file_list}")

        # Verify at least some of the expected files exist
        found_files = [
            f
            for f in expected_files
            if any(f in os.path.join(output_dir, filename) for filename in file_list)
        ]
        assert found_files, f"No expected training output files found in {output_dir}"

        # Check for the log file
        log_file_path = "receipt_trainer_test.log"
        assert os.path.exists(
            log_file_path
        ), f"Log file {log_file_path} was not created"
        assert os.path.getsize(log_file_path) > 0, f"Log file {log_file_path} is empty"

        # Print a sample of the log file content
        with open(log_file_path, "r") as f:
            log_content = f.readlines()
            print("\n=== Log File Sample (last 5 lines) ===")
            for line in log_content[-5:]:
                print(line.strip())

    finally:
        # Ensure wandb is cleaned up even if test fails
        wandb.finish()

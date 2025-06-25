# receipt_trainer/examples/optimize_hyperparameters.py
"""Example script demonstrating hyperparameter optimization using ReceiptTrainer's built-in sweep method."""

import os
from pathlib import Path
from dotenv import load_dotenv
import time
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig
from transformers import TrainerCallback

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class PerStepLoggingCallback(TrainerCallback):
    """Logs training metrics and evaluation metrics to DynamoDB at every step."""

    def __init__(self, job_service=None, job_id=None):
        super().__init__()
        self.job_service = job_service
        self.job_id = job_id

    def on_step_end(self, args, state, control, logs=None, **kwargs):
        if logs and state.is_world_process_zero and self.job_service and self.job_id:
            for k, v in logs.items():
                self.job_service.add_job_metric(
                    job_id=self.job_id,
                    metric_name=f"train/{k}",
                    metric_value=v,
                    metadata={"step": state.global_step},
                )

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        if metrics and state.is_world_process_zero and self.job_service and self.job_id:
            for k, v in metrics.items():
                self.job_service.add_job_metric(
                    job_id=self.job_id,
                    metric_name=f"eval/{k}",
                    metric_value=v,
                    metadata={"step": state.global_step},
                )


def validate_environment():
    """Validate that all required environment variables are set.

    Raises:
        ValueError: If any required environment variables are missing, with a detailed
            message listing which specific variables are not set.
    """
    required_vars = {
        "HF_TOKEN": "Hugging Face token for accessing models",
        "AWS_ACCESS_KEY_ID": "AWS access key for DynamoDB and S3",
        "AWS_SECRET_ACCESS_KEY": "AWS secret key for DynamoDB and S3",
        "AWS_DEFAULT_REGION": "AWS region for services",
        "CHECKPOINT_BUCKET": "S3 bucket for checkpoints",
        "DYNAMO_TABLE": "DynamoDB table for metrics and job tracking",
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


def main():
    # Validate environment variables
    validate_environment()

    # Create base configuration objects
    training_config = TrainingConfig(
        num_epochs=10,
        evaluation_steps=10,
        save_steps=10,
        logging_steps=1,
    )
    data_config = DataConfig(
        use_sroie=True, balance_ratio=0.7, augment=True, env="prod"
    )

    try:
        # Initialize the trainer with DynamoDB table
        dynamo_table = os.getenv("DYNAMO_TABLE")
        trainer = ReceiptTrainer(
            model_name="microsoft/layoutlm-base-uncased",
            training_config=training_config,
            data_config=data_config,
            dynamo_table=dynamo_table,
        )

        # Load data and initialize model once before starting the sweep.
        print("Loading dataset...")
        dataset = trainer.load_data()
        print(
            f"Loaded dataset with {len(dataset['train'])} training and {len(dataset['validation'])} validation examples"
        )

        print("Initializing model...")
        trainer.initialize_model()

        # Define your sweep configuration
        sweep_config = {
            "method": "bayes",
            "metric": {"name": "validation/macro_avg/f1-score", "goal": "maximize"},
            "parameters": {
                "learning_rate": {
                    "distribution": "log_uniform_values",
                    "min": 1e-4,
                    "max": 1e-3,
                },
                "batch_size": {
                    "values": [4, 8, 16],
                },
                "gradient_accumulation_steps": {
                    "values": [32, 64, 128],
                },
                "warmup_ratio": {
                    "distribution": "uniform",
                    "min": 0.0,
                    "max": 0.3,
                },
                "weight_decay": {
                    "distribution": "log_uniform_values",
                    "min": 1e-4,
                    "max": 1e-2,
                },
            },
        }

        # Run the hyperparameter sweep via the trainer's built-in method.
        # This method will handle the sweep creation and running of trials.
        best_run_id = trainer.run_hyperparameter_sweep(
            sweep_config=sweep_config,
            num_trials=10,  # Total number of trials to run
            parallel_workers=1,  # Ensure sequential runs
        )

        print(f"Hyperparameter sweep completed. Best run ID: {best_run_id}")

    except Exception as e:
        print(f"Error during hyperparameter optimization: {e}")
        raise


if __name__ == "__main__":
    main()

"""Example script demonstrating how to use parallel hyperparameter optimization with Receipt Trainer."""

import os
import torch
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig


def main():
    """Run parallel hyperparameter optimization example."""
    # Set environment variables (in production, use a .env file or environment)
    os.environ.update(
        {
            "HF_TOKEN": "your-hf-token",
            "AWS_ACCESS_KEY_ID": "your-aws-key",
            "AWS_SECRET_ACCESS_KEY": "your-aws-secret",
            "AWS_DEFAULT_REGION": "us-west-2",
            "CHECKPOINT_BUCKET": "your-checkpoint-bucket",
            "DYNAMO_TABLE": "your-dynamo-metrics-table",
        }
    )

    # Get available GPUs
    num_gpus = torch.cuda.device_count()
    if num_gpus < 1:
        raise RuntimeError("No GPUs available for parallel sweep execution")

    print(f"Found {num_gpus} GPUs available for parallel sweep")

    # Create base configurations with parallel sweep settings
    training_config = TrainingConfig(
        num_epochs=10,  # Reduced epochs for each trial
        evaluation_steps=50,  # More frequent evaluation during sweep
        save_steps=50,
        # Parallel sweep settings
        parallel_sweep_workers=num_gpus,  # Use all available GPUs
        parallel_sweep_gpu_ids=list(range(num_gpus)),  # Assign one worker per GPU
        parallel_sweep_per_worker_trials=5,  # Each worker runs 5 trials
    )

    data_config = DataConfig(
        use_sroie=True,
        balance_ratio=0.7,
        augment=True,
    )

    # Initialize trainer with DynamoDB table
    dynamo_table = os.getenv("DYNAMO_TABLE")
    trainer = ReceiptTrainer(
        model_name="microsoft/layoutlm-base-uncased",
        training_config=training_config,
        data_config=data_config,
        dynamo_table=dynamo_table,
    )

    # Custom sweep configuration (optional)
    sweep_config = {
        "method": "bayes",  # Bayesian optimization
        "metric": {"name": "validation/macro_avg/f1-score", "goal": "maximize"},
        "parameters": {
            "learning_rate": {
                "distribution": "log_uniform",
                "min": -9.21,  # 1e-4
                "max": -6.91,  # 1e-3
            },
            "batch_size": {"values": [8, 16, 32]},
            "gradient_accumulation_steps": {"values": [16, 32, 64]},
            "warmup_ratio": {"distribution": "uniform", "min": 0.0, "max": 0.3},
            "weight_decay": {
                "distribution": "log_uniform",
                "min": -9.21,  # 1e-4
                "max": -4.61,  # 1e-2
            },
        },
    }

    try:
        # Load and prepare data
        dataset = trainer.load_data()
        print(
            f"Loaded dataset with {len(dataset['train'])} training and {len(dataset['validation'])} validation examples"
        )

        # Initialize model
        trainer.initialize_model()

        # Run parallel hyperparameter sweep
        total_trials = num_gpus * training_config.parallel_sweep_per_worker_trials
        best_run_id = trainer.run_hyperparameter_sweep(
            sweep_config=sweep_config,
            num_trials=total_trials,  # Total number of trials across all workers
            early_stopping_min_trials=5,  # Run at least 5 trials
            early_stopping_grace_trials=3,  # Stop if no improvement after 3 trials
            parallel_workers=num_gpus,  # Use all available GPUs
            gpu_ids=list(range(num_gpus)),  # Assign one worker per GPU
        )

        print(
            f"Parallel hyperparameter optimization completed. Best run: {best_run_id}"
        )

    except Exception as e:
        print(f"Error during parallel hyperparameter optimization: {e}")
        raise


if __name__ == "__main__":
    main()

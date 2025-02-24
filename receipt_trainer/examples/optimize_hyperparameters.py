"""Example script demonstrating how to use hyperparameter optimization with Receipt Trainer."""

import os
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig


def main():
    """Run hyperparameter optimization example."""
    # Set environment variables (in production, use a .env file or environment)
    os.environ.update(
        {
            "WANDB_API_KEY": "your-wandb-key",
            "HF_TOKEN": "your-hf-token",
            "AWS_ACCESS_KEY_ID": "your-aws-key",
            "AWS_SECRET_ACCESS_KEY": "your-aws-secret",
            "AWS_DEFAULT_REGION": "us-west-2",
            "CHECKPOINT_BUCKET": "your-checkpoint-bucket",
        }
    )

    # Create base configurations
    training_config = TrainingConfig(
        num_epochs=10,  # Reduced epochs for each trial
        evaluation_steps=50,  # More frequent evaluation during sweep
        save_steps=50,
    )

    data_config = DataConfig(
        use_sroie=True,
        balance_ratio=0.7,
        augment=True,
    )

    # Initialize trainer
    trainer = ReceiptTrainer(
        wandb_project="receipt-training-sweep",
        model_name="microsoft/layoutlm-base-uncased",
        training_config=training_config,
        data_config=data_config,
    )

    # Custom sweep configuration (optional)
    sweep_config = {
        "method": "bayes",  # Bayesian optimization
        "metric": {
            "name": "validation/macro_avg/f1-score",
            "goal": "maximize"
        },
        "parameters": {
            "learning_rate": {
                "distribution": "log_uniform",
                "min": -9.21,  # 1e-4
                "max": -6.91,  # 1e-3
            },
            "batch_size": {
                "values": [8, 16, 32]
            },
            "gradient_accumulation_steps": {
                "values": [16, 32, 64]
            },
            "warmup_ratio": {
                "distribution": "uniform",
                "min": 0.0,
                "max": 0.3
            },
            "weight_decay": {
                "distribution": "log_uniform",
                "min": -9.21,  # 1e-4
                "max": -4.61,  # 1e-2
            }
        }
    }

    try:
        # Load and prepare data
        dataset = trainer.load_data()
        print(
            f"Loaded dataset with {len(dataset['train'])} training and {len(dataset['validation'])} validation examples"
        )

        # Initialize model and W&B
        trainer.initialize_model()
        trainer.initialize_wandb()

        # Run hyperparameter sweep
        best_run_id = trainer.run_hyperparameter_sweep(
            sweep_config=sweep_config,
            num_trials=10  # Number of trials to run
        )

        print(f"Hyperparameter optimization completed. Best run: {best_run_id}")

    except Exception as e:
        print(f"Error during hyperparameter optimization: {e}")
        raise


if __name__ == "__main__":
    main() 
"""Example script demonstrating how to use the Receipt Trainer package."""

import os
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig


def main():
    """Run the training example."""
    # Set environment variables (in production, use a .env file or environment)
    os.environ.update(
        {
            "WANDB_API_KEY": "your-wandb-key",
            "HF_TOKEN": "your-hf-token",
            "AWS_ACCESS_KEY_ID": "your-aws-key",
            "AWS_SECRET_ACCESS_KEY": "your-aws-secret",
            "AWS_DEFAULT_REGION": "us-west-2",
        }
    )

    # Create configurations
    training_config = TrainingConfig(
        batch_size=8, learning_rate=2e-5, num_epochs=10, gradient_accumulation_steps=4
    )

    data_config = DataConfig(
        use_sroie=True,
        balance_ratio=0.7,
        augment=True,
        env="prod",  # Use production DynamoDB table
    )

    # Initialize trainer
    trainer = ReceiptTrainer(
        wandb_project="receipt-training",
        model_name="microsoft/layoutlm-base-uncased",
        training_config=training_config,
        data_config=data_config,
    )

    # Load and prepare data
    dataset = trainer.load_data()
    print(
        f"Loaded dataset with {len(dataset['train'])} training and {len(dataset['validation'])} validation examples"
    )

    # Initialize model and W&B
    trainer.initialize_model()
    trainer.initialize_wandb()

    # Configure and start training
    trainer.configure_training()
    trainer.train(
        enable_checkpointing=True, enable_early_stopping=True, log_to_wandb=True
    )

    # Save the trained model
    trainer.save_model("./trained_model")


if __name__ == "__main__":
    main()

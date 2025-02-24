"""Example script demonstrating how to use the Receipt Trainer package with spot instance handling."""

import os
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig
from receipt_trainer.utils.aws import get_dynamo_table


def validate_environment():
    """Validate that all required environment variables are set.

    Raises:
        ValueError: If any required environment variable is missing or empty.
    """
    required_vars = {
        "WANDB_API_KEY": "API key for Weights & Biases",
        "HF_TOKEN": "Hugging Face token for accessing models",
        "AWS_ACCESS_KEY_ID": "AWS access key for DynamoDB and S3",
        "AWS_SECRET_ACCESS_KEY": "AWS secret key for DynamoDB and S3",
        "AWS_DEFAULT_REGION": "AWS region for services",
        "CHECKPOINT_BUCKET": "S3 bucket for checkpoints",
    }

    missing_vars = []
    for var, description in required_vars.items():
        if not os.getenv(var):
            missing_vars.append(f"{var} ({description})")

    if missing_vars:
        raise ValueError(
            "Missing required environment variables:\n"
            + "\n".join(f"- {var}" for var in missing_vars)
        )


def main():
    """Run the training example with spot instance handling."""

    # Validate environment variables
    validate_environment()

    try:
        # Get DynamoDB table name from Pulumi stack
        dynamo_table = get_dynamo_table(env="prod")
        if not dynamo_table:
            raise ValueError(
                "Could not find DynamoDB table name in Pulumi stack outputs"
            )
    except Exception as e:
        print(f"Error getting DynamoDB table name: {str(e)}")
        print("Please ensure you have proper AWS credentials and permissions")
        raise

    # Create configurations with spot-instance-friendly settings
    training_config = TrainingConfig(
        batch_size=8,
        learning_rate=2e-5,
        num_epochs=10,
        gradient_accumulation_steps=4,
        # More frequent checkpointing for spot instances
        save_steps=50,
        evaluation_steps=50,
    )

    data_config = DataConfig(
        use_sroie=True,
        balance_ratio=0.7,
        augment=True,
        env="prod",  # Use production DynamoDB table
    )

    # Initialize trainer with DynamoDB table
    trainer = ReceiptTrainer(
        wandb_project="receipt-training",
        model_name="microsoft/layoutlm-base-uncased",
        training_config=training_config,
        data_config=data_config,
        dynamo_table=dynamo_table,  # Pass the DynamoDB table name
    )

    try:
        # Initialize DynamoDB client explicitly
        trainer.initialize_dynamo()

        # Load and prepare data
        dataset = trainer.load_data()
        
        # Convert data to the expected format for balancing
        if len(dataset) > 0:
            print(
                f"Loaded dataset with {len(dataset['train'])} training and "
                f"{len(dataset['validation'])} validation examples"
            )
        else:
            print("Warning: No data was loaded from either DynamoDB or SROIE dataset")
            return

        # Initialize model and W&B
        trainer.initialize_model()
        trainer.initialize_wandb()

        # Configure and start training with spot instance handling
        trainer.configure_training()

        # Start training with spot instance handling enabled
        trainer.train(
            enable_checkpointing=True,  # Enable checkpointing for spot instances
            enable_early_stopping=True,
            log_to_wandb=True,
            resume_training=True,  # Enable automatic checkpoint resumption
        )

    except KeyboardInterrupt:
        print("\nTraining interrupted by user. Saving checkpoint...")
        trainer.save_checkpoint("interrupt_checkpoint")

    except Exception as e:
        print(f"Training failed: {str(e)}")
        # Even on failure, try to save a checkpoint
        try:
            trainer.save_checkpoint("error_checkpoint")
        except:
            pass
        raise


if __name__ == "__main__":
    main()

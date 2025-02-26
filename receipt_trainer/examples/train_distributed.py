"""Example script demonstrating how to use distributed training with Receipt Trainer."""

import os
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig


def setup_trainer(rank, world_size):
    """Initialize the trainer for a specific process."""
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

    # Create configurations with distributed training settings
    training_config = TrainingConfig(
        batch_size=8,  # Per-GPU batch size
        learning_rate=2e-5,
        num_epochs=10,
        gradient_accumulation_steps=4,
        save_steps=50,
        evaluation_steps=50,
        # Distributed training settings
        distributed_training=True,
        local_rank=rank,
        world_size=world_size,
        ddp_backend="nccl",  # Use NCCL backend for GPU training
        sync_bn=True,  # Use SyncBatchNorm for better stability
    )

    data_config = DataConfig(
        use_sroie=True,
        balance_ratio=0.7,
        augment=True,
        env="prod",
    )

    # Initialize trainer
    trainer = ReceiptTrainer(
        wandb_project="receipt-training-distributed",
        model_name="microsoft/layoutlm-base-uncased",
        training_config=training_config,
        data_config=data_config,
    )

    try:
        # Load and prepare data
        dataset = trainer.load_data()
        if rank == 0:  # Only log from main process
            print(
                f"Loaded dataset with {len(dataset['train'])} training and "
                f"{len(dataset['validation'])} validation examples"
            )

        # Initialize model and W&B
        trainer.initialize_model()
        trainer.initialize_wandb()

        # Configure and start training
        trainer.configure_training()
        trainer.train(
            enable_checkpointing=True,
            enable_early_stopping=True,
            log_to_wandb=True,
        )

    except Exception as e:
        if rank == 0:
            print(f"Error during training: {e}")
        raise


def main():
    """Run distributed training on all available GPUs."""
    # Get the number of available GPUs
    world_size = torch.cuda.device_count()
    if world_size < 1:
        raise RuntimeError("No CUDA devices available for distributed training")

    print(f"Starting distributed training with {world_size} GPUs")

    # Start processes
    try:
        mp.spawn(
            setup_trainer,
            args=(world_size,),
            nprocs=world_size,
            join=True
        )
    except Exception as e:
        print(f"Error during distributed training: {e}")
        raise


if __name__ == "__main__":
    main() 
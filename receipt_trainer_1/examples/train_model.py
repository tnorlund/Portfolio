# receipt_trainer/examples/train_model.py
"""Example script demonstrating how to use the Receipt Trainer package with spot instance handling."""

import os
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig
from receipt_trainer.utils.aws import get_dynamo_table
from transformers import TrainerCallback
import numpy as np
from sklearn.metrics import (
    confusion_matrix,
    precision_recall_fscore_support,
    accuracy_score,
)
import seaborn as sns
import matplotlib.pyplot as plt
import argparse
import traceback
import uuid
import tempfile


class MetricsCallback(TrainerCallback):
    """Custom callback to log confusion matrix and evaluation metrics during training."""

    def __init__(self, job_service=None, job_id=None):
        """Initialize the callback.

        Args:
            job_service: The JobService instance for logging metrics
            job_id: The ID of the current job
        """
        super().__init__()
        self.trainer = None
        self.step = 0
        self.job_service = job_service
        self.job_id = job_id

        if not self.job_service or not self.job_id:
            raise ValueError(
                "JobService and job_id must be provided for metrics logging."
            )

    def setup(self, trainer):
        """Set up the callback with a trainer instance."""
        self.trainer = trainer
        print(f"MetricsCallback setup complete with trainer: {trainer}")

    def on_train_begin(self, args, state, control, **kwargs):
        """Called when training begins."""
        if "trainer" in kwargs:
            self.trainer = kwargs["trainer"]
        print("Training started - MetricsCallback initialized")
        if not self.trainer:
            print("Warning: Trainer not available in on_train_begin")

        # Log training start
        if self.job_service and self.job_id:
            self.job_service.add_job_log(
                self.job_id, "INFO", "Training started with MetricsCallback"
            )

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        """Log confusion matrix and metrics after each evaluation."""
        print("\nStarting evaluation logging...")

        if not metrics:
            print("Warning: No metrics provided to callback")
            return

        # Try to get trainer from kwargs if not already set
        if not self.trainer and "trainer" in kwargs:
            self.trainer = kwargs["trainer"]
            print("Retrieved trainer from kwargs")

        if not self.trainer:
            print("Warning: No trainer available in callback")
            if "model" in kwargs:
                print("Model available in kwargs")
            if "eval_dataset" in kwargs:
                print("Eval dataset available in kwargs")
            return

        try:
            print(f"Current metrics received: {metrics}")
            print(f"Trainer state: {state.__dict__ if state else 'None'}")

            # Get predictions for validation set
            print("Getting predictions...")
            predictions = self.trainer.predict(self.trainer.eval_dataset)

            # Convert predictions to labels
            pred_labels = np.argmax(predictions.predictions, axis=2)
            true_labels = predictions.label_ids

            # Flatten predictions and labels, removing padding (-100)
            true_flat = []
            pred_flat = []
            for i in range(len(true_labels)):
                for j in range(len(true_labels[i])):
                    if true_labels[i][j] != -100:
                        true_flat.append(true_labels[i][j])
                        pred_flat.append(pred_labels[i][j])

            print(f"Processed {len(true_flat)} valid predictions")

            # Get label names
            id2label = self.trainer.model.config.id2label
            labels = list(range(len(id2label)))
            label_names = [id2label[i] for i in labels]

            # Calculate metrics
            precision, recall, f1, support = precision_recall_fscore_support(
                true_flat, pred_flat, labels=labels, zero_division=0
            )
            accuracy = accuracy_score(true_flat, pred_flat)

            # Create metrics dictionary with step information
            self.step = state.global_step if state else 0

            # Create metrics dictionary
            metric_dict = {
                "train/global_step": self.step,
                "eval/accuracy": float(accuracy),
                "eval/f1": float(np.mean(f1)),
                "eval/loss": float(metrics.get("eval_loss", 0.0)),
            }

            # Log per-label metrics
            for i, label in enumerate(label_names):
                if label != "O":  # Skip the "Outside" label
                    metric_dict.update(
                        {
                            f"eval/precision_{label}": float(precision[i]),
                            f"eval/recall_{label}": float(recall[i]),
                            f"eval/f1_{label}": float(f1[i]),
                            f"eval/support_{label}": int(support[i]),
                        }
                    )

            print("\nLogging metrics to DynamoDB:")
            for key, value in metric_dict.items():
                print(f"{key}: {value}")

                # Log each metric to DynamoDB
                if self.job_service and self.job_id:
                    self.job_service.add_job_metric(
                        job_id=self.job_id,
                        metric_name=key,
                        metric_value=value,
                        metadata={"step": self.step},
                    )

            # Create confusion matrix
            cm = confusion_matrix(true_flat, pred_flat, labels=labels)

            # Create and log performance plot
            plt.figure(figsize=(12, 6))
            x = np.arange(len(label_names))
            width = 0.25

            plt.bar(x - width, precision, width, label="Precision")
            plt.bar(x, recall, width, label="Recall")
            plt.bar(x + width, f1, width, label="F1")

            plt.xlabel("Labels")
            plt.ylabel("Score")
            plt.title("Performance by Label")
            plt.xticks(x, label_names, rotation=45, ha="right")
            plt.legend()
            plt.tight_layout()

            # Save the plot to a temporary file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                plt_path = tmp_file.name
                plt.savefig(plt_path)

                # Log the plot path to DynamoDB
                if self.job_service and self.job_id:
                    self.job_service.add_job_log(
                        self.job_id,
                        "INFO",
                        f"Performance plot saved to {plt_path}",
                    )

                    # Add as a resource
                    self.job_service.add_job_resource(
                        job_id=self.job_id,
                        resource_type="PLOT",
                        resource_id=f"performance_plot_{self.step}",
                        metadata={
                            "path": plt_path,
                            "type": "performance_plot",
                            "step": self.step,
                        },
                    )

            plt.close("all")

            # Print per-label performance
            print("\nPer-label Performance:")
            for i, label in enumerate(label_names):
                if label != "O":
                    print(
                        f"{label:15} F1: {f1[i]:.4f} | Precision: {precision[i]:.4f} | Recall: {recall[i]:.4f}"
                    )

        except Exception as e:
            print(f"Error in metrics callback: {str(e)}")
            print("Full error details:")
            traceback.print_exc()
            # Don't raise the exception to avoid interrupting training


def validate_environment():
    """Validate that all required environment variables are set.

    Raises:
        ValueError: If any required environment variable is missing or empty.
    """
    required_vars = {
        "HF_TOKEN": "Hugging Face token for accessing models",
        "AWS_ACCESS_KEY_ID": "AWS access key for DynamoDB and S3",
        "AWS_SECRET_ACCESS_KEY": "AWS secret key for DynamoDB and S3",
        "AWS_DEFAULT_REGION": "AWS region for services",
        "CHECKPOINT_BUCKET": "S3 bucket for checkpoints",
        "DYNAMO_TABLE": "DynamoDB table for metrics and job tracking",
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


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Train a Receipt Trainer model.")
    parser.add_argument("--run_name", type=str, default=None, help="Name of the run.")
    parser.add_argument(
        "--model_name",
        type=str,
        default="microsoft/layoutlm-base-uncased",
        help="Name of the model to train.",
    )
    parser.add_argument(
        "--batch_size", type=int, default=8, help="Batch size for training."
    )
    parser.add_argument(
        "--learning_rate",
        type=float,
        default=2e-5,
        help="Learning rate for training.",
    )
    parser.add_argument(
        "--num_train_epochs",
        type=int,
        default=10,
        help="Number of training epochs.",
    )
    parser.add_argument(
        "--warmup_ratio",
        type=float,
        default=0.1,
        help="Warmup ratio for learning rate.",
    )
    parser.add_argument(
        "--weight_decay",
        type=float,
        default=0.01,
        help="Weight decay for training.",
    )
    parser.add_argument(
        "--max_steps",
        type=int,
        default=None,
        help="Maximum number of steps for training. If None, will train for num_train_epochs.",
    )
    parser.add_argument(
        "--train_dataset",
        type=str,
        default=None,
        help="Path to the training dataset.",
    )
    parser.add_argument(
        "--eval_dataset",
        type=str,
        default=None,
        help="Path to the evaluation dataset.",
    )
    parser.add_argument(
        "--model_path",
        type=str,
        default=None,
        help="Path to the model checkpoint.",
    )
    parser.add_argument(
        "--resume_from_checkpoint",
        type=str,
        default=None,
        help="Path to resume training from.",
    )
    parser.add_argument(
        "--dynamo_table",
        type=str,
        required=True,
        help="Name of the DynamoDB table to use.",
    )
    return parser.parse_args()


def main():
    """Main training function."""
    # Parse arguments
    args = parse_args()

    # Validate environment variables
    validate_environment()

    # Create training config with default values
    training_config = TrainingConfig()

    # Update training config with provided arguments
    training_config.batch_size = args.batch_size
    training_config.learning_rate = args.learning_rate
    training_config.num_epochs = args.num_train_epochs
    training_config.warmup_ratio = args.warmup_ratio
    training_config.weight_decay = args.weight_decay
    if args.max_steps is not None:
        training_config.max_steps = args.max_steps

    # Set evaluation and logging steps
    training_config.evaluation_steps = 50
    training_config.save_steps = 50
    training_config.logging_steps = 10

    # Create data config
    data_config = DataConfig(
        env="prod",  # Use production environment
        use_sroie=True,  # Use SROIE dataset by default
        balance_ratio=0.7,  # Default balance ratio
        augment=True,  # Enable data augmentation
    )

    # Create trainer with explicit DynamoDB table name
    trainer = ReceiptTrainer(
        model_name=args.model_name,
        training_config=training_config,
        data_config=data_config,
        dynamo_table=args.dynamo_table,  # Use the table name from arguments
    )

    try:
        # Initialize DynamoDB client explicitly
        trainer.initialize_dynamo()

        # Create metrics callback with job_service and job_id from the trainer
        metrics_callback = MetricsCallback(
            job_service=trainer.job_service, job_id=trainer.job_id
        )

        # Load and prepare data
        print("Loading and preparing data...")
        trainer.dataset = trainer.load_data()
        print(
            f"Loaded dataset with {len(trainer.dataset['train'])} training and "
            f"{len(trainer.dataset['validation'])} validation examples"
        )

        # Initialize model
        print("Initializing model...")
        trainer.initialize_model()

        # Configure training
        print("Configuring training...")
        trainer.configure_training()

        # Train the model
        print("Starting training...")
        trainer.train(
            enable_checkpointing=True,
            enable_early_stopping=True,
            resume_training=True if args.resume_from_checkpoint else False,
            callbacks=[metrics_callback],
        )
    except Exception as e:
        print(f"Training failed with error: {str(e)}")
        traceback.print_exc()


if __name__ == "__main__":
    main()

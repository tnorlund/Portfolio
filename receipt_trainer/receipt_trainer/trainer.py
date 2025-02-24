"""Main trainer class for Receipt Trainer."""

import os
import json
import tempfile
import logging
import torch
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union
from datasets import Dataset, DatasetDict, load_dataset, Value, Features, Sequence
from transformers import (
    LayoutLMForTokenClassification,
    LayoutLMTokenizerFast,
    TrainingArguments,
    DataCollatorForTokenClassification,
    EarlyStoppingCallback,
    Trainer,
    AutoModel,
    AutoTokenizer,
)
import wandb
from dynamo import DynamoClient
import random
import numpy as np
from sklearn.metrics import confusion_matrix, classification_report
import seaborn as sns
import matplotlib.pyplot as plt
from collections import defaultdict
from sklearn.metrics import precision_score, recall_score, f1_score

from receipt_trainer.config import TrainingConfig, DataConfig
from receipt_trainer.utils.data import (
    process_receipt_details,
    create_sliding_windows,
    balance_dataset,
    augment_example,
)
from receipt_trainer.utils.aws import get_dynamo_table, get_s3_bucket
from receipt_trainer.constants import REQUIRED_ENV_VARS
from receipt_trainer.version import __version__


class ReceiptTrainer:
    """A wrapper class for training LayoutLM models on receipt data."""

    def __init__(
        self,
        wandb_project: str,
        model_name: str,
        training_config: Optional[TrainingConfig] = None,
        data_config: Optional[DataConfig] = None,
        dynamo_table: Optional[str] = None,
    ):
        """Initialize the trainer.

        Args:
            wandb_project: Name of the W&B project
            model_name: Name/path of the pre-trained model
            training_config: Training configuration
            data_config: Data loading configuration
            dynamo_table: DynamoDB table name for data loading
        """
        self.logger = logging.getLogger(__name__)
        self._validate_env_vars()
        
        self.wandb_project = wandb_project
        self.model_name = model_name
        self.training_config = training_config or TrainingConfig()
        self.data_config = data_config or DataConfig()
        self.dynamo_table = dynamo_table
        
        # Initialize components as None
        self.tokenizer = None
        self.model = None
        self.dataset = None
        self.training_args = None
        self.wandb_run = None
        self.dynamo_client = None
        self.output_dir = None
        
        # Set device
        self.device = self._get_device()
        
        # Initialize checkpoint tracking
        self.last_checkpoint = None
        self.is_interrupted = False
        
        self.logger.info("ReceiptTrainer initialized")

    def _validate_env_vars(self):
        """Validate that all required environment variables are set."""
        missing_vars = [
            var for var, desc in REQUIRED_ENV_VARS.items() if not os.environ.get(var)
        ]
        if missing_vars:
            raise EnvironmentError(
                f"Missing required environment variables: {missing_vars}\n"
                f"Required variables and their purposes:\n"
                + "\n".join(
                    f"- {var}: {desc}" for var, desc in REQUIRED_ENV_VARS.items()
                )
            )

    def _setup_spot_interruption_handler(self):
        """Set up handler for spot instance interruption.
        
        This method sets up a signal handler for SIGTERM, which AWS sends
        2 minutes before interrupting a spot instance.
        """
        import signal
        
        def handle_sigterm(*args):
            """Handle SIGTERM signal from AWS."""
            self.logger.warning("Received SIGTERM - spot instance interruption imminent")
            self.is_interrupted = True
            
            # Save checkpoint if we're in the middle of training
            if self.model and self.training_args:
                self.logger.info("Saving emergency checkpoint...")
                checkpoint_dir = os.path.join(self.output_dir, "interrupt_checkpoint")
                self.save_checkpoint(checkpoint_dir)
                
                # Upload checkpoint to S3 if possible
                try:
                    self._upload_checkpoint_to_s3(checkpoint_dir)
                except Exception as e:
                    self.logger.error(f"Failed to upload checkpoint to S3: {e}")
            
            # Clean up W&B
            if self.wandb_run:
                self.wandb_run.finish()
        
        signal.signal(signal.SIGTERM, handle_sigterm)
        self.logger.info("Spot interruption handler configured")

    def save_checkpoint(self, checkpoint_dir: str):
        """Save a training checkpoint.
        
        Args:
            checkpoint_dir: Directory to save the checkpoint
        """
        if not self.model or not self.tokenizer:
            raise ValueError("Model and tokenizer must be initialized before saving checkpoint")
            
        os.makedirs(checkpoint_dir, exist_ok=True)
        
        # Save model and tokenizer
        self.model.save_pretrained(checkpoint_dir)
        self.tokenizer.save_pretrained(checkpoint_dir)
        
        # Save optimizer and scheduler states
        if hasattr(self, 'optimizer') and hasattr(self, 'scheduler'):
            torch.save(
                {
                    'optimizer_state_dict': self.optimizer.state_dict(),
                    'scheduler_state_dict': self.scheduler.state_dict() if self.scheduler else None,
                    'epoch': self.current_epoch,
                    'global_step': self.global_step,
                },
                os.path.join(checkpoint_dir, 'training_state.pt')
            )
        
        self.last_checkpoint = checkpoint_dir
        self.logger.info(f"Checkpoint saved to {checkpoint_dir}")

    def _upload_checkpoint_to_s3(self, checkpoint_dir: str):
        """Upload checkpoint to S3.
        
        Args:
            checkpoint_dir: Local directory containing checkpoint files
        """
        import boto3
        from botocore.exceptions import ClientError
        
        s3 = boto3.client('s3')
        bucket_name = os.getenv('CHECKPOINT_BUCKET')
        
        if not bucket_name:
            raise ValueError("CHECKPOINT_BUCKET environment variable not set")
            
        # Upload all files in checkpoint directory
        for root, _, files in os.walk(checkpoint_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_path = os.path.join(
                    'checkpoints',
                    self.wandb_run.id if self.wandb_run else 'latest',
                    file
                )
                
                try:
                    s3.upload_file(local_path, bucket_name, s3_path)
                except ClientError as e:
                    self.logger.error(f"Failed to upload {file} to S3: {e}")
                    raise

    def _download_checkpoint_from_s3(self, run_id: Optional[str] = None) -> Optional[str]:
        """Download checkpoint from S3.
        
        Args:
            run_id: W&B run ID to download checkpoint for. If None, gets latest.
            
        Returns:
            Path to downloaded checkpoint directory, or None if not found
        """
        import boto3
        from botocore.exceptions import ClientError
        
        s3 = boto3.client('s3')
        bucket_name = os.getenv('CHECKPOINT_BUCKET')
        
        if not bucket_name:
            raise ValueError("CHECKPOINT_BUCKET environment variable not set")
            
        # Create temporary directory for checkpoint
        checkpoint_dir = tempfile.mkdtemp()
        
        try:
            # List objects in checkpoint directory
            prefix = f"checkpoints/{run_id if run_id else 'latest'}/"
            response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
            
            if 'Contents' not in response:
                return None
                
            # Download all checkpoint files
            for obj in response['Contents']:
                local_path = os.path.join(checkpoint_dir, os.path.basename(obj['Key']))
                s3.download_file(bucket_name, obj['Key'], local_path)
                
            return checkpoint_dir
            
        except ClientError as e:
            self.logger.error(f"Failed to download checkpoint from S3: {e}")
            return None

    def resume_training(self, run_id: Optional[str] = None):
        """Resume training from the latest checkpoint.
        
        Args:
            run_id: Optional W&B run ID to resume from
        """
        # Try to download checkpoint from S3
        checkpoint_dir = self._download_checkpoint_from_s3(run_id)
        
        if not checkpoint_dir:
            self.logger.warning("No checkpoint found in S3, starting fresh training")
            return
            
        # Load model and tokenizer from checkpoint
        self.model = AutoModel.from_pretrained(checkpoint_dir)
        self.tokenizer = AutoTokenizer.from_pretrained(checkpoint_dir)
        
        # Load training state if available
        training_state_path = os.path.join(checkpoint_dir, 'training_state.pt')
        if os.path.exists(training_state_path):
            training_state = torch.load(training_state_path)
            self.optimizer.load_state_dict(training_state['optimizer_state_dict'])
            if training_state['scheduler_state_dict'] and self.scheduler:
                self.scheduler.load_state_dict(training_state['scheduler_state_dict'])
            self.current_epoch = training_state['epoch']
            self.global_step = training_state['global_step']
            
        self.logger.info(f"Resumed training from checkpoint at step {self.global_step}")

    def train(
        self,
        enable_checkpointing: bool = True,
        enable_early_stopping: bool = True,
        log_to_wandb: bool = True,
        resume_training: bool = True
    ):
        """Train the model.

        Args:
            enable_checkpointing: Whether to save model checkpoints
            enable_early_stopping: Whether to enable early stopping
            log_to_wandb: Whether to log metrics to W&B
            resume_training: Whether to attempt to resume from checkpoint
        """
        self.logger.info("Starting training...")
        
        if not self.model or not self.training_args:
            raise ValueError("Training must be configured before starting training")
            
        # Set up spot interruption handling
        self._setup_spot_interruption_handler()
        
        # Initialize W&B if enabled
        if log_to_wandb and not self.wandb_run:
            self.initialize_wandb()
            
        # Try to resume training if requested
        if resume_training:
            self.resume_training()
            
        try:
            # Create trainer and start training
            trainer = self._create_trainer(enable_early_stopping)
            
            train_result = trainer.train(
                resume_from_checkpoint=self.last_checkpoint if enable_checkpointing else None
            )
            
            # Save final model if not interrupted
            if not self.is_interrupted and enable_checkpointing:
                trainer.save_model(self.output_dir)
                self.logger.info(f"Final model saved to {self.output_dir}")
                
            # Log metrics
            self._log_training_results(train_result)
            
            return train_result
            
        except Exception as e:
            self.logger.error(f"Training failed: {str(e)}")
            raise
            
        finally:
            # Cleanup
            if self.wandb_run:
                self.wandb_run.finish()
            torch.cuda.empty_cache()

    def initialize_model(self):
        """Initialize the LayoutLM model and tokenizer."""
        self.logger.info("Initializing model and tokenizer...")

        # Initialize tokenizer
        self.tokenizer = LayoutLMTokenizerFast.from_pretrained(self.model_name)

        # Initialize model (will be configured once we know the number of labels)
        self.model = None  # Will be initialized after data loading

        self.logger.info("Model and tokenizer initialized")

    def initialize_wandb(self, config: Optional[Dict[str, Any]] = None):
        """Initialize Weights & Biases for experiment tracking."""
        self.logger.info("Initializing W&B...")

        if config is None:
            config = {
                "model_name": self.model_name,
                "training": self.training_config.__dict__,
                "data": self.data_config.__dict__,
            }

        self.wandb_run = wandb.init(
            project=self.wandb_project,
            config=config,
            resume=True,
        )

        self.logger.info(f"W&B initialized: {self.wandb_run.name}")

    def initialize_dynamo(self):
        """Initialize DynamoDB client."""
        if self.dynamo_table:
            self.logger.info(
                f"Initializing DynamoDB client for table: {self.dynamo_table}"
            )
            self.dynamo_client = DynamoClient(self.dynamo_table)
        else:
            self.logger.warning("No DynamoDB table specified")

    def _load_dynamo_data(self) -> Dict[str, Any]:
        """Load receipt data from DynamoDB."""
        self.logger.info("Loading data from DynamoDB...")

        if not self.dynamo_client:
            self.initialize_dynamo()

        dataset = {}
        last_evaluated_key = None

        while True:
            receipt_details_dict, last_evaluated_key = (
                self.dynamo_client.listReceiptDetails(
                    last_evaluated_key=last_evaluated_key
                )
            )

            for key, details in receipt_details_dict.items():
                processed_data = process_receipt_details(details)
                if processed_data:
                    dataset[key] = processed_data

            if not last_evaluated_key:
                break

        self.logger.info(f"Loaded {len(dataset)} receipts from DynamoDB")
        return dataset

    def _load_sroie_data(self):
        """Load and prepare the SROIE dataset.
        
        Returns:
            Dictionary containing 'train' and 'test' splits of SROIE data
        """
        self.logger.info("Loading SROIE dataset...")

        # Define numeric to string mapping for SROIE labels
        sroie_idx_to_label = {
            0: "O",
            1: "B-COMPANY",
            2: "I-COMPANY",
            3: "B-DATE",
            4: "I-DATE",
            5: "B-ADDRESS",
            6: "I-ADDRESS",
            7: "B-TOTAL",
            8: "I-TOTAL",
        }

        # Define tag mapping from SROIE to our format
        sroie_to_our_format = {
            "O": "O",
            "B-COMPANY": "B-store_name",
            "I-COMPANY": "I-store_name",
            "B-ADDRESS": "B-address",
            "I-ADDRESS": "I-address",
            "B-DATE": "B-date",
            "I-DATE": "I-date",
            "B-TOTAL": "B-total_amount",
            "I-TOTAL": "I-total_amount",
        }

        dataset = load_dataset("darentang/sroie")
        train_data = []
        test_data = []

        def convert_example(
            example: Dict[str, Any], idx: int, split: str
        ) -> Dict[str, Any]:
            # First convert numeric labels to SROIE string labels, then to our format
            labels = [
                sroie_to_our_format[sroie_idx_to_label[label]]
                for label in example["ner_tags"]
            ]

            # Scale coordinates to 0-1000
            boxes = example["bboxes"]
            max_x = max(max(box[0], box[2]) for box in boxes)
            max_y = max(max(box[1], box[3]) for box in boxes)
            scale = 1000 / max(max_x, max_y)

            normalized_boxes = [
                [
                    int(box[0] * scale),
                    int(box[1] * scale),
                    int(box[2] * scale),
                    int(box[3] * scale),
                ]
                for box in boxes
            ]

            return {
                "words": example["words"],
                "bboxes": normalized_boxes,
                "labels": labels,
                "image_id": f"sroie_{split}_{idx}",
                "width": max_x,
                "height": max_y,
            }

        # Convert train split
        for idx, example in enumerate(dataset["train"]):
            train_data.append(convert_example(example, idx, "train"))

        # Convert test split
        for idx, example in enumerate(dataset["test"]):
            test_data.append(convert_example(example, idx, "test"))

        self.logger.info(
            f"Loaded {len(train_data)} training and {len(test_data)} test examples from SROIE"
        )
        return {"train": train_data, "test": test_data}

    def load_data(
        self,
        use_sroie: bool = True,
        balance_ratio: float = 0.7,
        augment: bool = True,
    ) -> DatasetDict:
        """Load and preprocess training data.

        Args:
            use_sroie: Whether to include SROIE dataset
            balance_ratio: Target ratio of entity tokens to total tokens
            augment: Whether to apply data augmentation

        Returns:
            DatasetDict containing train and validation splits
        """
        self.logger.info("Loading dataset...")
        self.logger.info(f"Use SROIE: {use_sroie}")
        self.logger.info(f"Balance ratio: {balance_ratio}")
        self.logger.info(f"Augment: {augment}")

        # Load data from DynamoDB
        self.logger.info("Loading data from DynamoDB...")
        examples = self._load_dynamo_data()
        self.logger.info(f"Loaded {len(examples)} receipts from DynamoDB")

        # Load SROIE dataset if requested
        if use_sroie:
            self.logger.info("Loading SROIE dataset...")
            sroie_examples = self._load_sroie_data()
            self.logger.info(
                f"Loaded {len(sroie_examples['train'])} training and {len(sroie_examples['test'])} test examples from SROIE"
            )
        else:
            sroie_examples = {"train": [], "test": []}

        # Balance dataset if requested and if we have examples
        if balance_ratio > 0 and examples:
            self.logger.info(f"Balancing dataset with target ratio {balance_ratio}...")
            examples = balance_dataset(examples, target_entity_ratio=balance_ratio)

        # Apply data augmentation if requested and if we have examples
        if augment and examples:
            self.logger.info("Applying data augmentation...")
            examples = augment_example(examples)

        # Create sliding windows
        self.logger.info(f"Creating sliding windows of size {self.data_config.window_size}...")
        train_windows = []
        if examples:  # Only process if we have examples
            for example in examples:
                windows = create_sliding_windows(
                    example["words"],
                    example["bboxes"],
                    example["labels"],
                    image_id=example.get("image_id"),  # Pass image_id if it exists
                    window_size=self.data_config.window_size,
                    overlap=self.data_config.window_overlap,
                )
                train_windows.extend(windows)

        # Process SROIE examples
        sroie_train_windows = []
        sroie_test_windows = []
        for split, examples in sroie_examples.items():
            for example in examples:
                windows = create_sliding_windows(
                    example["words"],
                    example["bboxes"],
                    example["labels"],
                    image_id=example.get("image_id"),  # Pass image_id if it exists
                    window_size=self.data_config.window_size,
                    overlap=self.data_config.window_overlap,
                )
                if split == "train":
                    sroie_train_windows.extend(windows)
                else:
                    sroie_test_windows.extend(windows)

        # Combine all examples
        train_windows.extend(sroie_train_windows)
        
        # Create datasets
        train_dataset = Dataset.from_list(train_windows, features=Features({
            "words": Sequence(Value("string")),
            "bboxes": Sequence(Sequence(Value("int64"))),
            "labels": Sequence(Value("string")),
            "image_id": Value("string"),
        }))
        val_dataset = Dataset.from_list(sroie_test_windows, features=Features({
            "words": Sequence(Value("string")),
            "bboxes": Sequence(Sequence(Value("int64"))),
            "labels": Sequence(Value("string")),
            "image_id": Value("string"),
        }))

        # Print statistics
        self._print_dataset_statistics(train_dataset, "Train")
        self._print_dataset_statistics(val_dataset, "Validation")

        return DatasetDict({"train": train_dataset, "validation": val_dataset})

    def _print_dataset_statistics(self, dataset: Dataset, split_name: str):
        """Print statistics about the loaded dataset."""
        if not dataset:
            return

        total_words = sum(len(words) for words in dataset["words"])
        label_counts = {}
        for labels in dataset["labels"]:
            for label in labels:
                label_counts[label] = label_counts.get(label, 0) + 1

        self.logger.info(f"\n{split_name.capitalize()} Split Statistics:")
        self.logger.info(f"Total documents: {len(dataset)}")
        self.logger.info(f"Total words: {total_words}")
        self.logger.info("\nLabel distribution:")
        for label, count in sorted(label_counts.items()):
            percentage = (count / total_words) * 100
            self.logger.info(f"{label}: {count} ({percentage:.2f}%)")

        if self.wandb_run:
            # Log statistics to W&B
            self.wandb_run.log(
                {
                    f"{split_name}/total_documents": len(dataset),
                    f"{split_name}/total_words": total_words,
                    **{
                        f"{split_name}/label_{label}": count
                        for label, count in label_counts.items()
                    },
                }
            )

    def configure_training(
        self,
        output_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        """Configure training parameters.

        Args:
            output_dir: Directory to save model checkpoints
            **kwargs: Additional training configuration parameters
        """
        self.logger.info("Configuring training...")

        if not self.dataset:
            raise ValueError("Dataset must be loaded before configuring training")

        if not self.tokenizer:
            raise ValueError("Model and tokenizer must be initialized before configuring training")

        # Update training config with any provided kwargs
        for key, value in kwargs.items():
            if hasattr(self.training_config, key):
                setattr(self.training_config, key, value)
            else:
                self.logger.warning(f"Unknown training config parameter: {key}")

        # Set up output directory
        self.output_dir = output_dir or os.path.join(self.data_config.cache_dir, "checkpoints")
        os.makedirs(self.output_dir, exist_ok=True)

        # Get unique labels from dataset
        unique_labels = set()
        for split in self.dataset.values():
            for label_sequence in split["labels"]:
                unique_labels.update(label_sequence)
        self.label_list = sorted(list(unique_labels))
        self.num_labels = len(self.label_list)
        self.label2id = {label: i for i, label in enumerate(self.label_list)}
        self.id2label = {i: label for label, i in self.label2id.items()}

        # Initialize model with correct number of labels
        self.model = LayoutLMForTokenClassification.from_pretrained(
            self.model_name,
            num_labels=self.num_labels,
            label2id=self.label2id,
            id2label=self.id2label,
        )
        self.model.to(self.device)

        # Create training arguments
        self.training_args = TrainingArguments(
            output_dir=self.output_dir,
            num_train_epochs=self.training_config.num_epochs,
            per_device_train_batch_size=self.training_config.batch_size,
            gradient_accumulation_steps=self.training_config.gradient_accumulation_steps,
            learning_rate=self.training_config.learning_rate,
            weight_decay=self.training_config.weight_decay,
            max_grad_norm=self.training_config.max_grad_norm,
            warmup_ratio=self.training_config.warmup_ratio,
            bf16=self.training_config.bf16 and self.device == "cuda",
            evaluation_strategy="steps",
            eval_steps=self.training_config.evaluation_steps,
            save_strategy="steps",
            save_steps=self.training_config.save_steps,
            logging_steps=self.training_config.logging_steps,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
        )

        self.logger.info("Training configuration complete")
        self.logger.info(f"Number of labels: {self.num_labels}")
        self.logger.info(f"Labels: {', '.join(self.label_list)}")
        self.logger.info(f"Output directory: {self.output_dir}")
        self.logger.info(f"Device: {self.device}")
        self.logger.info(f"Training arguments: {self.training_args}")

    def _create_trainer(self, enable_early_stopping: bool):
        """Create and configure the Trainer object."""
        # Create data collator
        data_collator = DataCollatorForTokenClassification(
            self.tokenizer,
            pad_to_multiple_of=8 if self.training_config.bf16 else None
        )

        # Setup early stopping if enabled
        callbacks = []
        if enable_early_stopping:
            callbacks.append(
                EarlyStoppingCallback(
                    early_stopping_patience=self.training_config.early_stopping_patience
                )
            )

        # Create Trainer
        trainer = Trainer(
            model=self.model,
            args=self.training_args,
            train_dataset=self.dataset["train"],
            eval_dataset=self.dataset["validation"],
            data_collator=data_collator,
            tokenizer=self.tokenizer,
            callbacks=callbacks,
        )

        return trainer

    def _log_training_results(self, train_result):
        """Log training results to W&B and print summary."""
        # Run detailed evaluation on both splits
        eval_output_dir = os.path.join(self.output_dir, "eval")
        train_metrics = self.evaluate("train", eval_output_dir)
        val_metrics = self.evaluate("validation", eval_output_dir)

        # Log metrics
        metrics = {
            **train_metrics,
            **val_metrics,
            "train/total_steps": train_result.global_step,
            "train/total_loss": train_result.training_loss,
        }

        if self.wandb_run:
            self.wandb_run.log(metrics)

        self.logger.info("Training complete")
        self.logger.info(f"Total steps: {train_result.global_step}")
        self.logger.info(f"Average training loss: {train_result.training_loss:.4f}")
        self.logger.info(f"Train Macro F1: {train_metrics['train/macro_avg/f1-score']:.4f}")
        self.logger.info(f"Validation Macro F1: {val_metrics['validation/macro_avg/f1-score']:.4f}")

    def evaluate(
        self,
        split: str = "validation",
        output_dir: Optional[str] = None,
        detailed_report: bool = True,
    ) -> Dict[str, float]:
        """Evaluate the model on a specific dataset split.

        Args:
            split: Dataset split to evaluate on ("train" or "validation")
            output_dir: Directory to save evaluation artifacts (plots, reports)
            detailed_report: Whether to generate detailed performance analysis

        Returns:
            Dictionary containing evaluation metrics
        """
        self.logger.info(f"Starting evaluation on {split} split...")

        if not self.model or not self.dataset:
            raise ValueError("Model and dataset must be initialized before evaluation")

        if split not in self.dataset:
            raise ValueError(f"Dataset split '{split}' not found")

        # Create output directory if needed
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Create trainer for evaluation
        trainer = Trainer(
            model=self.model,
            args=self.training_args,
            tokenizer=self.tokenizer,
            data_collator=DataCollatorForTokenClassification(
                self.tokenizer,
                pad_to_multiple_of=8 if self.training_config.bf16 else None
            ),
        )

        # Run prediction
        self.logger.info("Running predictions...")
        predictions = trainer.predict(self.dataset[split])
        
        # Convert predictions to labels
        logits = predictions.predictions
        pred_labels = np.argmax(logits, axis=2)
        
        # Get true labels
        true_labels = predictions.label_ids

        # Flatten predictions and labels, removing padding (-100)
        true_flat = []
        pred_flat = []
        for i in range(len(true_labels)):
            for j in range(len(true_labels[i])):
                if true_labels[i][j] != -100:
                    true_flat.append(self.id2label[true_labels[i][j]])
                    pred_flat.append(self.id2label[pred_labels[i][j]])

        # Compute metrics
        metrics = {}
        
        # Get classification report
        report = classification_report(
            true_flat,
            pred_flat,
            output_dict=True,
            zero_division=0
        )

        # Extract metrics per label
        for label, stats in report.items():
            if isinstance(stats, dict):
                for metric, value in stats.items():
                    metrics[f"{split}/{label}/{metric}"] = value

        # Add macro and weighted averages
        for avg_type in ["macro avg", "weighted avg"]:
            if avg_type in report:
                for metric, value in report[avg_type].items():
                    metrics[f"{split}/{avg_type.replace(' ', '_')}/{metric}"] = value

        if detailed_report:
            # Generate confusion matrix
            labels = sorted(list(set(true_flat)))
            cm = confusion_matrix(true_flat, pred_flat, labels=labels)
            
            # Plot confusion matrix
            plt.figure(figsize=(12, 10))
            sns.heatmap(
                cm,
                annot=True,
                fmt="d",
                cmap="Blues",
                xticklabels=labels,
                yticklabels=labels
            )
            plt.title(f"Confusion Matrix - {split.capitalize()} Split")
            plt.xlabel("Predicted")
            plt.ylabel("True")
            
            # Save plot
            if output_dir:
                plt.savefig(os.path.join(output_dir, f"confusion_matrix_{split}.png"))
                plt.close()

            # Add per-document analysis
            doc_metrics = self._compute_document_metrics(
                self.dataset[split],
                predictions.predictions,
                predictions.label_ids
            )
            metrics.update(doc_metrics)

        # Log metrics to W&B
        if self.wandb_run:
            # Log metrics
            self.wandb_run.log(metrics)
            
            # Log confusion matrix plot if generated
            if detailed_report and output_dir:
                self.wandb_run.log({
                    f"{split}/confusion_matrix": wandb.Image(
                        os.path.join(output_dir, f"confusion_matrix_{split}.png")
                    )
                })

        # Print summary
        self.logger.info("\nEvaluation Results:")
        self.logger.info(f"Split: {split}")
        self.logger.info(f"Macro F1: {metrics[f'{split}/macro_avg/f1-score']:.4f}")
        self.logger.info(f"Weighted F1: {metrics[f'{split}/weighted_avg/f1-score']:.4f}")
        self.logger.info("\nPer-Label Performance:")
        for label in self.label_list:
            if label != "O":  # Skip the 'Outside' label in summary
                f1 = metrics.get(f"{split}/{label}/f1-score", 0)
                support = metrics.get(f"{split}/{label}/support", 0)
                self.logger.info(f"{label:15} F1: {f1:.4f} (n={support})")

        return metrics

    def _compute_document_metrics(
        self,
        dataset: Dataset,
        predictions: np.ndarray,
        true_labels: np.ndarray,
    ) -> Dict[str, float]:
        """Compute document-level metrics.
        
        Args:
            dataset: The dataset split being evaluated
            predictions: Model predictions (logits)
            true_labels: True labels

        Returns:
            Dictionary of document-level metrics
        """
        metrics = {}
        pred_labels = np.argmax(predictions, axis=2)

        # Group by document
        doc_metrics = defaultdict(list)
        current_doc = None
        doc_true = []
        doc_pred = []

        for i, image_id in enumerate(dataset["image_id"]):
            if current_doc != image_id:
                if doc_true:
                    # Compute metrics for previous document
                    for label in self.label_list:
                        if label != "O":
                            label_true = [l == label for l in doc_true]
                            label_pred = [l == label for l in doc_pred]
                            if any(label_true):  # Only compute if label exists in document
                                precision = precision_score(label_true, label_pred, zero_division=0)
                                recall = recall_score(label_true, label_pred, zero_division=0)
                                f1 = f1_score(label_true, label_pred, zero_division=0)
                                doc_metrics[label].append((precision, recall, f1))
                
                # Reset for new document
                current_doc = image_id
                doc_true = []
                doc_pred = []

            # Add predictions for current document
            for j in range(len(true_labels[i])):
                if true_labels[i][j] != -100:
                    doc_true.append(self.id2label[true_labels[i][j]])
                    doc_pred.append(self.id2label[pred_labels[i][j]])

        # Compute average metrics per label across documents
        for label, scores in doc_metrics.items():
            avg_precision = np.mean([s[0] for s in scores])
            avg_recall = np.mean([s[1] for s in scores])
            avg_f1 = np.mean([s[2] for s in scores])
            metrics[f"doc_avg/{label}/precision"] = avg_precision
            metrics[f"doc_avg/{label}/recall"] = avg_recall
            metrics[f"doc_avg/{label}/f1-score"] = avg_f1

        return metrics

    def _get_device(self):
        """Get the default device for training."""
        if torch.cuda.is_available():
            return "cuda"
        elif torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def run_hyperparameter_sweep(
        self,
        sweep_config: Optional[Dict[str, Any]] = None,
        num_trials: int = 10,
    ) -> str:
        """Run hyperparameter optimization using W&B sweeps.
        
        Args:
            sweep_config: Optional custom sweep configuration. If None, uses default config.
            num_trials: Number of trials to run in the sweep.
            
        Returns:
            ID of the best performing sweep run.
        """
        self.logger.info("Setting up hyperparameter sweep...")
        
        if not sweep_config:
            # Default sweep configuration targeting key hyperparameters
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
                    },
                    "max_grad_norm": {
                        "values": [0.5, 1.0, 2.0]
                    },
                    "early_stopping_patience": {
                        "values": [3, 5, 7]
                    }
                }
            }
        
        # Initialize sweep
        sweep_id = wandb.sweep(sweep_config, project=self.wandb_project)
        self.logger.info(f"Created sweep with ID: {sweep_id}")
        
        def train_sweep():
            """Training function for each sweep run."""
            # Initialize a new W&B run
            with wandb.init() as run:
                # Update training config with sweep parameters
                sweep_params = run.config
                for key, value in sweep_params.items():
                    if hasattr(self.training_config, key):
                        setattr(self.training_config, key, value)
                
                # Configure training with updated parameters
                self.configure_training()
                
                # Train the model
                train_result = self.train(
                    enable_checkpointing=True,
                    enable_early_stopping=True,
                    log_to_wandb=True
                )
                
                # Log final metrics
                metrics = self.evaluate("validation")
                run.log(metrics)
                
                # Save best model if this is the best run so far
                if run.summary.get("validation/macro_avg/f1-score", 0) == wandb.run.best_metric:
                    self.save_model(os.path.join(self.output_dir, "best_model"))
        
        # Run the sweep
        self.logger.info(f"Starting sweep with {num_trials} trials...")
        wandb.agent(sweep_id, function=train_sweep, count=num_trials)
        
        # Get best run ID
        api = wandb.Api()
        sweep = api.sweep(f"{self.wandb_project}/{sweep_id}")
        best_run = sweep.best_run()
        
        self.logger.info(f"Sweep completed. Best run: {best_run.id}")
        self.logger.info(f"Best validation F1: {best_run.summary.get('validation/macro_avg/f1-score', 0):.4f}")
        
        return best_run.id

    def save_model(self, output_path: str):
        """Save the trained model and tokenizer.
        
        Args:
            output_path: Path where to save the model
        
        Raises:
            ValueError: If model or tokenizer is not initialized
        """
        if not self.model or not self.tokenizer:
            raise ValueError("Model and tokenizer must be initialized before saving")
        
        self.logger.info(f"Saving model to {output_path}")
        
        # Create output directory if it doesn't exist
        os.makedirs(output_path, exist_ok=True)
        
        # Save model
        self.model.save_pretrained(output_path)
        
        # Save tokenizer
        self.tokenizer.save_pretrained(output_path)
        
        # Save label mappings
        if hasattr(self, 'label_list'):
            label_config = {
                'label_list': self.label_list,
                'label2id': self.label2id,
                'id2label': self.id2label,
                'num_labels': self.num_labels
            }
            with open(os.path.join(output_path, 'label_config.json'), 'w') as f:
                json.dump(label_config, f)
        
        self.logger.info("Model saved successfully")

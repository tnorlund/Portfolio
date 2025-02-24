"""Main trainer class for Receipt Trainer."""

import os
import json
import tempfile
import logging
import torch
from pathlib import Path
from typing import Optional, Dict, Any, List
from datasets import Dataset, DatasetDict, load_dataset
from transformers import (
    LayoutLMForTokenClassification,
    LayoutLMTokenizerFast,
    TrainingArguments,
    DataCollatorForTokenClassification,
    EarlyStoppingCallback,
    Trainer,
    AutoModel,
)
import wandb
from dynamo import DynamoClient

from receipt_trainer.config import TrainingConfig, DataConfig
from receipt_trainer.utils.data import process_receipt_details, create_sliding_windows
from receipt_trainer.utils.aws import get_dynamo_table, get_s3_bucket
from receipt_trainer.constants import REQUIRED_ENV_VARS
from receipt_trainer.version import __version__


class ReceiptTrainer:
    """A wrapper class for training LayoutLM models on receipt data."""

    def __init__(
        self,
        wandb_project: str,
        model_name: str = "microsoft/layoutlm-base-uncased",
        dynamo_table: Optional[str] = None,
        s3_bucket: Optional[str] = None,
        training_config: Optional[TrainingConfig] = None,
        data_config: Optional[DataConfig] = None,
        device: Optional[str] = None,
    ):
        """Initialize the ReceiptTrainer."""
        # Setup logging first for other initialization steps
        self.logger = logging.getLogger("ReceiptTrainer")
        self.logger.setLevel(logging.INFO)

        # Create console handler if none exists
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

        # First, validate environment variables
        self._validate_env_vars()

        self.wandb_project = wandb_project
        self.model_name = model_name
        self.s3_bucket = s3_bucket

        # Set configurations
        self.training_config = training_config or TrainingConfig()
        self.data_config = data_config or DataConfig()

        # Create cache directory if needed
        if not self.data_config.cache_dir:
            self.data_config.cache_dir = os.path.join(
                tempfile.gettempdir(), "receipt_trainer"
            )
        os.makedirs(self.data_config.cache_dir, exist_ok=True)

        # Get DynamoDB table name from Pulumi stack if not provided
        self.dynamo_table = get_dynamo_table(dynamo_table, self.data_config.env)

        # Initialize device
        self.device = device or self._get_default_device()

        # Initialize components as None
        self.model = None
        self.tokenizer = None
        self.dataset = None
        self.label_map = None
        self.dynamo_client = None
        self.wandb_run = None

        self.logger.info(f"Initialized ReceiptTrainer with device: {self.device}")
        self.logger.info(f"Model: {self.model_name}")
        self.logger.info(f"W&B Project: {self.wandb_project}")
        self.logger.info(f"DynamoDB Table: {self.dynamo_table}")
        self.logger.info(f"Environment: {self.data_config.env}")
        self.logger.info(f"Cache directory: {self.data_config.cache_dir}")

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

    def _setup_logging(self):
        """Setup logging configuration."""
        self.logger = logging.getLogger("ReceiptTrainer")
        self.logger.setLevel(logging.INFO)

        # Create console handler
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)

        # Create formatter
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)

        # Add handler to logger
        self.logger.addHandler(handler)

    def _get_default_device(self) -> str:
        """Get the default device for training."""
        if torch.cuda.is_available():
            return "cuda"
        elif torch.backends.mps.is_available():
            return "mps"
        return "cpu"

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
        """Load and prepare the SROIE dataset."""
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
        train_data = {}
        test_data = {}

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
            train_data[f"sroie_train_{idx}"] = convert_example(example, idx, "train")

        # Convert test split
        for idx, example in enumerate(dataset["test"]):
            test_data[f"sroie_test_{idx}"] = convert_example(example, idx, "test")

        self.logger.info(
            f"Loaded {len(train_data)} training and {len(test_data)} test examples from SROIE"
        )
        return train_data, test_data

    def load_data(
        self,
        use_sroie: Optional[bool] = None,
        balance_ratio: Optional[float] = None,
        augment: Optional[bool] = None,
    ) -> DatasetDict:
        """Load and prepare the complete dataset.

        Args:
            use_sroie: Whether to include SROIE dataset
            balance_ratio: Ratio for dataset balancing
            augment: Whether to apply data augmentation

        Returns:
            DatasetDict containing train and validation splits
        """
        # Update config with any provided parameters
        if use_sroie is not None:
            self.data_config.use_sroie = use_sroie
        if balance_ratio is not None:
            self.data_config.balance_ratio = balance_ratio
        if augment is not None:
            self.data_config.augment = augment

        self.logger.info("Starting dataset loading process...")

        # Load DynamoDB data
        dynamo_data = self._load_dynamo_data()

        # Load SROIE data if requested
        sroie_train_data = {}
        sroie_test_data = {}
        if self.data_config.use_sroie:
            sroie_train_data, sroie_test_data = self._load_sroie_data()

        # Combine datasets
        all_train_data = {**dynamo_data, **sroie_train_data}

        # Convert to Dataset format
        train_dataset = Dataset.from_dict(
            {
                "words": [example["words"] for example in all_train_data.values()],
                "bbox": [example["bboxes"] for example in all_train_data.values()],
                "labels": [example["labels"] for example in all_train_data.values()],
                "image_id": [
                    example["image_id"] for example in all_train_data.values()
                ],
            }
        )

        val_dataset = Dataset.from_dict(
            {
                "words": [example["words"] for example in sroie_test_data.values()],
                "bbox": [example["bboxes"] for example in sroie_test_data.values()],
                "labels": [example["labels"] for example in sroie_test_data.values()],
                "image_id": [
                    example["image_id"] for example in sroie_test_data.values()
                ],
            }
        )

        # Create the final dataset dictionary
        self.dataset = DatasetDict({"train": train_dataset, "validation": val_dataset})

        # Log dataset statistics
        self._log_dataset_statistics()

        return self.dataset

    def _log_dataset_statistics(self):
        """Log statistics about the loaded dataset."""
        if not self.dataset:
            return

        for split_name, split_dataset in self.dataset.items():
            total_words = sum(len(words) for words in split_dataset["words"])
            label_counts = {}
            for labels in split_dataset["labels"]:
                for label in labels:
                    label_counts[label] = label_counts.get(label, 0) + 1

            self.logger.info(f"\n{split_name.capitalize()} Split Statistics:")
            self.logger.info(f"Total documents: {len(split_dataset)}")
            self.logger.info(f"Total words: {total_words}")
            self.logger.info("\nLabel distribution:")
            for label, count in sorted(label_counts.items()):
                percentage = (count / total_words) * 100
                self.logger.info(f"{label}: {count} ({percentage:.2f}%)")

            if self.wandb_run:
                # Log statistics to W&B
                self.wandb_run.log(
                    {
                        f"{split_name}/total_documents": len(split_dataset),
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

    def train(
        self,
        enable_checkpointing: bool = True,
        enable_early_stopping: bool = True,
        log_to_wandb: bool = True,
    ):
        """Train the model.

        Args:
            enable_checkpointing: Whether to save model checkpoints
            enable_early_stopping: Whether to enable early stopping
            log_to_wandb: Whether to log metrics to W&B
        """
        self.logger.info("Starting training...")

        if not self.model or not self.training_args:
            raise ValueError("Training must be configured before starting training")

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

        # Initialize W&B if enabled
        if log_to_wandb and not self.wandb_run:
            self.initialize_wandb()

        try:
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

            # Start training
            train_result = trainer.train(
                resume_from_checkpoint=enable_checkpointing
            )

            # Save final model
            if enable_checkpointing:
                trainer.save_model(self.output_dir)
                self.logger.info(f"Final model saved to {self.output_dir}")

            # Log metrics
            metrics = train_result.metrics
            trainer.log_metrics("train", metrics)
            trainer.save_metrics("train", metrics)

            # Run final evaluation
            eval_metrics = trainer.evaluate()
            trainer.log_metrics("eval", eval_metrics)
            trainer.save_metrics("eval", eval_metrics)

            self.logger.info("Training complete")
            self.logger.info(f"Final training metrics: {metrics}")
            self.logger.info(f"Final evaluation metrics: {eval_metrics}")

            return train_result

        except Exception as e:
            self.logger.error(f"Training failed: {str(e)}")
            raise

        finally:
            # Cleanup
            if self.wandb_run:
                self.wandb_run.finish()
            torch.cuda.empty_cache()

    def save_model(self, output_path: str):
        """Save the trained model, tokenizer, and configuration.

        Args:
            output_path: Path to save the model
        """
        self.logger.info(f"Saving model to {output_path}...")

        if not self.model or not self.tokenizer:
            raise ValueError("Model and tokenizer must be initialized before saving")

        try:
            # Create output directory
            os.makedirs(output_path, exist_ok=True)

            # Save model
            self.model.save_pretrained(output_path)
            self.logger.info("Model saved successfully")

            # Save tokenizer
            self.tokenizer.save_pretrained(output_path)
            self.logger.info("Tokenizer saved successfully")

            # Save label mappings
            label_config = {
                "label_list": self.label_list,
                "label2id": self.label2id,
                "id2label": self.id2label,
                "num_labels": self.num_labels,
            }
            with open(os.path.join(output_path, "label_config.json"), "w") as f:
                json.dump(label_config, f, indent=2)
            self.logger.info("Label configuration saved successfully")

            # Save training configuration
            config = {
                "model_name": self.model_name,
                "training": self.training_config.__dict__,
                "data": self.data_config.__dict__,
                "device": self.device,
                "version": __version__,
            }
            with open(os.path.join(output_path, "config.json"), "w") as f:
                json.dump(config, f, indent=2)
            self.logger.info("Training configuration saved successfully")

            # Validate saved model
            try:
                loaded_model = AutoModel.from_pretrained(output_path)
                del loaded_model  # Free memory
                self.logger.info("Saved model validated successfully")
            except Exception as e:
                self.logger.error(f"Model validation failed: {str(e)}")
                raise ValueError("Saved model validation failed") from e

        except Exception as e:
            self.logger.error(f"Failed to save model: {str(e)}")
            raise

        finally:
            torch.cuda.empty_cache()

        self.logger.info(f"Model successfully saved to {output_path}")

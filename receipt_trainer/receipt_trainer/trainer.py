"""Main trainer class for Receipt Trainer."""

import os
import json
import tempfile
import logging
import torch
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from sklearn.metrics import (
    confusion_matrix,
    precision_recall_fscore_support,
    accuracy_score,
)

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
    TrainerCallback,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.services.job_service import JobService
import random
import seaborn as sns
from collections import defaultdict
from sklearn.metrics import (
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report,
)
import threading
import time
import glob
from receipt_trainer.utils.aws import get_dynamo_table
import traceback
import uuid
from datetime import datetime

# Set tokenizer parallelism to false to avoid deadlocks
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from receipt_trainer.config import TrainingConfig, DataConfig
from receipt_trainer.utils.data import (
    process_receipt_details,
    create_sliding_windows,
    balance_dataset,
    augment_example,
)
from receipt_trainer.constants import REQUIRED_ENV_VARS
from receipt_trainer.version import __version__


class DynamoMetricsCallback(TrainerCallback):
    """Custom callback to log metrics to DynamoDB during training."""

    def __init__(self, job_service, job_id):
        """Initialize the callback.
        
        Args:
            job_service: JobService instance for logging metrics
            job_id: ID of the training job
        """
        super().__init__()
        self.trainer = None
        self.step = 0
        self.job_service = job_service
        self.job_id = job_id
        print(f"DynamoMetricsCallback initialized for job: {job_id}")

    def setup(self, trainer):
        """Set up the callback with a trainer instance."""
        self.trainer = trainer
        print(f"DynamoMetricsCallback setup complete with trainer: {trainer}")

    def on_train_begin(self, args, state, control, **kwargs):
        """Called when training begins."""
        if "trainer" in kwargs:
            self.trainer = kwargs["trainer"]
        print("Training started - DynamoMetricsCallback initialized")
        
        # Log training start
        self.job_service.add_job_status(
            self.job_id, 
            "TRAINING", 
            "Training started"
        )
        
        if not self.trainer:
            print("Warning: Trainer not available in on_train_begin")

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        """Log metrics after each evaluation."""
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

            # Prepare metrics dictionary
            metric_dict = {
                "train/global_step": self.step,
                "eval/accuracy": float(accuracy),
                "eval/f1": float(np.mean(f1)),
                "eval/loss": float(metrics.get("eval_loss", 0.0)),
            }

            # Add per-label metrics
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
                self.job_service.add_job_metric(
                    job_id=self.job_id,
                    metric_name=key,
                    metric_value=value,
                    step=self.step,
                    metadata={"type": "evaluation"}
                )

            # Create confusion matrix
            cm = confusion_matrix(true_flat, pred_flat, labels=labels)
            
            # Log confusion matrix as a metric
            # Convert the confusion matrix to a serializable format
            cm_data = {
                "matrix": cm.tolist(),
                "labels": label_names,
            }
            
            # Store confusion matrix as a complex metric
            self.job_service.add_job_metric(
                job_id=self.job_id,
                metric_name="eval/confusion_matrix",
                metric_value=cm_data,
                step=self.step,
                metadata={"type": "confusion_matrix"}
            )

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
            
            # Log the error to DynamoDB
            self.job_service.add_job_log(
                job_id=self.job_id,
                log_level="ERROR",
                message=f"Error during evaluation: {str(e)}\n{traceback.format_exc()}"
            )


class ReceiptTrainer:
    """A wrapper class for training LayoutLM models on receipt data."""

    def __init__(
        self,
        model_name: str,
        training_config: Optional[TrainingConfig] = None,
        data_config: Optional[DataConfig] = None,
        dynamo_table: Optional[str] = None,
    ):
        """Initialize the trainer.

        Args:
            model_name: Name/path of the pre-trained model.
            training_config: Training configuration.
            data_config: Data loading configuration.
            dynamo_table: DynamoDB table name for data loading (optional; will try to load from Pulumi if not provided).
        """
        self.logger = logging.getLogger(__name__)
        self._validate_env_vars()

        self.model_name = model_name
        self.training_config = training_config or TrainingConfig()
        self.data_config = data_config or DataConfig()

        # Initialize job tracking
        self.job_id = str(uuid.uuid4())
        self.job_service = None

        # Initialize other components as None.
        self.tokenizer = None
        self.model = None
        self.dataset = None
        self.training_args = None
        self.dynamo_client = None
        self.output_dir = None

        # Set the training device.
        self.device = self._get_device()

        # Initialize checkpoint tracking.
        self.last_checkpoint = None
        self.is_interrupted = False

        # Ensure we have a valid cache directory.
        if not self.data_config.cache_dir:
            self.data_config.cache_dir = os.path.join(
                os.path.expanduser("~/.cache"), "receipt_trainer"
            )
            self.logger.info(
                f"No cache directory specified, using default: {self.data_config.cache_dir}"
            )
        os.makedirs(self.data_config.cache_dir, exist_ok=True)

        # Initialize DynamoDB table name and client.
        try:
            self.dynamo_table = dynamo_table or get_dynamo_table(
                env=self.data_config.env
            )
            self.logger.info(f"Using DynamoDB table: {self.dynamo_table}")
            # Initialize DynamoDB client immediately to fail fast if there are issues.
            self.dynamo_client = DynamoClient(self.dynamo_table)
            
            # Initialize JobService for metrics tracking
            self.job_service = JobService(self.dynamo_table)
            
            self.logger.info("Successfully initialized DynamoDB client and JobService")
        except Exception as e:
            self.logger.error(f"Failed to initialize DynamoDB: {e}")
            raise ValueError(
                f"Failed to initialize DynamoDB. Please ensure your Pulumi stack '{self.data_config.env}' "
                f"is properly configured and accessible. Error: {str(e)}"
            )

        # Create a new training job record
        try:
            self.create_training_job()
        except Exception as e:
            self.logger.error(f"Failed to create training job record: {e}")
            
        self.logger.info("ReceiptTrainer initialized")
    
    def create_training_job(self):
        """Create a new training job record in DynamoDB."""
        if not self.job_service:
            self.logger.warning("JobService not initialized, skipping job creation")
            return
            
        job_config = {
            "model_name": self.model_name,
            "training_config": {k: str(v) for k, v in vars(self.training_config).items()},
            "data_config": {k: str(v) for k, v in vars(self.data_config).items()},
            "device": str(self.device),
            "version": __version__
        }
        
        self.job_service.create_job(
            job_id=self.job_id,
            name=f"LayoutLM Training Job - {self.model_name}",
            description=f"Training LayoutLM model for receipt OCR",
            created_by="receipt_trainer",
            status="INITIALIZED",
            priority="HIGH",
            job_config=job_config,
        )
        
        self.logger.info(f"Created training job with ID: {self.job_id}")
        
        # Log initialization status
        self.job_service.add_job_status(
            self.job_id,
            "INITIALIZED",
            "Training job initialized"
        )
        
        # Log initial message
        self.job_service.add_job_log(
            self.job_id,
            "INFO",
            f"Initialized training for model {self.model_name} on device {self.device}"
        )

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
            self.logger.warning(
                "Received SIGTERM - spot instance interruption imminent"
            )
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

            # Log the interruption to DynamoDB
            if self.job_service:
                try:
                    self.job_service.add_job_status(
                        self.job_id,
                        "INTERRUPTED",
                        "Spot instance interruption detected"
                    )
                    self.job_service.add_job_log(
                        self.job_id,
                        "WARNING",
                        "Training interrupted due to spot instance termination"
                    )
                except Exception as e:
                    self.logger.error(f"Failed to log interruption to DynamoDB: {e}")

        signal.signal(signal.SIGTERM, handle_sigterm)
        self.logger.info("Spot interruption handler configured")

    def save_checkpoint(self, checkpoint_dir: str):
        """Save a training checkpoint.

        Args:
            checkpoint_dir: Directory to save the checkpoint
        """
        if not self.model or not self.tokenizer:
            raise ValueError(
                "Model and tokenizer must be initialized before saving checkpoint"
            )

        os.makedirs(checkpoint_dir, exist_ok=True)

        # Save model and tokenizer
        self.model.save_pretrained(checkpoint_dir)
        self.tokenizer.save_pretrained(checkpoint_dir)

        # Save optimizer and scheduler states
        if hasattr(self, "optimizer") and hasattr(self, "scheduler"):
            torch.save(
                {
                    "optimizer_state_dict": self.optimizer.state_dict(),
                    "scheduler_state_dict": (
                        self.scheduler.state_dict() if self.scheduler else None
                    ),
                    "epoch": self.current_epoch,
                    "global_step": self.global_step,
                },
                os.path.join(checkpoint_dir, "training_state.pt"),
            )

        self.last_checkpoint = checkpoint_dir
        self.logger.info(f"Checkpoint saved to {checkpoint_dir}")
        
        # Log checkpoint to DynamoDB
        if self.job_service:
            try:
                checkpoint_metadata = {
                    "path": checkpoint_dir,
                    "global_step": self.global_step if hasattr(self, "global_step") else None,
                    "epoch": self.current_epoch if hasattr(self, "current_epoch") else None,
                }
                
                self.job_service.add_job_checkpoint(
                    self.job_id,
                    checkpoint_name=f"checkpoint_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    metadata=checkpoint_metadata
                )
                
                self.job_service.add_job_log(
                    self.job_id,
                    "INFO",
                    f"Checkpoint saved to {checkpoint_dir}"
                )
            except Exception as e:
                self.logger.error(f"Failed to log checkpoint to DynamoDB: {e}")

    def _upload_checkpoint_to_s3(self, checkpoint_dir: str):
        """Upload checkpoint to S3.

        Args:
            checkpoint_dir: Local directory containing checkpoint files
        """
        import boto3
        from botocore.exceptions import ClientError

        s3 = boto3.client("s3")
        bucket_name = os.getenv("CHECKPOINT_BUCKET")

        if not bucket_name:
            raise ValueError("CHECKPOINT_BUCKET environment variable not set")

        # Create a checkpoint ID using job ID
        checkpoint_id = f"{self.job_id}/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
        # Upload all files in checkpoint directory
        for root, _, files in os.walk(checkpoint_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_path = os.path.join(
                    "checkpoints",
                    checkpoint_id,
                    file,
                )

                try:
                    s3.upload_file(local_path, bucket_name, s3_path)
                except ClientError as e:
                    self.logger.error(f"Failed to upload {file} to S3: {e}")
                    raise
        
        # Log S3 upload to DynamoDB
        if self.job_service:
            try:
                s3_path = f"s3://{bucket_name}/checkpoints/{checkpoint_id}"
                
                # Record S3 location in job resources
                self.job_service.add_job_resource(
                    self.job_id,
                    resource_type="CHECKPOINT_S3",
                    resource_id=s3_path,
                    metadata={
                        "bucket": bucket_name,
                        "prefix": f"checkpoints/{checkpoint_id}",
                        "upload_time": datetime.now().isoformat()
                    }
                )
                
                self.job_service.add_job_log(
                    self.job_id,
                    "INFO",
                    f"Checkpoint uploaded to {s3_path}"
                )
            except Exception as e:
                self.logger.error(f"Failed to log S3 upload to DynamoDB: {e}")

    def _download_checkpoint_from_s3(
        self, job_id: Optional[str] = None
    ) -> Optional[str]:
        """Download checkpoint from S3.

        Args:
            job_id: Job ID to download checkpoint for. If None, uses current job_id.

        Returns:
            Path to downloaded checkpoint directory, or None if not found
        """
        import boto3
        from botocore.exceptions import ClientError

        s3 = boto3.client("s3")
        bucket_name = os.getenv("CHECKPOINT_BUCKET")

        if not bucket_name:
            raise ValueError("CHECKPOINT_BUCKET environment variable not set")
            
        # Use provided job_id or current job_id
        target_job_id = job_id or self.job_id

        # Create temporary directory for checkpoint
        checkpoint_dir = tempfile.mkdtemp()
        
        try:
            # If we have JobService and a job_id, try to find checkpoint from resources
            if self.job_service and target_job_id:
                try:
                    # Find checkpoint resources for this job
                    checkpoints = []
                    
                    # Get job resources of checkpoint type
                    resources = self.job_service.get_job_resources(target_job_id)
                    for resource in resources:
                        if resource.resource_type == "CHECKPOINT_S3":
                            checkpoints.append(resource)
                    
                    # Sort by timestamp in metadata if available
                    checkpoints.sort(
                        key=lambda r: r.metadata.get("upload_time", ""),
                        reverse=True  # Most recent first
                    )
                    
                    # Use the most recent checkpoint
                    if checkpoints:
                        most_recent = checkpoints[0]
                        prefix = most_recent.metadata.get("prefix", f"checkpoints/{target_job_id}")
                        
                        self.logger.info(f"Found checkpoint in job resources: {prefix}")
                        
                        # List objects with this prefix
                        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
                        
                        if "Contents" in response:
                            # Download all checkpoint files
                            for obj in response["Contents"]:
                                local_path = os.path.join(checkpoint_dir, os.path.basename(obj["Key"]))
                                s3.download_file(bucket_name, obj["Key"], local_path)
                            
                            self.logger.info(f"Downloaded checkpoint from {prefix} to {checkpoint_dir}")
                            
                            # Log the download
                            self.job_service.add_job_log(
                                self.job_id,
                                "INFO",
                                f"Downloaded checkpoint from {prefix}"
                            )
                            
                            return checkpoint_dir
                            
                except Exception as e:
                    self.logger.warning(f"Failed to get checkpoint from job resources: {e}")
                    # Continue with legacy approach
            
            # Legacy approach: list objects in checkpoint directory
            prefix = f"checkpoints/{target_job_id}/"
            response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)

            if "Contents" not in response:
                return None

            # Download all checkpoint files
            for obj in response["Contents"]:
                local_path = os.path.join(checkpoint_dir, os.path.basename(obj["Key"]))
                s3.download_file(bucket_name, obj["Key"], local_path)

            return checkpoint_dir

        except ClientError as e:
            self.logger.error(f"Failed to download checkpoint from S3: {e}")
            return None

    def resume_training(self, job_id: Optional[str] = None):
        """Resume training from the latest checkpoint.

        Args:
            job_id: Optional job ID to resume from
        """
        checkpoint_dir = None

        # First try to find local checkpoint
        if self.output_dir:
            local_checkpoint = os.path.join(self.output_dir, "checkpoint-*")
            checkpoints = sorted(
                glob.glob(local_checkpoint), key=lambda x: int(x.split("-")[-1])
            )
            if checkpoints:
                checkpoint_dir = checkpoints[-1]  # Use the latest checkpoint
                self.logger.info(f"Found local checkpoint: {checkpoint_dir}")
                self.last_checkpoint = checkpoint_dir
                
                # Log resuming from local checkpoint
                if self.job_service:
                    self.job_service.add_job_log(
                        self.job_id,
                        "INFO",
                        f"Resuming from local checkpoint: {checkpoint_dir}"
                    )
                    
                return checkpoint_dir

        # If no local checkpoint, try S3
        if not checkpoint_dir:
            checkpoint_dir = self._download_checkpoint_from_s3(job_id)

        if not checkpoint_dir:
            self.logger.warning(
                "No checkpoints found locally or in S3, starting fresh training"
            )
            
            # Log starting fresh training
            if self.job_service:
                self.job_service.add_job_log(
                    self.job_id,
                    "INFO",
                    "No checkpoints found, starting fresh training"
                )
                
            return

        # Load model and tokenizer from checkpoint
        try:
            self.model = LayoutLMForTokenClassification.from_pretrained(
                checkpoint_dir,
                num_labels=self.num_labels,
                label2id=self.label2id,
                id2label=self.id2label,
            )
            self.tokenizer = LayoutLMTokenizerFast.from_pretrained(checkpoint_dir)

            # Load training state if available
            training_state_path = os.path.join(checkpoint_dir, "training_state.pt")
            if os.path.exists(training_state_path):
                training_state = torch.load(training_state_path)
                if hasattr(self, "optimizer"):
                    self.optimizer.load_state_dict(
                        training_state["optimizer_state_dict"]
                    )
                if (
                    hasattr(self, "scheduler")
                    and training_state["scheduler_state_dict"]
                ):
                    self.scheduler.load_state_dict(
                        training_state["scheduler_state_dict"]
                    )
                self.current_epoch = training_state["epoch"]
                self.global_step = training_state["global_step"]

            self.logger.info(
                f"Resumed training from checkpoint at step {self.global_step}"
            )
            
            # Log resuming training
            if self.job_service:
                self.job_service.add_job_status(
                    self.job_id,
                    "RESUMED",
                    f"Resumed training from checkpoint at step {self.global_step}"
                )

        except Exception as e:
            self.logger.error(f"Failed to load checkpoint: {e}")
            self.logger.warning("Starting fresh training")
            
            # Log failure to load checkpoint
            if self.job_service:
                self.job_service.add_job_log(
                    self.job_id,
                    "ERROR",
                    f"Failed to load checkpoint: {e}"
                )
                
            return None

    def _initialize_wandb_early(self):
        """Initialize W&B at the start to ensure single process."""
        # This is now a no-op since we no longer use W&B
        pass

    def initialize_wandb(self):
        """Initialize Weights & Biases for experiment tracking."""
        # This is now a no-op since we no longer use W&B
        pass

    def initialize_model(self):
        """Initialize the LayoutLM model and tokenizer."""
        self.logger.info("Initializing model and tokenizer...")

        # Initialize tokenizer
        self.tokenizer = LayoutLMTokenizerFast.from_pretrained(self.model_name)

        # Initialize model (will be configured once we know the number of labels)
        self.model = None  # Will be initialized after data loading

        self.logger.info("Model and tokenizer initialized")

    def initialize_dynamo(self):
        """Initialize DynamoDB client if not already initialized.

        This is a no-op if the client is already initialized. If not initialized,
        it will attempt to initialize using the table name from constructor or Pulumi.

        Raises:
            ValueError: If DynamoDB client cannot be initialized.
        """
        if self.dynamo_client is not None:
            self.logger.debug("DynamoDB client already initialized")
            return

        if not self.dynamo_table:
            try:
                self.dynamo_table = get_dynamo_table(env=self.data_config.env)
                self.logger.info(
                    f"Retrieved DynamoDB table name from Pulumi: {self.dynamo_table}"
                )
            except Exception as e:
                raise ValueError(f"Failed to get DynamoDB table name from Pulumi: {e}")

        try:
            self.dynamo_client = DynamoClient(self.dynamo_table)
            self.logger.info(
                f"Successfully initialized DynamoDB client for table: {self.dynamo_table}"
            )
        except Exception as e:
            raise ValueError(f"Failed to initialize DynamoDB client: {e}")

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

    def encode_example_for_layoutlm(self, example, tokenizer, label2id):
        """Encode a single example for LayoutLM model.

        Args:
            example: Dictionary containing words, bboxes, and labels
            tokenizer: LayoutLM tokenizer
            label2id: Label to ID mapping

        Returns:
            Dictionary containing encoded inputs
        """
        # Convert words to token IDs with special tokens
        tokens = []
        token_boxes = []
        token_labels = []

        # Add CLS token at start
        tokens.append(tokenizer.cls_token_id)
        token_boxes.append([0, 0, 0, 0])
        token_labels.append(-100)  # Special tokens get -100

        # Process each word
        for word, box, label in zip(
            example["words"], example["bboxes"], example["labels"]
        ):
            # Tokenize word into subwords
            word_tokens = tokenizer.tokenize(word)
            word_ids = tokenizer.convert_tokens_to_ids(word_tokens)

            # Add tokens and replicate box for each subword
            tokens.extend(word_ids)
            token_boxes.extend([box] * len(word_ids))

            # First subword gets the label, rest get -100
            if label in label2id:  # Only convert valid labels
                token_labels.append(label2id[label])
                token_labels.extend([-100] * (len(word_ids) - 1))
            else:
                token_labels.extend([-100] * len(word_ids))

        # Add SEP token at end
        tokens.append(tokenizer.sep_token_id)
        token_boxes.append([0, 0, 0, 0])
        token_labels.append(-100)

        # Pad or truncate to max length
        padding_length = 512 - len(tokens)
        if padding_length > 0:
            # Pad
            tokens.extend([tokenizer.pad_token_id] * padding_length)
            token_boxes.extend([[0, 0, 0, 0]] * padding_length)
            token_labels.extend([-100] * padding_length)
        else:
            # Truncate
            tokens = tokens[:512]
            token_boxes = token_boxes[:512]
            token_labels = token_labels[:512]

        # Create attention mask (1 for real tokens, 0 for padding)
        attention_mask = [1] * min(len(example["words"]) + 2, 512)  # +2 for CLS and SEP
        attention_mask.extend([0] * max(512 - len(attention_mask), 0))

        # Normalize box coordinates
        normalized_boxes = []
        for box in token_boxes:
            normalized_boxes.append([min(max(0, int(coord)), 1000) for coord in box])

        return {
            "input_ids": tokens,
            "attention_mask": attention_mask,
            "bbox": normalized_boxes,
            "labels": token_labels,
        }

    def _preprocess_dataset(self, dataset: Dataset) -> Dataset:
        """Preprocess dataset by encoding inputs for LayoutLM.

        Args:
            dataset: Raw dataset with words, bboxes, and labels

        Returns:
            Processed dataset with encoded inputs
        """

        # Create a preprocessing function that uses the class tokenizer and label mappings
        def preprocess_function(example):
            return self.encode_example_for_layoutlm(
                example, tokenizer=self.tokenizer, label2id=self.label2id
            )

        # Apply preprocessing to each split with caching enabled
        processed_dataset = {}
        for split, data in dataset.items():
            self.logger.info(f"Preprocessing {split} split...")
            # Enable caching by providing a descriptive cache_file_name
            cache_dir = os.path.join(self.data_config.cache_dir, "preprocessed")
            os.makedirs(cache_dir, exist_ok=True)

            processed_dataset[split] = data.map(
                preprocess_function,
                load_from_cache_file=True,
                cache_file_name=os.path.join(
                    cache_dir,
                    f"layoutlm_processed_{split}_{self.model_name.replace('/', '_')}",
                ),
                desc=f"Preprocessing {split} split",
            )
            self.logger.info(f"Finished preprocessing {split} split")

        return DatasetDict(processed_dataset)

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

        # Initialize tokenizer if not already done
        if not self.tokenizer:
            self.logger.info("Initializing tokenizer before data loading...")
            self.initialize_model()

        # Load data from DynamoDB
        self.logger.info("Loading data from DynamoDB...")
        dynamo_examples = self._load_dynamo_data()
        self.logger.info(f"Loaded {len(dynamo_examples)} receipts from DynamoDB")

        # Convert DynamoDB data to list format
        examples = {"words": [], "bboxes": [], "labels": [], "image_id": []}

        for example in dynamo_examples.values():
            examples["words"].append(example["words"])
            examples["bboxes"].append(example["bboxes"])
            examples["labels"].append(example["labels"])
            examples["image_id"].append(example["image_id"])

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
        if balance_ratio > 0 and len(examples["words"]) > 0:
            self.logger.info(f"Balancing dataset with target ratio {balance_ratio}...")
            examples = balance_dataset(examples, target_entity_ratio=balance_ratio)

        # Apply data augmentation if requested and if we have examples
        if augment and len(examples["words"]) > 0:
            self.logger.info("Applying data augmentation...")
            examples = augment_example(examples)

        # Create sliding windows
        self.logger.info(
            f"Creating sliding windows of size {self.data_config.window_size}..."
        )
        train_windows = []

        # Process DynamoDB examples
        if len(examples["words"]) > 0:
            for i in range(len(examples["words"])):
                windows = create_sliding_windows(
                    examples["words"][i],
                    examples["bboxes"][i],
                    examples["labels"][i],
                    image_id=examples["image_id"][i],
                    window_size=self.data_config.window_size,
                    overlap=self.data_config.window_overlap,
                )
                train_windows.extend(windows)

        # Process SROIE examples
        sroie_train_windows = []
        sroie_test_windows = []
        for split, split_examples in sroie_examples.items():
            for example in split_examples:
                windows = create_sliding_windows(
                    example["words"],
                    example["bboxes"],
                    example["labels"],
                    image_id=example.get("image_id"),
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
        train_dataset = Dataset.from_list(
            train_windows,
            features=Features(
                {
                    "words": Sequence(Value("string")),
                    "bboxes": Sequence(Sequence(Value("int64"))),
                    "labels": Sequence(Value("string")),
                    "image_id": Value("string"),
                }
            ),
        )
        val_dataset = Dataset.from_list(
            sroie_test_windows,
            features=Features(
                {
                    "words": Sequence(Value("string")),
                    "bboxes": Sequence(Sequence(Value("int64"))),
                    "labels": Sequence(Value("string")),
                    "image_id": Value("string"),
                }
            ),
        )

        # Print statistics
        self._print_dataset_statistics(train_dataset, "Train")
        self._print_dataset_statistics(val_dataset, "Validation")

        # Create dataset dictionary
        dataset_dict = DatasetDict({"train": train_dataset, "validation": val_dataset})

        # Create label mappings before preprocessing
        unique_labels = set()
        for split in dataset_dict.values():
            for label_sequence in split["labels"]:
                unique_labels.update(label_sequence)
        self.label_list = sorted(list(unique_labels))
        self.num_labels = len(self.label_list)
        self.label2id = {label: i for i, label in enumerate(self.label_list)}
        self.id2label = {i: label for label, i in self.label2id.items()}
        self.logger.info(f"Found {self.num_labels} unique labels")

        # Now preprocess with tokenizer and label mappings in place
        return self._preprocess_dataset(dataset_dict)

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

        Raises:
            ValueError: If dataset validation fails or model initialization fails
            OSError: If output directory cannot be created
        """
        self.logger.info("Starting training configuration...")

        # Initialize model if not done yet (tokenizer should already be initialized)
        if not self.model:
            self.logger.info("Initializing model...")
            try:
                # Model will be initialized with correct number of labels
                self.model = LayoutLMForTokenClassification.from_pretrained(
                    self.model_name,
                    num_labels=self.num_labels,
                    label2id=self.label2id,
                    id2label=self.id2label,
                )
                self.logger.info(f"Successfully initialized model: {self.model_name}")
            except Exception as e:
                raise ValueError(f"Failed to initialize model: {str(e)}")

        # Load dataset if not already loaded
        if not hasattr(self, "dataset") or not self.dataset:
            self.logger.info("Loading dataset...")
            try:
                self.dataset = (
                    self.load_data()
                )  # This now handles tokenizer initialization and preprocessing
                self.logger.info("Successfully loaded dataset")
            except Exception as e:
                raise ValueError(f"Failed to load dataset: {str(e)}")

        # Update training config with any provided kwargs
        self.logger.info("Updating training configuration...")
        for key, value in kwargs.items():
            if hasattr(self.training_config, key):
                self.logger.debug(f"Setting {key} = {value}")
                setattr(self.training_config, key, value)
            else:
                self.logger.warning(f"Unknown training config parameter: {key}")

        # Set up output directory with path validation
        try:
            if output_dir:
                self.output_dir = output_dir
            else:
                # Use cache_dir from data_config if available, otherwise use a default
                cache_dir = getattr(self.data_config, "cache_dir", None)
                if not cache_dir:
                    # Default to a directory in the user's home directory
                    cache_dir = os.path.expanduser("~/.cache/receipt_trainer")
                    self.logger.warning(
                        f"No cache directory specified, using default: {cache_dir}"
                    )

                self.output_dir = os.path.join(cache_dir, "checkpoints")

            self.output_dir = os.path.abspath(
                self.output_dir
            )  # Convert to absolute path
            os.makedirs(self.output_dir, exist_ok=True)
            self.logger.info(f"Output directory set to: {self.output_dir}")
        except OSError as e:
            raise OSError(f"Failed to create output directory: {str(e)}")

        # Get the appropriate device
        try:
            self.device = self._get_device()
            self.logger.info(f"Using device: {self.device}")

            # Apply device-specific optimizations
            if self.device.type == "mps":
                self.logger.info("Applying MPS-specific optimizations...")
                # MPS doesn't support mixed precision training
                self.training_config.bf16 = False
                self.training_config.fp16 = False

                # Adjust batch size and gradient accumulation for M-series chips
                if not kwargs.get("batch_size"):
                    self.training_config.batch_size = min(
                        self.training_config.batch_size, 16
                    )
                if not kwargs.get("gradient_accumulation_steps"):
                    self.training_config.gradient_accumulation_steps = max(
                        self.training_config.gradient_accumulation_steps, 4
                    )
                self.logger.info("MPS optimizations applied successfully")

            # Set up distributed training if enabled
            if self.training_config.distributed_training:
                if self.device.type != "cuda":
                    self.logger.warning(
                        "Distributed training is only supported with CUDA devices"
                    )
                    self.training_config.distributed_training = False
                else:
                    self.logger.info("Setting up distributed training...")
                    # Initialize distributed environment
                    if self.training_config.local_rank != -1:
                        torch.cuda.set_device(self.training_config.local_rank)
                        self.device = torch.device(
                            "cuda", self.training_config.local_rank
                        )
                        torch.distributed.init_process_group(
                            backend=self.training_config.ddp_backend,
                            world_size=self.training_config.world_size,
                            rank=self.training_config.local_rank,
                        )
                        self.logger.info(
                            f"Initialized distributed training with rank {self.training_config.local_rank}"
                        )

            self.model.to(self.device)
            self.logger.info(f"Model moved to device: {self.device}")

        except Exception as e:
            raise ValueError(f"Failed to configure device and optimizations: {str(e)}")

        # Create training arguments
        try:
            self.logger.info("Setting up training arguments...")
            self.training_args = TrainingArguments(
                output_dir=self.output_dir,
                num_train_epochs=self.training_config.num_epochs,
                per_device_train_batch_size=self.training_config.batch_size,
                gradient_accumulation_steps=self.training_config.gradient_accumulation_steps,
                learning_rate=self.training_config.learning_rate,
                weight_decay=self.training_config.weight_decay,
                max_grad_norm=self.training_config.max_grad_norm,
                warmup_ratio=self.training_config.warmup_ratio,
                fp16=self.training_config.fp16 and self.device.type == "cuda",
                bf16=self.training_config.bf16 and self.device.type == "cuda",
                evaluation_strategy="steps",
                eval_steps=self.training_config.evaluation_steps,
                save_strategy="steps",
                save_steps=self.training_config.save_steps,
                logging_steps=self.training_config.logging_steps,
                load_best_model_at_end=True,
                metric_for_best_model="eval_loss",
                greater_is_better=False,
                # Distributed training arguments
                local_rank=self.training_config.local_rank,
                ddp_backend=(
                    self.training_config.ddp_backend
                    if self.training_config.distributed_training
                    else None
                ),
                dataloader_num_workers=(
                    4 if self.training_config.distributed_training else 0
                ),
                # Disable automatic W&B initialization
                report_to=[],
            )
            self.logger.info("Training arguments configured successfully")
        except Exception as e:
            raise ValueError(f"Failed to configure training arguments: {str(e)}")

        # Log final configuration summary
        self.logger.info("\nTraining Configuration Summary:")
        self.logger.info(f"Number of labels: {self.num_labels}")
        self.logger.info(f"Labels: {', '.join(self.label_list)}")
        self.logger.info(f"Output directory: {self.output_dir}")
        self.logger.info(f"Device: {self.device}")
        if self.device.type == "mps":
            self.logger.info("Using Apple Neural Engine optimizations")
            self.logger.info(f"Batch size: {self.training_config.batch_size}")
            self.logger.info(
                f"Gradient accumulation steps: {self.training_config.gradient_accumulation_steps}"
            )
        self.logger.info("Training configuration completed successfully")

    def _create_trainer(
        self,
        enable_early_stopping: bool,
        callbacks: Optional[List[TrainerCallback]] = None,
    ):
        """Create and configure the Trainer object."""
        # Create data collator
        data_collator = DataCollatorForTokenClassification(
            self.tokenizer, pad_to_multiple_of=8 if self.training_config.bf16 else None
        )

        # Setup callbacks
        trainer_callbacks = []
        if enable_early_stopping:
            trainer_callbacks.append(
                EarlyStoppingCallback(
                    early_stopping_patience=self.training_config.early_stopping_patience
                )
            )
        if callbacks:
            trainer_callbacks.extend(callbacks)

        # Create Trainer
        trainer = Trainer(
            model=self.model,
            args=self.training_args,
            train_dataset=self.dataset["train"],
            eval_dataset=self.dataset["validation"],
            data_collator=data_collator,
            tokenizer=self.tokenizer,
            callbacks=trainer_callbacks,
        )

        return trainer

    def _log_training_results(self, train_result):
        """Log training results to DynamoDB and print summary."""
        # Run detailed evaluation on both splits
        eval_output_dir = os.path.join(self.output_dir, "eval")
        os.makedirs(eval_output_dir, exist_ok=True)

        # Get metrics for both splits
        train_metrics = self.evaluate("train", eval_output_dir, detailed_report=True)
        val_metrics = self.evaluate("validation", eval_output_dir, detailed_report=True)

        # Prepare metrics for logging
        metrics = {
            **train_metrics,
            **val_metrics,
            "train/total_steps": train_result.global_step,
            "train/total_loss": train_result.training_loss,
        }

        # Log all metrics to DynamoDB
        if self.job_service:
            for metric_name, metric_value in metrics.items():
                try:
                    self.job_service.add_job_metric(
                        job_id=self.job_id,
                        metric_name=metric_name,
                        metric_value=float(metric_value) if isinstance(metric_value, (int, float)) else metric_value,
                        step=train_result.global_step,
                        metadata={"type": "training_result"}
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to log metric {metric_name} to DynamoDB: {e}")
            
            # Log training completion
            self.job_service.add_job_status(
                self.job_id,
                "COMPLETED",
                f"Training completed after {train_result.global_step} steps"
            )
            
            # Log summary message
            summary_msg = (
                f"Training completed with:\n"
                f"- Train macro F1: {train_metrics['train/macro_avg/f1-score']:.4f}\n"
                f"- Validation macro F1: {val_metrics['validation/macro_avg/f1-score']:.4f}\n"
                f"- Total steps: {train_result.global_step}\n"
                f"- Final loss: {train_result.training_loss:.4f}"
            )
            self.job_service.add_job_log(
                self.job_id,
                "INFO",
                summary_msg
            )

        # Print summary
        self.logger.info("\nTraining Results Summary:")
        self.logger.info(f"Total steps: {train_result.global_step}")
        self.logger.info(f"Average training loss: {train_result.training_loss:.4f}")
        self.logger.info(f"\nTrain Metrics:")
        self.logger.info(f"Macro F1: {train_metrics['train/macro_avg/f1-score']:.4f}")
        self.logger.info(
            f"Weighted F1: {train_metrics['train/weighted_avg/f1-score']:.4f}"
        )
        self.logger.info(f"\nValidation Metrics:")
        self.logger.info(
            f"Macro F1: {val_metrics['validation/macro_avg/f1-score']:.4f}"
        )
        self.logger.info(
            f"Weighted F1: {val_metrics['validation/weighted_avg/f1-score']:.4f}"
        )

        # Print per-label performance
        self.logger.info("\nPer-Label Performance (Validation):")
        for label in self.label_list:
            if label != "O":  # Skip the Outside label
                f1 = val_metrics.get(f"validation/{label}/f1-score", 0)
                prec = val_metrics.get(f"validation/{label}/precision", 0)
                rec = val_metrics.get(f"validation/{label}/recall", 0)
                support = val_metrics.get(f"validation/{label}/support", 0)
                self.logger.info(
                    f"{label:15} F1: {f1:.4f} | Precision: {prec:.4f} | "
                    f"Recall: {rec:.4f} | Support: {support}"
                )

    def evaluate(
        self,
        split: str = "validation",
        output_dir: Optional[str] = None,
        detailed_report: bool = True,
    ) -> Dict[str, float]:
        """Evaluate the model on a specific dataset split."""
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
                pad_to_multiple_of=8 if self.training_config.bf16 else None,
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
            true_flat, pred_flat, output_dict=True, zero_division=0
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

            # Log confusion matrix to DynamoDB
            if self.job_service:
                # Convert confusion matrix to serializable format
                cm_data = {
                    "matrix": cm.tolist(),
                    "labels": labels
                }
                
                try:
                    # Log confusion matrix
                    self.job_service.add_job_metric(
                        job_id=self.job_id,
                        metric_name=f"{split}/confusion_matrix",
                        metric_value=cm_data,
                        metadata={"type": "evaluation"}
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to log confusion matrix to DynamoDB: {e}")

            # Add per-document analysis
            doc_metrics = self._compute_document_metrics(
                self.dataset[split], predictions.predictions, predictions.label_ids
            )
            metrics.update(doc_metrics)

        # Log metrics to DynamoDB
        if self.job_service:
            for metric_name, metric_value in metrics.items():
                if isinstance(metric_value, (int, float)):
                    try:
                        self.job_service.add_job_metric(
                            job_id=self.job_id,
                            metric_name=metric_name,
                            metric_value=float(metric_value),
                            metadata={"type": "evaluation", "split": split}
                        )
                    except Exception as e:
                        self.logger.warning(f"Failed to log metric {metric_name} to DynamoDB: {e}")

            # Add evaluation status
            self.job_service.add_job_status(
                self.job_id,
                "EVALUATING",
                f"Evaluation on {split} split completed"
            )
            
            # Log summary message
            summary_msg = (
                f"Evaluation results for {split} split:\n"
                f"- Accuracy: {metrics.get(f'{split}/accuracy', 0):.4f}\n"
                f"- Macro F1: {metrics.get(f'{split}/macro_avg/f1-score', 0):.4f}\n"
                f"- Weighted F1: {metrics.get(f'{split}/weighted_avg/f1-score', 0):.4f}"
            )
            self.job_service.add_job_log(
                self.job_id,
                "INFO",
                summary_msg
            )

        # Print summary
        self.logger.info("\nEvaluation Results:")
        self.logger.info(f"Split: {split}")
        self.logger.info(f"Macro F1: {metrics[f'{split}/macro_avg/f1-score']:.4f}")
        self.logger.info(
            f"Weighted F1: {metrics[f'{split}/weighted_avg/f1-score']:.4f}"
        )
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
                            if any(
                                label_true
                            ):  # Only compute if label exists in document
                                precision = precision_score(
                                    label_true, label_pred, zero_division=0
                                )
                                recall = recall_score(
                                    label_true, label_pred, zero_division=0
                                )
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
        """Get the default device for training.

        Returns:
            torch.device: The device to use for training
        """
        # Use device from config if specified
        if self.training_config.device:
            return torch.device(self.training_config.device)

        # Auto-detect device
        if torch.backends.mps.is_available() and torch.backends.mps.is_built():
            self.logger.info("Using Apple Neural Engine (MPS)")
            return torch.device("mps")
        elif torch.cuda.is_available():
            self.logger.info(f"Using CUDA GPU: {torch.cuda.get_device_name(0)}")
            return torch.device("cuda")

        self.logger.info("Using CPU")
        return torch.device("cpu")

    def _generate_hyperparameter_report(self, sweep_job_id: str) -> Dict[str, Any]:
        """Generate a comprehensive report of hyperparameter performance.

        Args:
            sweep_job_id: The job ID of the sweep to analyze

        Returns:
            Dictionary containing report data
        """
        self.logger.info("Generating hyperparameter report...")

        # Get sweep data from DynamoDB
        if not self.job_service:
            raise ValueError("JobService not initialized")
            
        # Find all trial jobs that are part of this sweep
        trial_jobs = []
        try:
            # Get all jobs with a dependency on the sweep job
            dependencies = self.job_service.get_dependent_jobs(sweep_job_id)
            for dependency in dependencies:
                # Get the full job with its metrics
                dependent_job_id = dependency.job_id
                trial_job, _ = self.job_service.get_job_with_status(dependent_job_id)
                trial_metrics = self.job_service.get_job_metrics(dependent_job_id)
                
                # Find the best validation metric
                best_f1 = 0.0
                for metric in trial_metrics:
                    if metric.metric_name == "validation/macro_avg/f1-score":
                        if isinstance(metric.value, (int, float)) and float(metric.value) > best_f1:
                            best_f1 = float(metric.value)
                
                # Get hyperparameters from job config
                config = trial_job.job_config.get("training_config", {})
                
                trial_jobs.append({
                    "job_id": dependent_job_id,
                    "metrics": {
                        "validation/macro_avg/f1-score": best_f1,
                    },
                    "params": config,
                })
        except Exception as e:
            self.logger.error(f"Failed to get trial jobs: {e}")
            raise

        # Generate report data
        report = {
            "sweep_job_id": sweep_job_id,
            "total_trials": len(trial_jobs),
            "completed_trials": len([j for j in trial_jobs if j["metrics"]["validation/macro_avg/f1-score"] > 0]),
            "best_trial": None,
            "param_importance": {},
            "best_configs": [],
        }

        if trial_jobs:
            # Find best trial
            best_trial = max(
                trial_jobs, key=lambda x: x["metrics"]["validation/macro_avg/f1-score"]
            )
            report["best_trial"] = {
                "job_id": best_trial["job_id"],
                "metrics": best_trial["metrics"],
                "params": best_trial["params"],
            }

            # Get top 3 best configurations
            sorted_trials = sorted(
                trial_jobs,
                key=lambda x: x["metrics"]["validation/macro_avg/f1-score"],
                reverse=True,
            )
            report["best_configs"] = [
                {
                    "job_id": trial["job_id"],
                    "metrics": trial["metrics"],
                    "params": trial["params"],
                }
                for trial in sorted_trials[:3]
            ]
            
            # Log report to DynamoDB
            try:
                self.job_service.add_job_metric(
                    job_id=sweep_job_id,
                    metric_name="hyperparameter_sweep/report",
                    metric_value=report,
                    metadata={"type": "sweep_report"}
                )
                
                self.job_service.add_job_log(
                    sweep_job_id,
                    "INFO",
                    f"Generated hyperparameter sweep report. Best validation F1: {best_trial['metrics']['validation/macro_avg/f1-score']:.4f}"
                )
            except Exception as e:
                self.logger.error(f"Failed to log hyperparameter report: {e}")

        return report

    def save_model(self, output_path: str):
        """Save the trained model and tokenizer.

        Args:
            output_path: Path where to save the model

        Raises:
            ValueError: If model or tokenizer is not initialized, or if validation fails
        """
        if not self.model or not self.tokenizer:
            raise ValueError("Model and tokenizer must be initialized before saving")

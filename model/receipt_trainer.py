"""ReceiptTrainer: A wrapper class for training LayoutLM models on receipt data."""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import os
import json
import tempfile
import torch
from transformers import (
    LayoutLMForTokenClassification,
    LayoutLMTokenizerFast,
    TrainingArguments,
)
from datasets import Dataset, DatasetDict, load_dataset
import wandb
from dynamo import DynamoClient
from dynamo.data._pulumi import load_env
import logging
from pathlib import Path

REQUIRED_ENV_VARS = {
    'WANDB_API_KEY': 'API key for Weights & Biases',
    'HF_TOKEN': 'Hugging Face token for accessing models',
    'AWS_ACCESS_KEY_ID': 'AWS access key for DynamoDB and S3',
    'AWS_SECRET_ACCESS_KEY': 'AWS secret key for DynamoDB and S3',
    'AWS_DEFAULT_REGION': 'AWS region for services',
}

@dataclass
class TrainingConfig:
    """Configuration for model training."""
    batch_size: int = 16
    learning_rate: float = 3e-4
    num_epochs: int = 20
    gradient_accumulation_steps: int = 32
    warmup_ratio: float = 0.2
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    evaluation_steps: int = 100
    save_steps: int = 100
    logging_steps: int = 50
    bf16: bool = True  # Use bfloat16 precision
    early_stopping_patience: int = 5

@dataclass
class DataConfig:
    """Configuration for data processing."""
    balance_ratio: float = 0.7
    use_sroie: bool = True
    augment: bool = True
    max_length: int = 512
    sliding_window_size: int = 50
    sliding_window_overlap: int = 10
    env: str = "dev"  # Pulumi stack environment (dev/prod)
    cache_dir: Optional[str] = None  # Directory to cache datasets

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
        # First, validate environment variables
        self._validate_env_vars()
        
        # Setup logging first for other initialization steps
        self._setup_logging()
        
        self.wandb_project = wandb_project
        self.model_name = model_name
        self.s3_bucket = s3_bucket
        
        # Set configurations
        self.training_config = training_config or TrainingConfig()
        self.data_config = data_config or DataConfig()
        
        # Create cache directory if needed
        if not self.data_config.cache_dir:
            self.data_config.cache_dir = os.path.join(tempfile.gettempdir(), "receipt_trainer")
        os.makedirs(self.data_config.cache_dir, exist_ok=True)
        
        # Get DynamoDB table name from Pulumi stack if not provided
        self.dynamo_table = dynamo_table
        if not self.dynamo_table:
            pulumi_outputs = load_env(self.data_config.env)
            self.dynamo_table = pulumi_outputs.get('dynamodb_table_name')
            if not self.dynamo_table:
                self.logger.warning(
                    f"Could not find DynamoDB table name in Pulumi stack outputs for environment: {self.data_config.env}"
                )
        
        # Initialize device
        self.device = device or self._get_default_device()
        
        # Initialize components as None
        self.model = None
        self.tokenizer = None
        self.dataset = None
        self.label_map = None
        self.dynamo_client = None
        self.wandb_run = None
        
        # Log initialization
        self.logger.info(f"Initialized ReceiptTrainer with device: {self.device}")
        self.logger.info(f"Model: {self.model_name}")
        self.logger.info(f"W&B Project: {self.wandb_project}")
        self.logger.info(f"DynamoDB Table: {self.dynamo_table}")
        self.logger.info(f"Environment: {self.data_config.env}")
        self.logger.info(f"Cache directory: {self.data_config.cache_dir}")

    def _validate_env_vars(self):
        """Validate that all required environment variables are set."""
        missing_vars = []
        for var, description in REQUIRED_ENV_VARS.items():
            if not os.getenv(var):
                missing_vars.append(f"{var} ({description})")
        
        if missing_vars:
            raise EnvironmentError(
                "Missing required environment variables:\n" + 
                "\n".join(f"- {var}" for var in missing_vars)
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
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
            self.logger.info(f"Initializing DynamoDB client for table: {self.dynamo_table}")
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
            receipt_details_dict, last_evaluated_key = self.dynamo_client.listReceiptDetails(
                last_evaluated_key=last_evaluated_key
            )
            
            for key, details in receipt_details_dict.items():
                processed_data = self._process_receipt_details(details)
                if processed_data:
                    dataset[key] = processed_data
            
            if not last_evaluated_key:
                break
        
        self.logger.info(f"Loaded {len(dataset)} receipts from DynamoDB")
        return dataset

    def _process_receipt_details(self, receipt_details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single receipt's details into the LayoutLM format."""
        receipt = receipt_details["receipt"]
        words = receipt_details["words"]
        word_tags = receipt_details["word_tags"]
        
        has_validated_word = False
        word_data = []
        
        # Calculate scale factor maintaining aspect ratio
        max_dim = 1000
        scale = max_dim / max(receipt.width, receipt.height)
        scaled_width = int(receipt.width * scale)
        scaled_height = int(receipt.height * scale)
        
        # First pass: collect words and their base labels
        for word in words:
            tag_label = "O"  # Default to Outside
            for tag in word_tags:
                if tag.word_id == word.word_id and tag.human_validated:
                    tag_label = tag.tag
                    has_validated_word = True
                    break
            
            # Get the coordinates (already normalized 0-1)
            x1 = min(word.top_left["x"], word.bottom_left["x"])
            y1 = min(word.top_left["y"], word.top_right["y"])
            x2 = max(word.top_right["x"], word.bottom_right["x"])
            y2 = max(word.bottom_left["y"], word.bottom_right["y"])
            
            # Store center_y for grouping
            center_y = (y1 + y2) / 2
            
            # Scale to 0-1000 maintaining aspect ratio
            normalized_bbox = [
                int(x1 * scaled_width),
                int(y1 * scaled_height),
                int(x2 * scaled_width),
                int(y2 * scaled_height)
            ]
            
            word_data.append({
                "text": word.text,
                "bbox": normalized_bbox,
                "base_label": tag_label,
                "word_id": word.word_id,
                "line_id": word.line_id,
                "center_y": center_y,
                "raw_coords": [x1, y1, x2, y2]
            })
        
        if not has_validated_word:
            return None
        
        # Sort words by line_id and word_id to maintain reading order
        word_data.sort(key=lambda x: (x["line_id"], x["word_id"]))
        
        # Convert to IOB format
        processed_words = []
        for i, word in enumerate(word_data):
            base_label = word["base_label"]
            iob_label = "O"
            
            if base_label != "O":
                # Check if this word is a continuation
                is_continuation = False
                if i > 0:
                    prev_word = word_data[i-1]
                    y_diff = abs(word["center_y"] - prev_word["center_y"])
                    x_diff = word["raw_coords"][0] - prev_word["raw_coords"][2]
                    if (prev_word["base_label"] == base_label and 
                        y_diff < 20 and x_diff < 50):
                        is_continuation = True
                
                iob_label = f"I-{base_label}" if is_continuation else f"B-{base_label}"
            
            processed_words.append({
                "text": word["text"],
                "bbox": word["bbox"],
                "label": iob_label
            })
        
        return {
            "words": [w["text"] for w in processed_words],
            "bboxes": [w["bbox"] for w in processed_words],
            "labels": [w["label"] for w in processed_words],
            "image_id": receipt.image_id,
            "receipt_id": receipt.receipt_id,
            "width": receipt.width,
            "height": receipt.height
        }

    def _load_sroie_data(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Load and prepare the SROIE dataset."""
        self.logger.info("Loading SROIE dataset...")
        
        # Define tag mapping from SROIE to our format
        sroie_to_our_format = {
            "B-COMPANY": "B-store_name",
            "I-COMPANY": "I-store_name",
            "B-ADDRESS": "B-address",
            "I-ADDRESS": "I-address",
            "B-DATE": "B-date",
            "I-DATE": "I-date",
            "B-TOTAL": "B-total_amount",
            "I-TOTAL": "I-total_amount",
            "O": "O",
        }
        
        dataset = load_dataset("darentang/sroie")
        train_data = {}
        test_data = {}
        
        def convert_example(example: Dict[str, Any], idx: int, split: str) -> Dict[str, Any]:
            # Convert labels using mapping
            labels = [sroie_to_our_format[label] for label in example['ner_tags']]
            
            # Scale coordinates to 0-1000
            boxes = example['bboxes']
            max_x = max(max(box[0], box[2]) for box in boxes)
            max_y = max(max(box[1], box[3]) for box in boxes)
            scale = 1000 / max(max_x, max_y)
            
            normalized_boxes = [
                [
                    int(box[0] * scale),
                    int(box[1] * scale),
                    int(box[2] * scale),
                    int(box[3] * scale)
                ]
                for box in boxes
            ]
            
            return {
                "words": example['words'],
                "bboxes": normalized_boxes,
                "labels": labels,
                "image_id": f"sroie_{split}_{idx}",
                "width": max_x,
                "height": max_y
            }
        
        # Convert train split
        for idx, example in enumerate(dataset['train']):
            train_data[f"sroie_train_{idx}"] = convert_example(example, idx, "train")
        
        # Convert test split
        for idx, example in enumerate(dataset['test']):
            test_data[f"sroie_test_{idx}"] = convert_example(example, idx, "test")
        
        self.logger.info(f"Loaded {len(train_data)} training and {len(test_data)} test examples from SROIE")
        return train_data, test_data

    def load_data(
        self,
        use_sroie: Optional[bool] = None,
        balance_ratio: Optional[float] = None,
        augment: Optional[bool] = None,
    ) -> DatasetDict:
        """Load and prepare the complete dataset."""
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
        train_dataset = Dataset.from_dict({
            "words": [example["words"] for example in all_train_data.values()],
            "bbox": [example["bboxes"] for example in all_train_data.values()],
            "labels": [example["labels"] for example in all_train_data.values()],
            "image_id": [example["image_id"] for example in all_train_data.values()]
        })
        
        val_dataset = Dataset.from_dict({
            "words": [example["words"] for example in sroie_test_data.values()],
            "bbox": [example["bboxes"] for example in sroie_test_data.values()],
            "labels": [example["labels"] for example in sroie_test_data.values()],
            "image_id": [example["image_id"] for example in sroie_test_data.values()]
        })
        
        # Create the final dataset dictionary
        self.dataset = DatasetDict({
            "train": train_dataset,
            "validation": val_dataset
        })
        
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
                self.wandb_run.log({
                    f"{split_name}/total_documents": len(split_dataset),
                    f"{split_name}/total_words": total_words,
                    **{f"{split_name}/label_{label}": count 
                       for label, count in label_counts.items()}
                })

    def configure_training(
        self,
        **kwargs: Any,
    ):
        """Configure training parameters.
        
        This is a placeholder - the full implementation will be added later.
        """
        self.logger.info("Configuring training...")
        # TODO: Implement training configuration
        pass

    def train(
        self,
        enable_checkpointing: bool = True,
        enable_early_stopping: bool = True,
        log_to_wandb: bool = True,
    ):
        """Train the model.
        
        This is a placeholder - the full implementation will be added later.
        """
        self.logger.info("Starting training...")
        # TODO: Implement training logic
        pass

    def save_model(self, output_path: str):
        """Save the trained model.
        
        This is a placeholder - the full implementation will be added later.
        """
        self.logger.info(f"Saving model to {output_path}...")
        # TODO: Implement model saving logic
        pass 
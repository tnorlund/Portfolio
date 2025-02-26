# Receipt Trainer API Documentation

## Core Classes

### ReceiptTrainer

The main class for training LayoutLM models on receipt data.

```python
from receipt_trainer import ReceiptTrainer
```

#### Constructor

```python
def __init__(
    wandb_project: str,
    model_name: str = "microsoft/layoutlm-base-uncased",
    dynamo_table: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    training_config: Optional[TrainingConfig] = None,
    data_config: Optional[DataConfig] = None,
    device: Optional[str] = None,
)
```

**Parameters:**
- `wandb_project` (str): Name of the Weights & Biases project
- `model_name` (str): Name/path of the pre-trained model (default: "microsoft/layoutlm-base-uncased")
- `dynamo_table` (Optional[str]): DynamoDB table name for data loading
- `s3_bucket` (Optional[str]): S3 bucket name for artifact storage
- `training_config` (Optional[TrainingConfig]): Training configuration
- `data_config` (Optional[DataConfig]): Data loading configuration
- `device` (Optional[str]): Device to use for training ("cuda", "mps", or "cpu")

#### Main Methods

##### load_data

```python
def load_data(
    use_sroie: bool = True,
    balance_ratio: float = 0.7,
    augment: bool = True,
) -> DatasetDict
```

Loads and preprocesses training data.

**Parameters:**
- `use_sroie` (bool): Whether to include SROIE dataset
- `balance_ratio` (float): Target ratio of entity tokens to total tokens
- `augment` (bool): Whether to apply data augmentation

**Returns:**
- `DatasetDict`: Dictionary containing train and validation splits

##### train

```python
def train(
    enable_checkpointing: bool = True,
    enable_early_stopping: bool = True,
    log_to_wandb: bool = True,
    resume_training: bool = True
)
```

Trains the model.

**Parameters:**
- `enable_checkpointing` (bool): Whether to save model checkpoints
- `enable_early_stopping` (bool): Whether to enable early stopping
- `log_to_wandb` (bool): Whether to log metrics to W&B
- `resume_training` (bool): Whether to attempt to resume from checkpoint

##### evaluate

```python
def evaluate(
    split: str = "validation",
    output_dir: Optional[str] = None,
    detailed_report: bool = True,
) -> Dict[str, float]
```

Evaluates the model on a specific dataset split.

**Parameters:**
- `split` (str): Dataset split to evaluate on ("train" or "validation")
- `output_dir` (Optional[str]): Directory to save evaluation artifacts
- `detailed_report` (bool): Whether to generate detailed performance analysis

**Returns:**
- Dictionary containing evaluation metrics

### Configuration Classes

#### TrainingConfig

Configuration for model training parameters.

```python
from receipt_trainer import TrainingConfig
```

**Attributes:**
- `batch_size` (int): Training batch size (default: 16)
- `learning_rate` (float): Learning rate (default: 3e-4)
- `num_epochs` (int): Number of training epochs (default: 20)
- `gradient_accumulation_steps` (int): Steps for gradient accumulation (default: 32)
- `warmup_ratio` (float): Ratio of warmup steps (default: 0.2)
- `weight_decay` (float): Weight decay for optimization (default: 0.01)
- `max_grad_norm` (float): Maximum gradient norm (default: 1.0)
- `evaluation_steps` (int): Steps between evaluations (default: 100)
- `save_steps` (int): Steps between checkpoints (default: 100)
- `logging_steps` (int): Steps between logging (default: 50)
- `bf16` (bool): Whether to use bfloat16 precision (default: True)
- `early_stopping_patience` (int): Patience for early stopping (default: 5)

**Distributed Training Parameters:**
- `distributed_training` (bool): Whether to use distributed training (default: False)
- `local_rank` (int): Process rank within node (default: -1)
- `world_size` (int): Total number of processes (default: 1)
- `ddp_backend` (str): DDP backend ("nccl" for GPU, "gloo" for CPU)
- `find_unused_parameters` (bool): Whether to find unused parameters in DDP
- `sync_bn` (bool): Whether to use SyncBatchNorm in distributed training

#### DataConfig

Configuration for data loading and processing.

```python
from receipt_trainer import DataConfig
```

**Parameters:**
- `env` (str): Environment name (dev/prod) (default: "dev")
- `cache_dir` (Optional[str]): Directory for caching data
- `use_sroie` (bool): Whether to include SROIE dataset (default: True)
- `balance_ratio` (float): Target ratio of entity tokens to total tokens (default: 0.7)
- `augment` (bool): Whether to apply data augmentation (default: True)
- `sliding_window_size` (int): Size of sliding windows (default: 50)
- `sliding_window_overlap` (int): Number of overlapping tokens between windows (default: 10)
- `max_length` (int): Maximum sequence length for tokenization (default: 512)

## Utility Functions

### Data Processing

```python
from receipt_trainer.utils.data import create_sliding_windows, balance_dataset, augment_example
```

#### create_sliding_windows
Creates sliding windows from input data for processing long documents.

#### balance_dataset
Balances the dataset by adjusting the ratio of entity tokens to total tokens.

#### augment_example
Applies data augmentation techniques to the input examples.

## Environment Variables

The following environment variables are required:

- `WANDB_API_KEY`: API key for Weights & Biases
- `HF_TOKEN`: Hugging Face token for accessing models
- `AWS_ACCESS_KEY_ID`: AWS access key for DynamoDB and S3
- `AWS_SECRET_ACCESS_KEY`: AWS secret key for DynamoDB and S3
- `AWS_DEFAULT_REGION`: AWS region for services

## Example Usage

```python
from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig

# Create configurations
training_config = TrainingConfig(
    batch_size=32,
    learning_rate=2e-5,
    num_epochs=10
)

data_config = DataConfig(
    env="dev",
    use_sroie=True,
    augment=True
)

# Initialize trainer
trainer = ReceiptTrainer(
    wandb_project="receipt-ocr",
    model_name="microsoft/layoutlm-base-uncased",
    training_config=training_config,
    data_config=data_config
)

# Load and prepare data
dataset = trainer.load_data()

# Train model
trainer.train(
    enable_checkpointing=True,
    enable_early_stopping=True,
    log_to_wandb=True
)

# Evaluate model
metrics = trainer.evaluate("validation")
``` 
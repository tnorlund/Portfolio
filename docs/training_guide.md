# Training Guide

This guide provides detailed instructions for training LayoutLM models using the Receipt Trainer package. It covers everything from basic training to advanced features like distributed training and hyperparameter optimization.

## Table of Contents

1. [Basic Training](#basic-training)
2. [Data Preparation](#data-preparation)
3. [Training Configuration](#training-configuration)
4. [Distributed Training](#distributed-training)
5. [Hyperparameter Optimization](#hyperparameter-optimization)
6. [Performance Optimization](#performance-optimization)
7. [Monitoring and Logging](#monitoring-and-logging)
8. [Troubleshooting](#troubleshooting)

## Basic Training

### Setting Up Your Environment

1. Install the package:
```bash
pip install receipt-trainer
```

2. Set up required environment variables:
```bash
export WANDB_API_KEY="your-wandb-api-key"
export HF_TOKEN="your-huggingface-token"
export AWS_ACCESS_KEY_ID="your-aws-access-key"
export AWS_SECRET_ACCESS_KEY="your-aws-secret-key"
export AWS_DEFAULT_REGION="your-aws-region"
```

### Simple Training Example

```python
from receipt_trainer import ReceiptTrainer

trainer = ReceiptTrainer(
    wandb_project="receipt-ocr",
    model_name="microsoft/layoutlm-base-uncased"
)

# Load data
dataset = trainer.load_data()

# Start training
trainer.train()
```

## Data Preparation

### Data Sources

The Receipt Trainer supports two main data sources:
1. DynamoDB table containing receipt data
2. SROIE dataset (automatically downloaded)

### Data Configuration

```python
from receipt_trainer import DataConfig

data_config = DataConfig(
    env="dev",
    use_sroie=True,
    balance_ratio=0.7,
    augment=True,
    sliding_window_size=50,
    sliding_window_overlap=10
)
```

### Data Augmentation

Data augmentation is enabled by default and includes:
- Random rotations
- Random scaling
- Random translations
- Text augmentation techniques

To disable augmentation:
```python
dataset = trainer.load_data(augment=False)
```

## Training Configuration

### Basic Configuration

```python
from receipt_trainer import TrainingConfig

training_config = TrainingConfig(
    batch_size=16,
    learning_rate=3e-4,
    num_epochs=20,
    gradient_accumulation_steps=32
)
```

### Mixed Precision Training

Mixed precision training is enabled by default using bfloat16:

```python
training_config = TrainingConfig(
    bf16=True  # Enable bfloat16 mixed precision
)
```

### Early Stopping

```python
training_config = TrainingConfig(
    early_stopping_patience=5  # Stop if no improvement for 5 evaluations
)

trainer.train(enable_early_stopping=True)
```

## Distributed Training

### Multi-GPU Training

1. Configure distributed training:
```python
training_config = TrainingConfig(
    distributed_training=True,
    world_size=4,  # Number of GPUs
    ddp_backend="nccl"
)
```

2. Launch training:
```bash
python -m torch.distributed.launch --nproc_per_node=4 train_script.py
```

### Example Distributed Training Script

```python
# train_script.py
import os
from receipt_trainer import ReceiptTrainer, TrainingConfig

def main():
    training_config = TrainingConfig(
        distributed_training=True,
        local_rank=int(os.environ.get("LOCAL_RANK", -1)),
        world_size=int(os.environ.get("WORLD_SIZE", 1))
    )
    
    trainer = ReceiptTrainer(
        wandb_project="receipt-ocr",
        training_config=training_config
    )
    
    trainer.train()

if __name__ == "__main__":
    main()
```

## Hyperparameter Optimization

### Setting Up W&B Sweeps

```python
sweep_config = {
    "method": "bayes",  # Bayesian optimization
    "metric": {
        "name": "validation/macro_avg/f1-score",
        "goal": "maximize"
    },
    "parameters": {
        "learning_rate": {
            "distribution": "log_uniform",
            "min": 1e-5,
            "max": 1e-3
        },
        "batch_size": {
            "values": [8, 16, 32]
        }
    }
}

trainer.run_sweep(
    sweep_config=sweep_config,
    num_trials=20
)
```

### Parallel Sweep Execution

```python
trainer.run_sweep(
    sweep_config=sweep_config,
    num_trials=20,
    parallel_workers=4,
    gpu_ids=[0, 1, 2, 3]
)
```

## Performance Optimization

### Gradient Accumulation

For larger effective batch sizes:
```python
training_config = TrainingConfig(
    batch_size=16,
    gradient_accumulation_steps=32  # Effective batch size = 16 * 32
)
```

### Memory Optimization

1. Enable mixed precision training (default)
2. Use gradient accumulation for larger batches
3. Use sliding windows for long documents:
```python
data_config = DataConfig(
    sliding_window_size=50,
    sliding_window_overlap=10
)
```

## Monitoring and Logging

### Weights & Biases Integration

Training metrics automatically logged to W&B:
- Training loss
- Validation metrics
- Learning rate
- GPU utilization
- Memory usage

### Local Logging

```python
trainer = ReceiptTrainer(
    wandb_project="receipt-ocr",
    log_to_wandb=True
)
```

### Checkpointing

```python
trainer.train(
    enable_checkpointing=True,
    resume_training=True  # Resume from last checkpoint if available
)
```

## Troubleshooting

### Common Issues

1. **Out of Memory Errors**
   - Reduce batch size
   - Enable mixed precision training
   - Increase gradient accumulation steps
   - Use sliding windows

2. **Slow Training**
   - Check GPU utilization
   - Optimize data loading pipeline
   - Use distributed training
   - Enable mixed precision training

3. **Poor Performance**
   - Check data quality
   - Adjust learning rate
   - Increase training epochs
   - Try different model architectures
   - Run hyperparameter optimization

### Best Practices

1. **Data Quality**
   - Validate input data
   - Balance dataset
   - Use appropriate augmentation

2. **Training Process**
   - Start with small experiments
   - Monitor validation metrics
   - Use early stopping
   - Save checkpoints regularly

3. **Resource Management**
   - Monitor GPU memory usage
   - Use appropriate batch sizes
   - Enable mixed precision training
   - Use distributed training for large datasets 
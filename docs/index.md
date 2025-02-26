# Receipt Trainer Documentation

Welcome to the Receipt Trainer documentation. This documentation will help you understand and use the Receipt Trainer package for training LayoutLM models on receipt data.

## Contents

1. [Training Guide](training_guide.md)
   - Basic Training
   - Data Preparation
   - Training Configuration
   - Distributed Training
   - Performance Optimization
   - Troubleshooting

2. [API Documentation](api.md)
   - Core Classes
   - Configuration Classes
   - Utility Functions
   - Environment Setup
   - Example Usage

## Quick Start

The Receipt Trainer package provides a high-level interface for training LayoutLM models on receipt data. Here's a minimal example to get started:

```python
from receipt_trainer import ReceiptTrainer

# Initialize trainer
trainer = ReceiptTrainer(
    wandb_project="receipt-ocr",
    model_name="microsoft/layoutlm-base-uncased"
)

# Load and prepare data
dataset = trainer.load_data()

# Train model
trainer.train()
```

For more detailed examples and API documentation, please refer to the [API Documentation](api.md).

## Requirements

- Python 3.8+
- PyTorch 1.8+
- Transformers 4.5+
- Weights & Biases account
- AWS account (for DynamoDB and S3)

## Installation

```bash
pip install receipt-trainer
```

## Contributing

We welcome contributions! Please see our [contribution guidelines](CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 
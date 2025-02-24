# Receipt Trainer

A Python package for training LayoutLM models on receipt data, with support for DynamoDB data sources and SROIE dataset integration.

## Features

- Load and process receipt data from DynamoDB
- Integrate with SROIE dataset
- Train LayoutLM models for receipt information extraction
- Track experiments with Weights & Biases
- Handle AWS infrastructure with Pulumi

## Installation

```bash
# Install from source
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"
```

## Usage

```python
from receipt_trainer import ReceiptTrainer

# Initialize trainer
trainer = ReceiptTrainer(
    wandb_project="receipt-training",
    model_name="microsoft/layoutlm-base-uncased",
    data_config=DataConfig(
        use_sroie=True,
        env="prod"
    )
)

# Load data
dataset = trainer.load_data()

# Train model
trainer.train()
```

## Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov

# Format code
black receipt_trainer tests
isort receipt_trainer tests

# Type checking
mypy receipt_trainer
```

## Project Structure

```
receipt_trainer/
├── pyproject.toml        # Package configuration
├── README.md            # This file
├── receipt_trainer/     # Package source
│   ├── __init__.py     # Package initialization
│   ├── trainer.py      # Main trainer class
│   ├── config.py       # Configuration classes
│   └── utils/          # Utility functions
│       ├── __init__.py
│       ├── data.py     # Data processing utilities
│       └── aws.py      # AWS integration utilities
├── tests/              # Test suite
│   ├── __init__.py
│   ├── conftest.py     # Test configuration
│   ├── test_trainer.py # Unit tests
│   └── test_integration.py # Integration tests
└── examples/           # Usage examples
    └── train_model.py  # Example training script
```

## License

MIT 
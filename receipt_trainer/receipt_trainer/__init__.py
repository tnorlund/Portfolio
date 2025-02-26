"""Receipt Trainer package."""

from .version import __version__
from .trainer import ReceiptTrainer
from .config import TrainingConfig, DataConfig
from .utils.data import (
    create_sliding_windows,
    balance_dataset,
    augment_example,
)

__all__ = [
    "__version__",
    "ReceiptTrainer",
    "TrainingConfig",
    "DataConfig",
    "create_sliding_windows",
    "balance_dataset",
    "augment_example",
]

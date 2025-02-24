"""Configuration classes for Receipt Trainer."""

from dataclasses import dataclass
from typing import Optional


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

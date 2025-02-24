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
    # Distributed training parameters
    distributed_training: bool = False
    local_rank: int = -1  # Process rank within node
    world_size: int = 1  # Total number of processes
    ddp_backend: str = "nccl"  # DDP backend (nccl for GPU, gloo for CPU)
    find_unused_parameters: bool = False  # Whether to find unused parameters in DDP
    sync_bn: bool = False  # Whether to use SyncBatchNorm in distributed training


class DataConfig:
    """Configuration for data loading and processing."""

    def __init__(
        self,
        env: str = "dev",
        cache_dir: Optional[str] = None,
        use_sroie: bool = True,
        balance_ratio: float = 0.7,
        augment: bool = True,
        sliding_window_size: int = 50,
        sliding_window_overlap: int = 10,
        max_length: int = 512,
    ):
        """Initialize data configuration.
        
        Args:
            env: Environment name (dev/prod)
            cache_dir: Directory for caching data
            use_sroie: Whether to include SROIE dataset
            balance_ratio: Target ratio of entity tokens to total tokens
            augment: Whether to apply data augmentation
            sliding_window_size: Size of sliding windows (0 to disable)
            sliding_window_overlap: Number of overlapping tokens between windows
            max_length: Maximum sequence length for tokenization
        """
        self.env = env
        self.cache_dir = cache_dir
        self.use_sroie = use_sroie
        self.balance_ratio = balance_ratio
        self.augment = augment
        self.sliding_window_size = sliding_window_size
        self.sliding_window_overlap = sliding_window_overlap
        self.max_length = max_length
        
        # Aliases for backward compatibility with new code
        self.window_size = self.sliding_window_size
        self.window_overlap = self.sliding_window_overlap

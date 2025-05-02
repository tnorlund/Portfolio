"""Receipt Trainer package."""

from .version import __version__
from .trainer import ReceiptTrainer
from .config import TrainingConfig, DataConfig
from .utils.data import (
    create_sliding_windows,
    balance_dataset,
    augment_example,
)

# Import job queue system
from .jobs import (
    Job,
    JobStatus,
    JobPriority,
    JobQueue,
    JobQueueConfig,
    JobRetryStrategy,
)

__all__ = [
    "__version__",
    "ReceiptTrainer",
    "TrainingConfig",
    "DataConfig",
    "create_sliding_windows",
    "balance_dataset",
    "augment_example",
    # Job queue system
    "Job",
    "JobStatus",
    "JobPriority",
    "JobQueue",
    "JobQueueConfig",
    "JobRetryStrategy",
]

"""Compaction operations for vector store data."""

from .compaction_engine import CompactionEngine
from .delta_processor import DeltaProcessor

# Move lock_manager from utils to compaction since it's primarily used there
try:
    from receipt_label.utils.lock_manager import LockManager
    __all__ = ["CompactionEngine", "DeltaProcessor", "LockManager"]
except ImportError:
    __all__ = ["CompactionEngine", "DeltaProcessor"]
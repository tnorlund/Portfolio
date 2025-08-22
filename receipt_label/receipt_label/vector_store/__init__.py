"""
Vector Store Module for Receipt Label System.

This module provides a clean, organized interface to vector storage systems,
replacing the scattered ChromaDB utilities with a well-structured architecture.

The module supports:
- Multiple vector store backends (ChromaDB, with extensibility for others)
- S3-based storage and synchronization
- Delta processing and compaction
- Integration with decision engines and pattern matching

Usage:
    # Simple client creation
    from receipt_label.vector_store import VectorClient
    client = VectorClient.create_chromadb_client(mode="read")
    
    # Storage operations
    from receipt_label.vector_store.storage import SnapshotManager
    manager = SnapshotManager(bucket="my-bucket", prefix="snapshots/")
    
    # Compaction operations
    from receipt_label.vector_store.compaction import CompactionEngine
    engine = CompactionEngine(collection="receipt_words")
"""

# Core interfaces and factories
from .client.factory import VectorClient
from .client.base import VectorStoreInterface
from .client.chromadb_client import ChromaDBClient

# Storage operations
from .storage.snapshot_manager import SnapshotManager
from .storage.s3_operations import S3Operations
from .storage.hash_calculator import HashCalculator, HashResult

# Compaction operations
from .compaction.compaction_engine import CompactionEngine
from .compaction.delta_processor import DeltaProcessor

# Integration modules
from .integration.decision_engine import VectorDecisionEngine, MerchantReliabilityData
from .integration.pattern_matching import PatternMatcher

# Legacy compatibility functions
from .client.factory import (
    get_chroma_client,
    get_word_client, 
    get_line_client
)

__all__ = [
    # Core interfaces
    "VectorClient", 
    "VectorStoreInterface",
    "ChromaDBClient",
    
    # Storage operations
    "SnapshotManager",
    "S3Operations", 
    "HashCalculator",
    "HashResult",
    
    # Compaction operations
    "CompactionEngine",
    "DeltaProcessor",
    
    # Integration modules
    "VectorDecisionEngine",
    "MerchantReliabilityData",
    "PatternMatcher",
    
    # Legacy compatibility
    "get_chroma_client",
    "get_word_client",
    "get_line_client",
]

# Version info
__version__ = "1.0.0"
__author__ = "Receipt Label System"
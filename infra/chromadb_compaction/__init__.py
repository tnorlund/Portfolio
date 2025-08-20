"""
ChromaDB Compaction Infrastructure

This package provides the Pulumi infrastructure for the ChromaDB S3 compaction
pipeline with DynamoDB mutex protection.

Architecture:
- Multiple producer Lambdas write delta files to S3
- SQS queue collects delta notifications
- Single compactor Lambda merges deltas with distributed lock
- Query Lambdas read from consistent snapshots

See README.md for detailed documentation and operational procedures.
"""

from .components import (
    ChromaDBBuckets, 
    create_chromadb_buckets,
    ChromaDBQueues, 
    create_chromadb_queues,
    EnhancedCompactionLambda, 
    create_enhanced_compaction_lambda,
    create_stream_processor,
)

__all__ = [
    "ChromaDBBuckets",
    "create_chromadb_buckets",
    "ChromaDBQueues", 
    "create_chromadb_queues",
    "EnhancedCompactionLambda",
    "create_enhanced_compaction_lambda",
    "create_stream_processor",
]
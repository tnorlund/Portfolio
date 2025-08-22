"""
ChromaDB Compaction Infrastructure

This package provides the Pulumi infrastructure for the ChromaDB S3 compaction
pipeline with DynamoDB mutex protection.

Architecture:
- DynamoDB stream processor Lambda (zip-based, lightweight)
- Enhanced compaction Lambda (container-based, complex ChromaDB operations)
- SQS queues for message passing between components
- S3 buckets for ChromaDB snapshots and metadata
- Hybrid deployment strategy for optimal cost and performance

See README.md for detailed documentation and operational procedures.
"""

from .components import (
    ChromaDBBuckets,
    create_chromadb_buckets,
    ChromaDBQueues,
    create_chromadb_queues,
    DockerImageComponent,
    HybridLambdaDeployment,
    create_hybrid_lambda_deployment,
    EnhancedCompactionLambda,
    create_enhanced_compaction_lambda,
    create_stream_processor,
)

from .infrastructure import (
    ChromaDBCompactionInfrastructure,
    create_chromadb_compaction_infrastructure,
)

# pylint: disable=duplicate-code
# Export lists are expected to be similar between package __init__ files
__all__ = [
    # Infrastructure
    "ChromaDBCompactionInfrastructure",
    "create_chromadb_compaction_infrastructure",
    # Components
    "ChromaDBBuckets",
    "create_chromadb_buckets",
    "ChromaDBQueues",
    "create_chromadb_queues",
    "DockerImageComponent",
    "HybridLambdaDeployment",
    "create_hybrid_lambda_deployment",
    "EnhancedCompactionLambda",
    "create_enhanced_compaction_lambda",
    "create_stream_processor",
]

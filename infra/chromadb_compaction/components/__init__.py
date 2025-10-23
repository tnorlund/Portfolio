"""
ChromaDB Compaction Pulumi Components

This package contains the Pulumi infrastructure components for ChromaDB
compaction:
- s3_buckets: S3 bucket resources for storing ChromaDB snapshots
- sqs_queues: SQS queue resources for message passing
- docker_image: Docker image building for container-based Lambda
- lambda_functions: Hybrid Lambda deployment (zip + container)
"""

from .s3_buckets import ChromaDBBuckets, create_chromadb_buckets
from .sqs_queues import ChromaDBQueues, create_chromadb_queues
from .docker_image import DockerImageComponent
from .lambda_functions import (
    HybridLambdaDeployment,
    create_hybrid_lambda_deployment,
)

# pylint: disable=duplicate-code
# Export lists are expected to be similar between package __init__ files
__all__ = [
    "ChromaDBBuckets",
    "create_chromadb_buckets",
    "ChromaDBQueues",
    "create_chromadb_queues",
    "DockerImageComponent",
    "HybridLambdaDeployment",
    "create_hybrid_lambda_deployment",
]

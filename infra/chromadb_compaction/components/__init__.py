"""
ChromaDB Compaction Pulumi Components

This package contains the Pulumi infrastructure components for ChromaDB
compaction:
- s3_buckets: S3 bucket resources for storing ChromaDB snapshots
- sqs_queues: SQS queue resources for message passing
- docker_image: Docker image building for container-based Lambda
- lambda_functions: Hybrid Lambda deployment (zip + container)
- enhanced_compaction_infra: Enhanced compaction Lambda infrastructure
- stream_processor_infra: DynamoDB stream processor Lambda infrastructure
"""

from .s3_buckets import ChromaDBBuckets, create_chromadb_buckets
from .sqs_queues import ChromaDBQueues, create_chromadb_queues
from .docker_image import DockerImageComponent
from .lambda_functions import (
    HybridLambdaDeployment,
    create_hybrid_lambda_deployment,
)
from .enhanced_compaction_infra import (
    EnhancedCompactionLambda,
    create_enhanced_compaction_lambda,
)
from .stream_processor_infra import create_stream_processor

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
    "EnhancedCompactionLambda",
    "create_enhanced_compaction_lambda",
    "create_stream_processor",
]

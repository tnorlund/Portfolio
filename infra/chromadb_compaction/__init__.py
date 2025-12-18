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

import os
import sys
from pathlib import Path

# Ensure infra/ directory is in sys.path so that modules can use
# "from infra." imports. This is necessary because chromadb_compaction
# can be imported as a top-level module, breaking the package hierarchy.
_infra_dir = Path(__file__).parent.parent  # infra/
if str(_infra_dir) not in sys.path:
    sys.path.insert(0, str(_infra_dir))

# Skip infrastructure imports when running tests
# This allows Lambda function tests to run without Pulumi dependencies
if os.getenv("PYTEST_RUNNING") == "1":
    __all__ = []
else:
    from .components import (
        ChromaDBBuckets,
        ChromaDBQueues,
        DockerImageComponent,
        HybridLambdaDeployment,
        create_chromadb_buckets,
        create_chromadb_queues,
        create_hybrid_lambda_deployment,
    )
    from .infrastructure import (
        ChromaDBCompactionInfrastructure,
        create_chromadb_compaction_infrastructure,
    )

    # pylint: disable=duplicate-code
    # Export lists are expected to be similar between package __init__ files
    __all__ = [
        # Components
        "ChromaDBBuckets",
        "ChromaDBQueues",
        "DockerImageComponent",
        "HybridLambdaDeployment",
        "create_chromadb_buckets",
        "create_chromadb_queues",
        "create_hybrid_lambda_deployment",
        # Infrastructure
        "ChromaDBCompactionInfrastructure",
        "create_chromadb_compaction_infrastructure",
    ]

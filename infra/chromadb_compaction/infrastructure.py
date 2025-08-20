"""Main infrastructure orchestration for ChromaDB compaction system.

Coordinates all components including SQS queues, S3 buckets, and hybrid Lambda deployment.
"""
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments
# Infrastructure components naturally have many attributes and configuration parameters

from typing import Optional

from pulumi import ComponentResource, ResourceOptions

from .components.lambda_functions import create_hybrid_lambda_deployment
from .components.s3_buckets import create_chromadb_buckets
from .components.sqs_queues import create_chromadb_queues


class ChromaDBCompactionInfrastructure(ComponentResource):
    """
    Main infrastructure component for ChromaDB compaction system.

    Creates and coordinates:
    - SQS queues for message processing
    - S3 buckets for ChromaDB snapshots
    - Hybrid Lambda deployment (zip + container)
    """

    def __init__(
        self,
        name: str,
        dynamodb_table_arn: str,
        dynamodb_stream_arn: str,
        base_images=None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize the ChromaDB compaction infrastructure.

        Args:
            name: The unique name of the resource
            dynamodb_table_arn: ARN of the DynamoDB table
            dynamodb_stream_arn: ARN of the DynamoDB stream
            base_images: Base images for container builds
            opts: Optional resource options
        """
        super().__init__(
            "chromadb:compaction:Infrastructure", name, None, opts
        )

        # Create SQS queues for message processing
        self.chromadb_queues = create_chromadb_queues(
            name=f"{name}-queues",
            opts=ResourceOptions(parent=self),
        )

        # Create S3 buckets for ChromaDB snapshots
        self.chromadb_buckets = create_chromadb_buckets(
            name=f"{name}-buckets",
            opts=ResourceOptions(parent=self),
        )

        # Create hybrid Lambda deployment
        self.lambda_deployment = create_hybrid_lambda_deployment(
            name=f"{name}-lambdas",
            chromadb_queues=self.chromadb_queues,
            chromadb_buckets=self.chromadb_buckets,
            dynamodb_table_arn=dynamodb_table_arn,
            dynamodb_stream_arn=dynamodb_stream_arn,
            base_images=base_images,
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.lines_queue_url = self.chromadb_queues.lines_queue_url
        self.words_queue_url = self.chromadb_queues.words_queue_url
        self.bucket_name = self.chromadb_buckets.bucket_name
        self.stream_processor_arn = self.lambda_deployment.stream_processor_arn
        self.enhanced_compaction_arn = (
            self.lambda_deployment.enhanced_compaction_arn
        )

        # Register outputs
        self.register_outputs(
            {
                "lines_queue_url": self.lines_queue_url,
                "words_queue_url": self.words_queue_url,
                "bucket_name": self.bucket_name,
                "stream_processor_arn": self.stream_processor_arn,
                "enhanced_compaction_arn": self.enhanced_compaction_arn,
                "docker_image_uri": self.lambda_deployment.docker_image.image_uri,
            }
        )


def create_chromadb_compaction_infrastructure(
    name: str = "chromadb-compaction",
    dynamodb_table_arn: str = None,
    dynamodb_stream_arn: str = None,
    base_images=None,
    opts: Optional[ResourceOptions] = None,
) -> ChromaDBCompactionInfrastructure:
    """
    Factory function to create the complete ChromaDB compaction infrastructure.

    Args:
        name: Base name for the resources
        dynamodb_table_arn: ARN of the DynamoDB table
        dynamodb_stream_arn: ARN of the DynamoDB stream
        base_images: Base images for container builds
        opts: Optional resource options

    Returns:
        ChromaDBCompactionInfrastructure component
    """
    if not dynamodb_table_arn:
        raise ValueError("dynamodb_table_arn parameter is required")
    if not dynamodb_stream_arn:
        raise ValueError("dynamodb_stream_arn parameter is required")

    return ChromaDBCompactionInfrastructure(
        name=name,
        dynamodb_table_arn=dynamodb_table_arn,
        dynamodb_stream_arn=dynamodb_stream_arn,
        base_images=base_images,
        opts=opts,
    )

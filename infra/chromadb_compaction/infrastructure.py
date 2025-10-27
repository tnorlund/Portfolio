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
from .components.efs import ChromaEfs


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
        chromadb_buckets=None,
        vpc_id: str | None = None,
        subnet_ids=None,
        lambda_security_group_id: str | None = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize the ChromaDB compaction infrastructure.

        Args:
            name: The unique name of the resource
            dynamodb_table_arn: ARN of the DynamoDB table
            dynamodb_stream_arn: ARN of the DynamoDB stream
            chromadb_buckets: Shared ChromaDB S3 buckets component
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

        # Use provided ChromaDB buckets or create new ones
        if chromadb_buckets is not None:
            self.chromadb_buckets = chromadb_buckets
        else:
            # Fallback: create S3 buckets for ChromaDB snapshots
            self.chromadb_buckets = create_chromadb_buckets(
                name=f"{name}-buckets",
                opts=ResourceOptions(parent=self),
            )

        # Optionally create EFS for Chroma if networking details provided
        self.efs = None
        if vpc_id and subnet_ids and lambda_security_group_id:
            self.efs = ChromaEfs(
                f"{name}-efs",
                vpc_id=vpc_id,
                subnet_ids=subnet_ids,
                lambda_security_group_id=lambda_security_group_id,
                opts=ResourceOptions(parent=self),
            )

        # Create hybrid Lambda deployment
        self.hybrid_deployment = create_hybrid_lambda_deployment(
            name=f"{name}",
            chromadb_queues=self.chromadb_queues,
            chromadb_buckets=self.chromadb_buckets,
            dynamodb_table_arn=dynamodb_table_arn,
            dynamodb_stream_arn=dynamodb_stream_arn,
            vpc_subnet_ids=subnet_ids,
            lambda_security_group_id=lambda_security_group_id,
            efs_access_point_arn=(
                self.efs.access_point_arn if self.efs else None
            ),
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.lines_queue_url = self.chromadb_queues.lines_queue_url
        self.words_queue_url = self.chromadb_queues.words_queue_url
        self.bucket_name = self.chromadb_buckets.bucket_name
        self.stream_processor_arn = self.hybrid_deployment.stream_processor_arn
        self.enhanced_compaction_arn = self.hybrid_deployment.enhanced_compaction_arn

        # Register outputs
        self.register_outputs(
            {
                "lines_queue_url": self.lines_queue_url,
                "words_queue_url": self.words_queue_url,
                "bucket_name": self.bucket_name,
                "stream_processor_arn": self.stream_processor_arn,
                "enhanced_compaction_arn": self.enhanced_compaction_arn,
                "efs_access_point_arn": (
                    self.efs.access_point_arn if self.efs else None
                ),
            }
        )


def create_chromadb_compaction_infrastructure(
    name: str = "chromadb-compaction",
    dynamodb_table_arn: str = None,
    dynamodb_stream_arn: str = None,
    chromadb_buckets=None,
    vpc_id: str | None = None,
    subnet_ids=None,
    lambda_security_group_id: str | None = None,
    opts: Optional[ResourceOptions] = None,
) -> ChromaDBCompactionInfrastructure:
    """
    Factory function to create the complete ChromaDB compaction infrastructure.

    Args:
        name: Base name for the resources
        dynamodb_table_arn: ARN of the DynamoDB table
        dynamodb_stream_arn: ARN of the DynamoDB stream
        chromadb_buckets: Shared ChromaDB S3 buckets component
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
        chromadb_buckets=chromadb_buckets,
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        lambda_security_group_id=lambda_security_group_id,
        opts=opts,
    )

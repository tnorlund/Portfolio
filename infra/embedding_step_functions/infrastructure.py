"""Infrastructure for embedding step functions.

This provides both zip-based Lambda functions for simple operations
and container-based Lambda functions for ChromaDB operations.

This file has been refactored to use modular components for better
code organization and easier maintenance of the 79-character line limit.
"""

from typing import Optional

from pulumi import ComponentResource, Output, ResourceOptions

# pylint: disable=import-error
from chromadb_compaction import (  # type: ignore[import-not-found]
    ChromaDBBuckets,
)

from .components import (
    DockerImageComponent,
    LambdaFunctionsComponent,
    LineEmbeddingWorkflow,
    MonitoringComponent,
    RealtimeEmbeddingWorkflow,
    WordEmbeddingWorkflow,
)

# pylint: enable=import-error


class EmbeddingInfrastructure(ComponentResource):
    """Infrastructure with both zip and container Lambda functions.

    Simple functions (list_pending, find_unembedded, submit_openai) use
    zip deployment. Complex functions (line_polling, word_polling,
    compaction) use container deployment.
    """

    def __init__(
        self,
        name: str,
        chromadb_queues,
        chromadb_buckets=None,
        vpc_subnet_ids=None,
        lambda_security_group_id=None,
        efs_access_point_arn=None,
        efs_mount_targets=None,  # Mount targets dependency for Lambda
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize embedding infrastructure.

        Args:
            name: Component name
            chromadb_queues: ChromaDB SQS queues component (from chromadb_compaction)
            chromadb_buckets: Shared ChromaDB S3 buckets component
            vpc_subnet_ids: Subnet IDs for Lambda VPC configuration
            lambda_security_group_id: Security group ID for Lambda VPC access
            efs_access_point_arn: EFS access point ARN for ChromaDB storage
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:Infrastructure",
            name,
            None,
            opts,
        )

        # Use provided ChromaDB queues instead of creating our own
        self.chromadb_queues = chromadb_queues

        # Use provided ChromaDB buckets or create new ones
        if chromadb_buckets is not None:
            self.chromadb_buckets = chromadb_buckets
        else:
            # Fallback: create ChromaDB buckets for embedding-specific storage
            self.chromadb_buckets = ChromaDBBuckets(
                f"{name}-chromadb-buckets",
                opts=ResourceOptions(parent=self),
            )

        # Create Docker image component
        self.docker = DockerImageComponent(
            f"{name}-docker",
            opts=ResourceOptions(parent=self),
        )

        # Create Lambda functions component
        # Pass mount targets dependency so Lambda waits for EFS mount targets to be available
        # This is critical - Lambda will timeout if it tries to mount EFS before mount targets are ready
        self.lambdas = LambdaFunctionsComponent(
            f"{name}-lambdas",
            chromadb_buckets=self.chromadb_buckets,
            chromadb_queues=self.chromadb_queues,
            docker_image_component=self.docker,
            vpc_subnet_ids=vpc_subnet_ids,
            lambda_security_group_id=lambda_security_group_id,
            efs_access_point_arn=efs_access_point_arn,
            efs_mount_targets=efs_mount_targets,  # Pass mount targets dependency
            opts=ResourceOptions(parent=self),
        )

        # Create workflow components
        self.line_workflow = LineEmbeddingWorkflow(
            f"{name}-line",
            lambda_functions=self.lambdas.all_functions,
            batch_bucket=self.lambdas.batch_bucket,
            opts=ResourceOptions(parent=self),
        )

        self.word_workflow = WordEmbeddingWorkflow(
            f"{name}-word",
            lambda_functions=self.lambdas.all_functions,
            batch_bucket=self.lambdas.batch_bucket,
            opts=ResourceOptions(parent=self),
        )

        # Create realtime embedding workflow
        self.realtime_workflow = RealtimeEmbeddingWorkflow(
            f"{name}-realtime",
            lambda_functions=self.lambdas.all_functions,
            opts=ResourceOptions(parent=self),
        )

        # Create monitoring component
        self.monitoring = MonitoringComponent(
            f"{name}-monitoring",
            lambda_functions=self.lambdas.all_functions,
            step_functions={
                "line_submit": self.line_workflow.submit_sf,
                "line_ingest": self.line_workflow.ingest_sf,
                "word_submit": self.word_workflow.submit_sf,
                "word_ingest": self.word_workflow.ingest_sf,
            },
            opts=ResourceOptions(parent=self),
        )

        # Legacy mappings for backward compatibility
        self._setup_legacy_mappings()

        # Register outputs
        self._register_outputs()

    def _setup_legacy_mappings(self):
        """Set up legacy mappings for backward compatibility."""
        # Map old names to new components for compatibility
        self.batch_bucket = self.lambdas.batch_bucket
        self.lambda_role = self.lambdas.lambda_role
        self.ecr_repo = self.docker.ecr_repo
        self.docker_image = self.docker.docker_image

        # Legacy Step Function mappings
        self.embedding_line_submit_sf = self.line_workflow.submit_sf
        self.embedding_line_ingest_sf = self.line_workflow.ingest_sf
        self.embedding_word_submit_sf = self.word_workflow.submit_sf
        self.embedding_word_ingest_sf = self.word_workflow.ingest_sf

        # Legacy names for compatibility
        self.create_batches_sf = self.embedding_line_submit_sf
        self.poll_and_store_sf = self.embedding_line_ingest_sf
        self.create_word_batches_sf = self.embedding_word_submit_sf
        self.poll_word_embeddings_sf = self.embedding_word_ingest_sf

        # Lambda function mappings
        self.zip_lambda_functions = self.lambdas.zip_lambda_functions
        self.container_lambda_functions = (
            self.lambdas.container_lambda_functions
        )

        # Additional legacy mappings
        if hasattr(self, "container_lambda_functions"):
            self.container_lambda_functions["line-polling"] = (
                self.container_lambda_functions.get("embedding-poll-lines")
            )
            self.container_lambda_functions["word-polling"] = (
                self.container_lambda_functions.get("embedding-poll-words")
            )
            self.container_lambda_functions["compaction"] = (
                self.container_lambda_functions.get("embedding-compact")
            )

    def _register_outputs(self):
        """Register all outputs for the infrastructure."""
        self.register_outputs(
            {
                "docker_image_uri": (
                    Output.all(
                        self.ecr_repo.repository_url,
                        self.docker_image.digest,
                    ).apply(lambda args: f"{args[0].split(':')[0]}@{args[1]}")
                    if hasattr(self, "docker_image")
                    else None
                ),
                "chromadb_bucket_name": self.chromadb_buckets.bucket_name,
                "chromadb_queue_url": self.chromadb_queues.lines_queue_url,
                "batch_bucket_name": self.batch_bucket.bucket,
                # Line workflows
                "embedding_line_submit_sf_arn": (
                    self.embedding_line_submit_sf.arn
                ),
                "embedding_line_ingest_sf_arn": (
                    self.embedding_line_ingest_sf.arn
                ),
                # Word workflows
                "embedding_word_submit_sf_arn": (
                    self.embedding_word_submit_sf.arn
                ),
                "embedding_word_ingest_sf_arn": (
                    self.embedding_word_ingest_sf.arn
                ),
                # Legacy names
                "create_batches_sf_arn": self.create_batches_sf.arn,
                "poll_and_store_sf_arn": self.poll_and_store_sf.arn,
                "create_word_batches_sf_arn": (
                    self.create_word_batches_sf.arn
                ),
                "poll_word_embeddings_sf_arn": (
                    self.poll_word_embeddings_sf.arn
                ),
                # Realtime workflow
                "realtime_embedding_sf_arn": (
                    self.realtime_workflow.realtime_sf.arn
                ),
                # Monitoring outputs
                "alert_topic_arn": self.monitoring.alert_topic.arn,
            }
        )

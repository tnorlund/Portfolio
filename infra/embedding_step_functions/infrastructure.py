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
    ChromaDBQueues,
)

# pylint: enable=import-error

from .components import (
    DockerImageComponent,
    LambdaFunctionsComponent,
    LineEmbeddingWorkflow,
    WordEmbeddingWorkflow,
)


class EmbeddingInfrastructure(ComponentResource):
    """Infrastructure with both zip and container Lambda functions.

    Simple functions (list_pending, find_unembedded, submit_openai) use
    zip deployment. Complex functions (line_polling, word_polling,
    compaction) use container deployment.
    """

    def __init__(
        self,
        name: str,
        base_images=None,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize embedding infrastructure.

        Args:
            name: Component name
            base_images: Optional base images for Docker builds
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:Infrastructure",
            name,
            None,
            opts,
        )

        self.base_images = base_images

        # Create ChromaDB infrastructure
        self.chromadb_buckets = ChromaDBBuckets(
            f"{name}-chromadb-buckets",
            opts=ResourceOptions(parent=self),
        )

        self.chromadb_queues = ChromaDBQueues(
            f"{name}-chromadb-queues",
            opts=ResourceOptions(parent=self),
        )

        # Create Docker image component
        self.docker = DockerImageComponent(
            f"{name}-docker",
            base_images=base_images,
            opts=ResourceOptions(parent=self),
        )

        # Create Lambda functions component
        self.lambdas = LambdaFunctionsComponent(
            f"{name}-lambdas",
            chromadb_buckets=self.chromadb_buckets,
            chromadb_queues=self.chromadb_queues,
            docker_image_component=self.docker,
            opts=ResourceOptions(parent=self),
        )

        # Create workflow components
        self.line_workflow = LineEmbeddingWorkflow(
            f"{name}-line",
            lambda_functions=self.lambdas.all_functions,
            opts=ResourceOptions(parent=self),
        )

        self.word_workflow = WordEmbeddingWorkflow(
            f"{name}-word",
            lambda_functions=self.lambdas.all_functions,
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
                self.container_lambda_functions.get("embedding-line-poll")
            )
            self.container_lambda_functions["word-polling"] = (
                self.container_lambda_functions.get("embedding-word-poll")
            )
            self.container_lambda_functions["compaction"] = (
                self.container_lambda_functions.get(
                    "embedding-vector-compact"
                )
            )

    def _register_outputs(self):
        """Register all outputs for the infrastructure."""
        self.register_outputs(
            {
                "docker_image_uri": (
                    Output.all(
                        self.ecr_repo.repository_url,
                        self.docker_image.digest,
                    ).apply(
                        lambda args: f"{args[0].split(':')[0]}@{args[1]}"
                    )
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
            }
        )

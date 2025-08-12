"""Hybrid infrastructure for embedding step functions.

This provides both zip-based Lambda functions for simple operations
and container-based Lambda functions for ChromaDB operations.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any
import zipfile
import tempfile
import shutil

import pulumi
from pulumi import (
    ComponentResource,
    Config,
    Output,
    ResourceOptions,
    FileArchive,
)
from pulumi_aws import get_caller_identity
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
    LayerVersion,
)
from pulumi_aws.s3 import Bucket
from pulumi_aws.sfn import StateMachine
import pulumi_docker_build as docker_build

from chromadb_compaction import ChromaDBBuckets, ChromaDBQueues
from dynamo_db import dynamodb_table

# Import the existing Lambda layer for receipt packages
try:
    from lambda_layer import label_layer
except ImportError:
    label_layer = None

# Configuration
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
stack = pulumi.get_stack()


class HybridEmbeddingInfrastructure(ComponentResource):
    """Hybrid infrastructure with both zip and container Lambda functions.

    Simple functions (list_pending, find_unembedded, submit_openai) use zip deployment.
    Complex functions (line_polling, word_polling, compaction) use container deployment.
    """

    def __init__(
        self,
        name: str,
        base_images=None,  # Add base_images dependency
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            "custom:embedding:HybridInfrastructure",
            name,
            None,
            opts,
        )

        # Store base_images dependency for use in _build_docker_image
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

        # Create S3 bucket for NDJSON batch files
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Create shared IAM role for Lambda functions
        self._create_lambda_role()

        # Build Docker image for container-based functions
        self._build_docker_image()

        # Create zip-based Lambda functions (simple, fast)
        self._create_zip_lambda_functions()

        # Create container-based Lambda functions (for ChromaDB)
        self._create_container_lambda_functions()

        # Create Step Functions
        self._create_step_functions()

        # Register outputs
        self.register_outputs(
            {
                "docker_image_uri": (
                    Output.all(self.ecr_repo.repository_url, self.docker_image.digest).apply(
                        lambda args: f"{args[0].split(':')[0]}@{args[1]}"
                    )
                    if hasattr(self, "docker_image")
                    else None
                ),
                "chromadb_bucket_name": self.chromadb_buckets.bucket_name,
                "chromadb_queue_url": self.chromadb_queues.delta_queue_url,
                "batch_bucket_name": self.batch_bucket.bucket,
                "create_batches_sf_arn": self.create_batches_sf.arn,
                "poll_and_store_sf_arn": self.poll_and_store_sf.arn,
                "create_word_batches_sf_arn": self.create_word_batches_sf.arn,
                "poll_word_embeddings_sf_arn": self.poll_word_embeddings_sf.arn,
            }
        )

    def _create_lambda_role(self):
        """Create shared IAM role for all Lambda functions."""

        self.lambda_role = Role(
            f"unified-lambda-role-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach basic execution policy
        RolePolicyAttachment(
            f"lambda-basic-execution-{stack}",
            role=self.lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for DynamoDB, S3, and SQS
        RolePolicy(
            f"lambda-permissions-{stack}",
            role=self.lambda_role.id,
            policy=Output.all(
                dynamodb_table.name,
                self.chromadb_buckets.bucket_name,
                self.chromadb_queues.delta_queue_arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:PutItem",
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:*:*:table/{args[0]}",
                                    f"arn:aws:dynamodb:*:*:table/{args[0]}/index/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:DeleteObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{args[1]}",
                                    f"arn:aws:s3:::{args[1]}/*",
                                    f"arn:aws:s3:::{args[3]}",
                                    f"arn:aws:s3:::{args[3]}/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[2],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

    def _create_zip_package(self, handler_dir: str) -> str:
        """Create a zip package for a Lambda function."""
        temp_dir = tempfile.mkdtemp()
        zip_path = f"{temp_dir}/function.zip"

        # Copy handler files
        src_dir = Path(__file__).parent / "simple_lambdas" / handler_dir

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Add handler.py
            handler_path = src_dir / "handler.py"
            if handler_path.exists():
                zipf.write(handler_path, "handler.py")

            # Note: receipt_label will come from Lambda layer

        return zip_path

    def _create_zip_lambda_functions(self):
        """Create simple, zip-based Lambda functions."""

        self.zip_lambda_functions = {}

        # Define zip-based Lambda configurations
        zip_configs = {
            "list-pending": {
                "handler": "handler.lambda_handler",
                "memory": 512,
                "timeout": 900,
                "source_dir": "list_pending",
            },
            "find-unembedded": {
                "handler": "handler.lambda_handler",
                "memory": 1024,
                "timeout": 900,
                "source_dir": "find_unembedded",
            },
            "find-unembedded-words": {
                "handler": "handler.lambda_handler",
                "memory": 1024,
                "timeout": 900,
                "source_dir": "find_unembedded_words",
            },
            "submit-openai": {
                "handler": "handler.lambda_handler",
                "memory": 1024,
                "timeout": 900,
                "source_dir": "submit_openai",
            },
            "submit-words-openai": {
                "handler": "handler.lambda_handler",
                "memory": 1024,
                "timeout": 900,
                "source_dir": "submit_words_openai",
            },
        }

        for name, config in zip_configs.items():
            # Create zip package
            source_path = (
                Path(__file__).parent / "simple_lambdas" / config["source_dir"]
            )

            # Common environment variables
            env_vars = {
                "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                "OPENAI_API_KEY": openai_api_key,
                "S3_BUCKET": self.batch_bucket.bucket,
            }

            # Create the Lambda function
            layers = []
            if label_layer:
                layers = [label_layer.arn]

            lambda_func = Function(
                f"{name}-lambda-{stack}",
                name=f"{name}-{stack}",
                runtime="python3.12",
                handler=config["handler"],
                code=FileArchive(str(source_path)),
                role=self.lambda_role.arn,
                memory_size=config["memory"],
                timeout=config["timeout"],
                environment=FunctionEnvironmentArgs(variables=env_vars),
                layers=layers,  # Use the receipt_label layer
                architectures=["arm64"],
                opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
            )

            self.zip_lambda_functions[name] = lambda_func

    def _build_docker_image(self):
        """Build the unified Docker image for container-based Lambda functions."""

        # Create ECR repository with versioned name to avoid conflicts
        self.ecr_repo = Repository(
            f"unified-embedding-v2-repo-{stack}",
            name=f"unified-embedding-v2-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Get ECR auth token
        ecr_auth_token = get_authorization_token_output()

        # Build context path (repository root)
        build_context_path = Path(__file__).parent.parent.parent

        # Build Docker image
        self.docker_image = docker_build.Image(
            f"unified-embedding-v2-image-{stack}",
            context={
                "location": str(build_context_path.resolve()),
            },
            dockerfile={
                "location": str(
                    (
                        Path(__file__).parent
                        / "unified_embedding"
                        / "Dockerfile"
                    ).resolve()
                ),
            },
            platforms=["linux/arm64"],
            build_args={
                "PYTHON_VERSION": "3.12",
            },
            push=True,
            registries=[
                {
                    "address": self.ecr_repo.repository_url.apply(
                        lambda url: url.split("/")[0]
                    ),
                    "password": ecr_auth_token.password,
                    "username": ecr_auth_token.user_name,
                }
            ],
            tags=[
                self.ecr_repo.repository_url.apply(
                    lambda url: f"{url}:latest"
                ),
            ],
            opts=ResourceOptions(
                parent=self, 
                depends_on=[self.ecr_repo] + ([self.base_images] if self.base_images else [])
            ),
        )

    def _create_container_lambda_functions(self):
        """Create container-based Lambda functions for ChromaDB operations."""

        # Define container-based Lambda configurations
        container_configs = {
            "line-polling": {
                "memory": 3008,
                "timeout": 900,
                "ephemeral_storage": 3072,
                "handler_type": "line_polling",
            },
            "word-polling": {
                "memory": 3008,
                "timeout": 900,
                "ephemeral_storage": 3072,
                "handler_type": "word_polling",
            },
            "compaction": {
                "memory": 4096,
                "timeout": 900,
                "ephemeral_storage": 5120,
                "handler_type": "compaction",
            },
        }

        # Create Lambda functions
        self.container_lambda_functions = {}
        for name, config in container_configs.items():
            env_vars = {
                "HANDLER_TYPE": config["handler_type"],
                "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                "COMPACTION_QUEUE_URL": self.chromadb_queues.delta_queue_url,
                "OPENAI_API_KEY": openai_api_key,
                "S3_BUCKET": self.batch_bucket.bucket,
                "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
            }

            # Add handler-specific environment variables
            if config["handler_type"] == "compaction":
                env_vars.update(
                    {
                        "CHUNK_SIZE": "10",
                        "HEARTBEAT_INTERVAL_SECONDS": "60",
                        "LOCK_DURATION_MINUTES": "5",
                        "DELETE_PROCESSED_DELTAS": "false",
                        "DELETE_INTERMEDIATE_CHUNKS": "true",
                    }
                )

            lambda_func = Function(
                f"{name}-lambda-{stack}",
                name=f"{name}-{stack}",
                package_type="Image",
                image_uri=Output.all(self.ecr_repo.repository_url, self.docker_image.digest).apply(
                    lambda args: f"{args[0].split(':')[0]}@{args[1]}"
                ),
                role=self.lambda_role.arn,
                architectures=["arm64"],
                memory_size=config["memory"],
                timeout=config["timeout"],
                environment=FunctionEnvironmentArgs(variables=env_vars),
                ephemeral_storage=(
                    FunctionEphemeralStorageArgs(
                        size=config.get("ephemeral_storage", 512)
                    )
                    if config.get("ephemeral_storage", 512) > 512
                    else None
                ),
                opts=ResourceOptions(
                    parent=self, depends_on=[self.docker_image]
                ),
            )

            self.container_lambda_functions[name] = lambda_func

    def _create_step_functions(self):
        """Create Step Functions for orchestration."""

        # Create IAM role for Step Functions
        self.sf_role = Role(
            f"sf-role-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Combine all Lambda functions for permissions
        all_lambda_arns = []
        all_lambda_arns.extend(
            [f.arn for f in self.zip_lambda_functions.values()]
        )
        all_lambda_arns.extend(
            [f.arn for f in self.container_lambda_functions.values()]
        )

        # Add permissions to invoke Lambda functions
        RolePolicy(
            f"sf-lambda-invoke-{stack}",
            role=self.sf_role.id,
            policy=Output.all(*all_lambda_arns).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": arns,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create the Create Embedding Batches Step Function
        self.create_batches_sf = StateMachine(
            f"create-batches-sf-{stack}",
            role_arn=self.sf_role.arn,
            definition=Output.all(
                self.zip_lambda_functions["find-unembedded"].arn,
                self.zip_lambda_functions["submit-openai"].arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Comment": "Find items without embeddings and submit to OpenAI",
                        "StartAt": "FindUnembedded",
                        "States": {
                            "FindUnembedded": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "SubmitBatches",
                            },
                            "SubmitBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 10,
                                "Iterator": {
                                    "StartAt": "SubmitToOpenAI",
                                    "States": {
                                        "SubmitToOpenAI": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                        },
                                    },
                                },
                                "End": True,
                            },
                        },
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create the Poll and Store Embeddings Step Function
        self.poll_and_store_sf = StateMachine(
            f"poll-store-sf-{stack}",
            role_arn=self.sf_role.arn,
            definition=Output.all(
                self.zip_lambda_functions["list-pending"].arn,
                self.container_lambda_functions["line-polling"].arn,
                self.container_lambda_functions["compaction"].arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Comment": "Poll OpenAI for completed batches and store in ChromaDB",
                        "StartAt": "ListPendingBatches",
                        "States": {
                            "ListPendingBatches": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "ResultPath": "$.pending_batches",
                                "Next": "CheckPendingBatches",
                            },
                            "CheckPendingBatches": {
                                "Type": "Choice",
                                "Choices": [
                                    {
                                        # Handle clean array response
                                        "Variable": "$.pending_batches[0]",
                                        "IsPresent": True,
                                        "Next": "PollBatches",
                                    },
                                ],
                                "Default": "NoPendingBatches",
                            },
                            "PollBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.pending_batches",
                                "MaxConcurrency": 10,
                                "Parameters": {
                                    "batch_id.$": "$$.Map.Item.Value.batch_id",
                                    "openai_batch_id.$": "$$.Map.Item.Value.openai_batch_id",
                                    "skip_sqs_notification": True,
                                },
                                "Iterator": {
                                    "StartAt": "PollBatch",
                                    "States": {
                                        "PollBatch": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                        },
                                    },
                                },
                                "ResultPath": "$.poll_results",
                                "Next": "PrepareChunkedCompaction",
                            },
                            "PrepareChunkedCompaction": {
                                "Type": "Pass",
                                "Comment": "Prepare data for chunked compaction",
                                "Parameters": {
                                    "batch_id.$": "$$.Execution.Name",
                                    "delta_results.$": "$.poll_results",
                                    "chunk_index": 0,
                                    "total_chunks_processed": 0,
                                    "operation": "process_chunk",
                                },
                                "Next": "CheckForDeltas",
                            },
                            "CheckForDeltas": {
                                "Type": "Choice",
                                "Comment": "Check if there are deltas to process",
                                "Choices": [
                                    {
                                        "Variable": "$.delta_results[0]",
                                        "IsPresent": True,
                                        "Next": "ProcessChunk",
                                    }
                                ],
                                "Default": "FinalMerge",
                            },
                            "ProcessChunk": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Comment": "Process a chunk of deltas (max 10)",
                                "Parameters": {
                                    "operation": "process_chunk",
                                    "batch_id.$": "$.batch_id",
                                    "chunk_index.$": "$.chunk_index",
                                    "delta_results.$": "$.delta_results",
                                },
                                "ResultPath": "$.chunk_result",
                                "Next": "CheckContinuation",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        "ErrorEquals": ["Lambda.TooManyRequestsException", "States.Timeout"],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    },
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Catch": [
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "Next": "ChunkProcessingFailed",
                                        "ResultPath": "$.error",
                                    }
                                ],
                            },
                            "CheckContinuation": {
                                "Type": "Choice",
                                "Comment": "Check if there are more chunks to process",
                                "Choices": [
                                    {
                                        "Variable": "$.chunk_result.has_more_chunks",
                                        "BooleanEquals": True,
                                        "Next": "PrepareNextChunk",
                                    }
                                ],
                                "Default": "FinalMerge",
                            },
                            "PrepareNextChunk": {
                                "Type": "Pass",
                                "Comment": "Prepare for next chunk iteration",
                                "Parameters": {
                                    "batch_id.$": "$.batch_id",
                                    "operation": "process_chunk",
                                    "chunk_index.$": "$.chunk_result.next_chunk_index",
                                    "delta_results.$": "$.chunk_result.remaining_deltas",
                                    "total_chunks_processed.$": "States.MathAdd($.total_chunks_processed, 1)",
                                },
                                "Next": "ProcessChunk",
                            },
                            "FinalMerge": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Comment": "Final merge of all intermediate chunks",
                                "Parameters": {
                                    "operation": "final_merge",
                                    "batch_id.$": "$.batch_id",
                                    "total_chunks.$": "States.MathAdd($.total_chunks_processed, 1)",
                                },
                                "End": True,
                                "Retry": [
                                    {
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 3,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                            },
                            "ChunkProcessingFailed": {
                                "Type": "Fail",
                                "Error": "ChunkProcessingFailed",
                                "Cause": "Failed to process delta chunk",
                            },
                            "NoPendingBatches": {
                                "Type": "Succeed",
                                "Comment": "No pending batches to process",
                            },
                        },
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        
        # Create the Create Word Embedding Batches Step Function
        self.create_word_batches_sf = StateMachine(
            f"create-word-batches-sf-{stack}",
            role_arn=self.sf_role.arn,
            definition=Output.all(
                self.zip_lambda_functions["find-unembedded-words"].arn,
                self.zip_lambda_functions["submit-words-openai"].arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Comment": "Find words without embeddings and submit to OpenAI",
                        "StartAt": "FindUnembeddedWords",
                        "States": {
                            "FindUnembeddedWords": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "SubmitWordBatches",
                            },
                            "SubmitWordBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 10,
                                "Iterator": {
                                    "StartAt": "SubmitWordsToOpenAI",
                                    "States": {
                                        "SubmitWordsToOpenAI": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                        },
                                    },
                                },
                                "End": True,
                            },
                        },
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        
        # Create the Poll and Store Word Embeddings Step Function
        self.poll_word_embeddings_sf = StateMachine(
            f"poll-word-embeddings-sf-{stack}",
            role_arn=self.sf_role.arn,
            definition=Output.all(
                self.zip_lambda_functions["list-pending"].arn,
                self.container_lambda_functions["word-polling"].arn,
                self.container_lambda_functions["compaction"].arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Comment": "Poll OpenAI for completed word embedding batches and store in ChromaDB",
                        "StartAt": "ListPendingWordBatches",
                        "States": {
                            "ListPendingWordBatches": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "ResultPath": "$.pending_batches",
                                "Next": "CheckPendingWordBatches",
                            },
                            "CheckPendingWordBatches": {
                                "Type": "Choice",
                                "Choices": [
                                    {
                                        "Variable": "$.pending_batches[0]",
                                        "IsPresent": True,
                                        "Next": "PollWordBatches",
                                    },
                                ],
                                "Default": "NoWordBatchesPending",
                            },
                            "PollWordBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.pending_batches",
                                "MaxConcurrency": 10,
                                "Parameters": {
                                    "batch_id.$": "$$.Map.Item.Value.batch_id",
                                    "openai_batch_id.$": "$$.Map.Item.Value.openai_batch_id",
                                    "skip_sqs_notification": True,
                                },
                                "Iterator": {
                                    "StartAt": "PollWordBatch",
                                    "States": {
                                        "PollWordBatch": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                        },
                                    },
                                },
                                "ResultPath": "$.poll_results",
                                "Next": "PrepareWordChunkedCompaction",
                            },
                            "PrepareWordChunkedCompaction": {
                                "Type": "Pass",
                                "Comment": "Prepare data for chunked compaction",
                                "Parameters": {
                                    "batch_id.$": "$$.Execution.Name",
                                    "delta_results.$": "$.poll_results",
                                    "chunk_index": 0,
                                    "total_chunks_processed": 0,
                                    "operation": "process_chunk",
                                },
                                "Next": "CheckForWordDeltas",
                            },
                            "CheckForWordDeltas": {
                                "Type": "Choice",
                                "Comment": "Check if there are deltas to process",
                                "Choices": [
                                    {
                                        "Variable": "$.delta_results[0]",
                                        "IsPresent": True,
                                        "Next": "ProcessWordChunk",
                                    }
                                ],
                                "Default": "WordFinalMerge",
                            },
                            "ProcessWordChunk": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Comment": "Process a chunk of word deltas (max 10)",
                                "Parameters": {
                                    "operation": "process_chunk",
                                    "batch_id.$": "$.batch_id",
                                    "chunk_index.$": "$.chunk_index",
                                    "delta_results.$": "$.delta_results",
                                },
                                "ResultPath": "$.chunk_result",
                                "Next": "CheckWordContinuation",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        "ErrorEquals": ["Lambda.TooManyRequestsException", "States.Timeout"],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    },
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Catch": [
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "Next": "WordChunkProcessingFailed",
                                        "ResultPath": "$.error",
                                    }
                                ],
                            },
                            "CheckWordContinuation": {
                                "Type": "Choice",
                                "Comment": "Check if there are more chunks to process",
                                "Choices": [
                                    {
                                        "Variable": "$.chunk_result.has_more_chunks",
                                        "BooleanEquals": True,
                                        "Next": "PrepareNextWordChunk",
                                    }
                                ],
                                "Default": "WordFinalMerge",
                            },
                            "PrepareNextWordChunk": {
                                "Type": "Pass",
                                "Comment": "Prepare for next chunk iteration",
                                "Parameters": {
                                    "batch_id.$": "$.batch_id",
                                    "operation": "process_chunk",
                                    "chunk_index.$": "$.chunk_result.next_chunk_index",
                                    "delta_results.$": "$.chunk_result.remaining_deltas",
                                    "total_chunks_processed.$": "States.MathAdd($.total_chunks_processed, 1)",
                                },
                                "Next": "ProcessWordChunk",
                            },
                            "WordFinalMerge": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Comment": "Final merge of all intermediate word chunks",
                                "Parameters": {
                                    "operation": "final_merge",
                                    "batch_id.$": "$.batch_id",
                                    "total_chunks.$": "States.MathAdd($.total_chunks_processed, 1)",
                                },
                                "End": True,
                                "Retry": [
                                    {
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 3,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                            },
                            "WordChunkProcessingFailed": {
                                "Type": "Fail",
                                "Error": "WordChunkProcessingFailed",
                                "Cause": "Failed to process word delta chunk",
                            },
                            "NoWordBatchesPending": {
                                "Type": "Succeed",
                                "Comment": "No pending word batches to process",
                            },
                        },
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

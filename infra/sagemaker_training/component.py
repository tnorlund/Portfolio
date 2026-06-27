"""SageMaker Training Infrastructure for LayoutLM models.

This component provides managed ML training infrastructure using AWS SageMaker,
replacing the custom EC2-based training setup. Benefits include:
- No instance management (SageMaker handles provisioning/teardown)
- Built-in spot instance support with automatic checkpointing
- Managed container execution
- Integrated CloudWatch logging
- Pay only for training time
"""

import hashlib
import json
import os
from pathlib import Path

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileArchive,
    FileAsset,
    Output,
    ResourceOptions,
)

# Get repo root at module load time (two levels up from this file)
REPO_ROOT = Path(__file__).resolve().parents[2]


class SageMakerTrainingInfra(ComponentResource):
    """SageMaker-based training infrastructure for LayoutLM models.

    Creates:
    - ECR repository for training container image
    - IAM role for SageMaker execution (DynamoDB, S3, ECR access)
    - S3 bucket for model outputs and dataset caching
    - CodeBuild project to build/push Docker image on changes
    - Lambda function to start training jobs programmatically
    """

    def __init__(
        self,
        name: str,
        dynamodb_table_name: Output[str],
        raw_bucket_arn: Output[str] | None = None,
        synthetic_source_bucket_arn: Output[str] | None = None,
        opts: ResourceOptions | None = None,
    ):
        super().__init__("custom:ml:SageMakerTrainingInfra", name, None, opts)

        stack = pulumi.get_stack()
        region = aws.get_region().name
        account_id = aws.get_caller_identity().account_id

        # ---------------------------------------------------------------------
        # ECR Repository for Training Image
        # ---------------------------------------------------------------------
        self.ecr_repo = aws.ecr.Repository(
            f"{name}-repo",
            name=f"layoutlm-trainer-{stack}",
            image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            image_tag_mutability="MUTABLE",  # Allow :latest updates
            tags={"Component": name, "Purpose": "layoutlm-training"},
            opts=ResourceOptions(parent=self),
        )

        # Lifecycle policy to keep only recent images
        aws.ecr.LifecyclePolicy(
            f"{name}-lifecycle",
            repository=self.ecr_repo.name,
            policy=json.dumps(
                {
                    "rules": [
                        {
                            "rulePriority": 1,
                            "description": "Keep last 5 images",
                            "selection": {
                                "tagStatus": "any",
                                "countType": "imageCountMoreThan",
                                "countNumber": 5,
                            },
                            "action": {"type": "expire"},
                        }
                    ]
                }
            ),
            opts=ResourceOptions(parent=self.ecr_repo),
        )

        # ---------------------------------------------------------------------
        # S3 Bucket for Model Outputs
        # ---------------------------------------------------------------------
        self.output_bucket = aws.s3.Bucket(
            f"{name}-output",
            bucket=f"layoutlm-training-{stack}-{account_id[:8]}",
            force_destroy=True,  # Allow deletion even with objects
            tags={"Component": name, "Purpose": "training-outputs"},
            opts=ResourceOptions(parent=self),
        )

        # Block public access
        aws.s3.BucketPublicAccessBlock(
            f"{name}-output-pab",
            bucket=self.output_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.output_bucket),
        )

        # Lifecycle policy for checkpoint cleanup
        # - Per-epoch checkpoints expire after 7 days (keep recent for debugging)
        # - Full run directories expire after 30 days
        aws.s3.BucketLifecycleConfiguration(
            f"{name}-output-lifecycle",
            bucket=self.output_bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="expire-epoch-checkpoints",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix="checkpoints/",  # Per-epoch checkpoints from on_save
                    ),
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=7,
                    ),
                ),
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="expire-run-directories",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix="runs/",  # Full run directories
                    ),
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=30,
                    ),
                ),
            ],
            opts=ResourceOptions(parent=self.output_bucket),
        )

        # ---------------------------------------------------------------------
        # IAM Role for SageMaker Execution
        # ---------------------------------------------------------------------
        self.sagemaker_role = aws.iam.Role(
            f"{name}-sagemaker-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "sagemaker.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # SageMaker execution policy
        policy_inputs = [
            self.output_bucket.arn,
            self.ecr_repo.arn,
            dynamodb_table_name,
            raw_bucket_arn or "",
            synthetic_source_bucket_arn or "",
        ]
        sagemaker_policy = aws.iam.RolePolicy(
            f"{name}-sagemaker-policy",
            role=self.sagemaker_role.id,
            policy=Output.all(*policy_inputs).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # S3 access for model outputs and data
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:DeleteObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/*",
                                ]
                                + (
                                    [
                                        args[4],
                                        f"{args[4]}/*",
                                    ]
                                    if args[4]
                                    else []
                                ),
                            },
                            # ECR access to pull training image
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecr:GetDownloadUrlForLayer",
                                    "ecr:BatchGetImage",
                                    "ecr:BatchCheckLayerAvailability",
                                ],
                                "Resource": args[1],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "ecr:GetAuthorizationToken",
                                "Resource": "*",
                            },
                            # DynamoDB access for training data
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",  # For table validation
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:PutItem",  # For job logging
                                    "dynamodb:UpdateItem",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{region}:{account_id}:table/{args[2]}",
                                    f"arn:aws:dynamodb:{region}:{account_id}:table/{args[2]}/index/*",
                                ],
                            },
                            # CloudWatch Logs
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogGroup",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                    "logs:DescribeLogStreams",
                                ],
                                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/sagemaker/*",
                            },
                            # CloudWatch Metrics
                            {
                                "Effect": "Allow",
                                "Action": "cloudwatch:PutMetricData",
                                "Resource": "*",
                                "Condition": {
                                    "StringEquals": {
                                        "cloudwatch:namespace": "/aws/sagemaker/TrainingJobs",
                                    }
                                },
                            },
                        ]
                        + (
                            [
                                # S3 read access for receipt images across all buckets (LayoutLMv3 training)
                                {
                                    "Effect": "Allow",
                                    "Action": "s3:GetObject",
                                    "Resource": [
                                        f"{args[3]}/*",
                                        "arn:aws:s3:::raw-image-bucket-*/*",
                                    ],
                                },
                            ]
                            if args[3]
                            else []
                        ),
                    }
                )
            ),
            opts=ResourceOptions(parent=self.sagemaker_role),
        )

        # ---------------------------------------------------------------------
        # S3 Bucket for Build Source
        # ---------------------------------------------------------------------
        self.source_bucket = aws.s3.Bucket(
            f"{name}-source",
            bucket=f"layoutlm-build-source-{stack}-{account_id[:8]}",
            force_destroy=True,
            tags={"Component": name, "Purpose": "codebuild-source"},
            opts=ResourceOptions(parent=self),
        )

        # Block public access
        aws.s3.BucketPublicAccessBlock(
            f"{name}-source-pab",
            bucket=self.source_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.source_bucket),
        )

        # Compute content hash for the Docker build context
        self.content_hash = self._compute_source_hash()

        # Create source archive using Pulumi's native asset system
        # This automatically handles change detection and uploads
        sagemaker_training_dir = REPO_ROOT / "infra" / "sagemaker_training"
        self.source_archive = AssetArchive(
            {
                "receipt_layoutlm": FileArchive(str(REPO_ROOT / "receipt_layoutlm")),
                "receipt_dynamo": FileArchive(str(REPO_ROOT / "receipt_dynamo")),
                "infra/sagemaker_training/Dockerfile": FileAsset(
                    str(sagemaker_training_dir / "Dockerfile")
                ),
                "infra/sagemaker_training/train.py": FileAsset(
                    str(sagemaker_training_dir / "train.py")
                ),
            }
        )

        # Upload source archive to S3 (Pulumi handles change detection automatically)
        self.source_object = aws.s3.BucketObjectv2(
            f"{name}-source-object",
            bucket=self.source_bucket.id,
            key="source.zip",
            source=self.source_archive,
            opts=ResourceOptions(parent=self.source_bucket),
        )

        # ---------------------------------------------------------------------
        # CodeBuild for Docker Image Building
        # ---------------------------------------------------------------------
        self.codebuild_role = aws.iam.Role(
            f"{name}-codebuild-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codebuild.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        codebuild_policy = aws.iam.RolePolicy(
            f"{name}-codebuild-policy",
            role=self.codebuild_role.id,
            policy=Output.all(
                self.ecr_repo.arn,
                self.source_bucket.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # ECR push access
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecr:GetDownloadUrlForLayer",
                                    "ecr:BatchGetImage",
                                    "ecr:BatchCheckLayerAvailability",
                                    "ecr:PutImage",
                                    "ecr:InitiateLayerUpload",
                                    "ecr:UploadLayerPart",
                                    "ecr:CompleteLayerUpload",
                                ],
                                "Resource": args[0],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "ecr:GetAuthorizationToken",
                                "Resource": "*",
                            },
                            # CloudWatch Logs
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogGroup",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/codebuild/*",
                            },
                            # S3 source bucket access
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                ],
                                "Resource": f"{args[1]}/*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": "s3:ListBucket",
                                "Resource": args[1],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.codebuild_role),
        )

        # CodeBuild project with S3 source
        self.codebuild_project = aws.codebuild.Project(
            f"{name}-image-builder",
            description="Builds LayoutLM training Docker image",
            build_timeout=30,  # 30 minutes
            service_role=self.codebuild_role.arn,
            environment=aws.codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_MEDIUM",
                image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                type="LINUX_CONTAINER",
                privileged_mode=True,  # Required for Docker builds
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AWS_ACCOUNT_ID",
                        value=account_id,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AWS_REGION",
                        value=region,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="ECR_REPO",
                        value=self.ecr_repo.repository_url,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="IMAGE_TAG",
                        value=self.content_hash,
                    ),
                ],
            ),
            source=aws.codebuild.ProjectSourceArgs(
                type="S3",
                location=self.source_bucket.bucket.apply(lambda b: f"{b}/source.zip"),
                buildspec=self._generate_buildspec(),
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
            tags={"Component": name},
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.source_object],
            ),
        )

        # Trigger build when source changes
        # Use source_object's etag as trigger - it changes when file content changes
        self.trigger_build = command.local.Command(
            f"{name}-trigger-build",
            create=Output.all(self.codebuild_project.name).apply(
                lambda args: f'aws codebuild start-build --project-name "{args[0]}" --query "build.id" --output text'
            ),
            update=Output.all(self.codebuild_project.name).apply(
                lambda args: f'aws codebuild start-build --project-name "{args[0]}" --query "build.id" --output text'
            ),
            triggers=[self.source_object.etag],
            opts=ResourceOptions(
                parent=self.codebuild_project,
                depends_on=[self.source_object],
            ),
        )

        # ---------------------------------------------------------------------
        # Lambda to Start Training Jobs
        # ---------------------------------------------------------------------
        self.lambda_role = aws.iam.Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            ],
            opts=ResourceOptions(parent=self),
        )

        lambda_policy = aws.iam.RolePolicy(
            f"{name}-lambda-policy",
            role=self.lambda_role.id,
            policy=Output.all(
                self.sagemaker_role.arn,
                self.output_bucket.arn,
                self.ecr_repo.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # SageMaker training job management
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sagemaker:CreateTrainingJob",
                                    "sagemaker:DescribeTrainingJob",
                                    "sagemaker:StopTrainingJob",
                                    "sagemaker:ListTrainingJobs",
                                    "sagemaker:AddTags",
                                ],
                                "Resource": f"arn:aws:sagemaker:{region}:{account_id}:training-job/*",
                            },
                            # Read account spend before synthetic replay launches
                            {
                                "Effect": "Allow",
                                "Action": "ce:GetCostAndUsage",
                                "Resource": "*",
                            },
                            # Pass role to SageMaker
                            {
                                "Effect": "Allow",
                                "Action": "iam:PassRole",
                                "Resource": args[0],
                                "Condition": {
                                    "StringEquals": {
                                        "iam:PassedToService": "sagemaker.amazonaws.com",
                                    }
                                },
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Lambda function code (inline for simplicity)
        lambda_code = self._generate_lambda_code()

        self.start_training_lambda = aws.lambda_.Function(
            f"{name}-start-training",
            runtime="python3.11",
            handler="index.handler",
            role=self.lambda_role.arn,
            timeout=30,
            memory_size=256,
            code=pulumi.AssetArchive(
                {
                    "index.py": pulumi.StringAsset(lambda_code),
                }
            ),
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables=Output.all(
                    self.ecr_repo.repository_url,
                    self.output_bucket.bucket,
                    self.sagemaker_role.arn,
                    dynamodb_table_name,
                ).apply(
                    lambda args: {
                        "ECR_IMAGE_URI": f"{args[0]}:latest",
                        "OUTPUT_BUCKET": args[1],
                        "SAGEMAKER_ROLE_ARN": args[2],
                        "DYNAMO_TABLE_NAME": args[3],
                        "SYNTHETIC_REPLAY_MAX_AWS_SPEND_USD": "200",
                    }
                ),
            ),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # ---------------------------------------------------------------------
        # Outputs
        # ---------------------------------------------------------------------
        self.register_outputs(
            {
                "ecr_repo_url": self.ecr_repo.repository_url,
                "output_bucket": self.output_bucket.bucket,
                "source_bucket": self.source_bucket.bucket,
                "content_hash": self.content_hash,
                "sagemaker_role_arn": self.sagemaker_role.arn,
                "start_training_lambda_arn": self.start_training_lambda.arn,
                "codebuild_project_name": self.codebuild_project.name,
            }
        )

    def _generate_lambda_code(self) -> str:
        """Generate the Lambda function code for starting training jobs."""
        return '''
import json
import os
import boto3
from datetime import datetime, timedelta

sagemaker = boto3.client("sagemaker")

def _tag_value(value):
    """Return a SageMaker tag-safe string value."""
    if value is None:
        return None
    text = str(value)
    return text[:256] if text else None

def _truthy(value):
    """Return whether an event value is an explicit truthy flag."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _int_value(name, value):
    """Parse an integer event value with a clear error."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc

def _float_value(name, value):
    """Parse a float event/env value with a clear error."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc

def _current_month_spend_usd():
    """Return current-month unblended AWS spend from Cost Explorer."""
    today = datetime.utcnow().date()
    start = today.replace(day=1).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    ce = boto3.client("ce", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )
    total = (
        response.get("ResultsByTime", [{}])[0]
        .get("Total", {})
        .get("UnblendedCost", {})
    )
    amount = _float_value("current_month_aws_spend_usd", total.get("Amount"))
    return {
        "amount": amount,
        "start": start,
        "end": end,
        "unit": total.get("Unit") or "USD",
    }

def _require_synthetic_replay_budget(event):
    """Fail closed before starting synthetic replay after the experiment cap."""
    raw_limit = event.get("max_aws_spend_usd")
    if raw_limit is None:
        raw_limit = event.get("synthetic_replay_max_aws_spend_usd")
    if raw_limit is None:
        raw_limit = os.environ.get("SYNTHETIC_REPLAY_MAX_AWS_SPEND_USD", "200")
    limit = _float_value("synthetic_replay_max_aws_spend_usd", raw_limit)
    if limit <= 0:
        return
    spend = _current_month_spend_usd()
    if spend["amount"] >= limit:
        raise ValueError(
            "synthetic replay AWS spend cap reached: "
            f"${spend['amount']:.2f} month-to-date >= ${limit:.2f}; "
            "not starting SageMaker training"
        )

def handler(event, context):
    """Start a SageMaker training job.

    Event parameters:
    - job_name: Unique name for the training job (required)
    - instance_type: SageMaker instance type (default: ml.g5.xlarge)
    - instance_count: Number of instances (default: 1)
    - use_spot: Whether to use spot instances (default: False)
    - max_runtime_hours: Maximum runtime in hours (default: 24)
    - hyperparameters: Dict of training hyperparameters
    """
    # Extract parameters
    job_name = event.get("job_name")
    if not job_name:
        job_name = f"layoutlm-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    batch_bucket = event.get("batch_bucket")
    line_item_patterns_prefix = event.get("line_item_patterns_s3_prefix")
    synthetic_training_examples = (
        event.get("synthetic_training_examples")
        or event.get("synthetic_training_examples_s3")
        or event.get("line_item_patterns_s3_uri")
    )
    if not synthetic_training_examples:
        line_item_patterns_key = event.get("line_item_patterns_s3_key")
        if batch_bucket and line_item_patterns_key:
            synthetic_training_examples = f"s3://{batch_bucket}/{line_item_patterns_key}"
        elif (
            isinstance(line_item_patterns_prefix, str)
            and line_item_patterns_prefix.startswith("s3://")
        ):
            synthetic_training_examples = line_item_patterns_prefix
        elif batch_bucket and line_item_patterns_prefix:
            synthetic_training_examples = f"s3://{batch_bucket}/{line_item_patterns_prefix}"
        elif batch_bucket and event.get("execution_id"):
            synthetic_training_examples = (
                f"s3://{batch_bucket}/line_item_patterns/{event['execution_id']}/"
            )

    baseline_job_ref = (
        event.get("baseline_job_ref")
        or event.get("baseline_job_id")
        or event.get("baseline_job_name")
    )
    is_synthetic_replay = bool(synthetic_training_examples)

    instance_type = event.get("instance_type", "ml.g5.xlarge")
    instance_count = _int_value("instance_count", event.get("instance_count", 1))
    # Default to on-demand; synthetic replay no longer *requires* spot.
    # On-demand g5.xlarge provisions immediately (spot capacity is scarce and
    # can queue for the full wait window). Callers may still pass use_spot=true.
    use_spot = _truthy(event.get("use_spot", False))
    max_runtime_hours = _int_value(
        "max_runtime_hours",
        event.get("max_runtime_hours", 1 if is_synthetic_replay else 24),
    )

    if is_synthetic_replay:
        source_receipt_limit = _int_value(
            "source_receipt_limit",
            event.get("source_receipt_limit"),
        )
        if not baseline_job_ref:
            raise ValueError(
                "baseline_job_ref is required for synthetic training examples"
            )
        if not _truthy(event.get("synthetic_replay_cost_ack")):
            raise ValueError(
                "synthetic_replay_cost_ack=true is required for synthetic replay"
            )
        if source_receipt_limit < 1 or source_receipt_limit > 3:
            raise ValueError("synthetic replay requires 1-3 source receipts")
        if instance_count != 1:
            raise ValueError("synthetic replay is capped at one SageMaker instance")
        if max_runtime_hours > 1:
            raise ValueError("synthetic replay is capped at one runtime hour")
        _require_synthetic_replay_budget(event)

    # Default hyperparameters
    hyperparameters = {
        "epochs": "1" if is_synthetic_replay else "10",
        "batch_size": "8",
        "learning_rate": "5e-5",
        "warmup_ratio": "0.1",
        "early_stopping_patience": "1" if is_synthetic_replay else "2",
        **{k: str(v) for k, v in event.get("hyperparameters", {}).items()},
    }

    if is_synthetic_replay and _int_value("epochs", hyperparameters.get("epochs")) > 1:
        raise ValueError("synthetic replay is capped at one epoch")

    if synthetic_training_examples:
        hyperparameters.setdefault(
            "synthetic_training_examples", str(synthetic_training_examples)
        )

    # Add required parameters
    hyperparameters["dynamo_table"] = os.environ["DYNAMO_TABLE_NAME"]
    hyperparameters["job_name"] = job_name
    hyperparameters["output_s3_path"] = os.environ["OUTPUT_BUCKET"]

    # Build training job config
    training_job_config = {
        "TrainingJobName": job_name,
        "AlgorithmSpecification": {
            "TrainingImage": os.environ["ECR_IMAGE_URI"],
            "TrainingInputMode": "File",
        },
        "RoleArn": os.environ["SAGEMAKER_ROLE_ARN"],
        "HyperParameters": hyperparameters,
        "ResourceConfig": {
            "InstanceType": instance_type,
            "InstanceCount": instance_count,
            "VolumeSizeInGB": 100,
        },
        "StoppingCondition": {
            "MaxRuntimeInSeconds": max_runtime_hours * 3600,
        },
        "OutputDataConfig": {
            "S3OutputPath": f"s3://{os.environ['OUTPUT_BUCKET']}/runs",
        },
    }

    # Add spot configuration if enabled
    if use_spot:
        training_job_config["EnableManagedSpotTraining"] = True
        training_job_config["StoppingCondition"]["MaxWaitTimeInSeconds"] = (
            max_runtime_hours * 3600 + 3600  # +1 hour for spot wait
        )
        training_job_config["CheckpointConfig"] = {
            "S3Uri": f"s3://{os.environ['OUTPUT_BUCKET']}/checkpoints/{job_name}",
        }

    tags = []
    # Skip CoreML auto-export for v3 runs (Swift inference not yet supported)
    if hyperparameters.get("model_version") == "v3":
        tags.append({"Key": "skip-coreml-export", "Value": "true"})

    if baseline_job_ref and synthetic_training_examples:
        tags.append({"Key": "synthetic-augmentation", "Value": "true"})
        tags.append({"Key": "synthetic-replay-cost-ack", "Value": "true"})
        tags.append({
            "Key": "source-receipt-limit",
            "Value": _tag_value(event.get("source_receipt_limit")),
        })
        tags.append({
            "Key": "baseline-job-ref",
            "Value": _tag_value(baseline_job_ref),
        })
        tags.append({
            "Key": "pattern-artifact-s3-uri",
            "Value": _tag_value(synthetic_training_examples),
        })
        if event.get("batch_bucket"):
            tags.append({
                "Key": "batch-bucket",
                "Value": _tag_value(event.get("batch_bucket")),
            })
        if event.get("line_item_patterns_s3_key"):
            tags.append({
                "Key": "line-item-patterns-s3-key",
                "Value": _tag_value(event.get("line_item_patterns_s3_key")),
            })
        if event.get("line_item_patterns_s3_prefix"):
            tags.append({
                "Key": "line-item-patterns-s3-prefix",
                "Value": _tag_value(event.get("line_item_patterns_s3_prefix")),
            })

    if tags:
        training_job_config["Tags"] = [
            tag for tag in tags if tag.get("Value") is not None
        ]

    # Create the training job
    response = sagemaker.create_training_job(**training_job_config)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "job_name": job_name,
            "job_arn": response["TrainingJobArn"],
            "instance_type": instance_type,
            "use_spot": use_spot,
            "synthetic_training_examples": hyperparameters.get("synthetic_training_examples"),
        }),
    }
'''

    def _compute_source_hash(self) -> str:
        """Compute a hash of the source files that affect the Docker image.

        Used for the Docker image tag to ensure unique tags per build.
        """
        sagemaker_training_dir = REPO_ROOT / "infra" / "sagemaker_training"

        # Files/directories to include in hash
        paths_to_hash = [
            sagemaker_training_dir / "Dockerfile",
            sagemaker_training_dir / "train.py",
            REPO_ROOT / "receipt_layoutlm",
            REPO_ROOT / "receipt_dynamo",
        ]

        hasher = hashlib.sha256()

        for path in paths_to_hash:
            if path.is_file():
                hasher.update(path.read_bytes())
            elif path.is_dir():
                # Hash all Python files in directory
                for fpath in sorted(path.rglob("*.py")):
                    hasher.update(fpath.read_bytes())
                for fpath in sorted(path.rglob("*.txt")):
                    hasher.update(fpath.read_bytes())
                for fpath in sorted(path.rglob("*.json")):
                    hasher.update(fpath.read_bytes())

        return hasher.hexdigest()[:16]

    def _generate_buildspec(self) -> str:
        """Generate the buildspec for CodeBuild."""
        return """version: 0.2

phases:
  pre_build:
    commands:
      - echo "Logging in to Amazon ECR..."
      - aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
      - echo "Building image with tag $IMAGE_TAG"

  build:
    commands:
      - echo "Building Docker image..."
      - docker build -t layoutlm-trainer -f infra/sagemaker_training/Dockerfile .
      - docker tag layoutlm-trainer:latest $ECR_REPO:latest
      - docker tag layoutlm-trainer:latest $ECR_REPO:$IMAGE_TAG

  post_build:
    commands:
      - echo "Pushing Docker image to ECR..."
      - docker push $ECR_REPO:latest
      - docker push $ECR_REPO:$IMAGE_TAG
      - echo "Image pushed successfully"
      - echo "Image URI - $ECR_REPO:$IMAGE_TAG"
"""

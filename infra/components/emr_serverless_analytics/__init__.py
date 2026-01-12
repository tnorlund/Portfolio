"""
EMR Serverless Analytics Infrastructure.

This component creates infrastructure for running Spark analytics on LangSmith
trace exports using EMR Serverless, integrated with Step Functions.

Creates:
- EMR Serverless Application (Spark) with custom Docker image
- S3 bucket for Spark job artifacts (entry point scripts)
- S3 bucket for analytics output (Parquet)
- IAM role for EMR job execution
"""

import hashlib
import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    ComponentResource,
    FileAsset,
    Input,
    Output,
    ResourceOptions,
)

# Get repo root (three levels up from this file)
REPO_ROOT = Path(__file__).resolve().parents[3]

# Get stack configuration
stack = pulumi.get_stack()


class EMRServerlessAnalytics(ComponentResource):
    """EMR Serverless infrastructure for LangSmith Spark analytics.

    This component provides:
    - EMR Serverless Application (auto-start, auto-stop) with custom image
    - S3 buckets for job artifacts and analytics output
    - IAM roles with appropriate permissions
    """

    def __init__(
        self,
        name: str,
        *,
        langsmith_export_bucket_arn: Input[str],
        custom_image_uri: Optional[Input[str]] = None,
        # Viz-cache integration (optional)
        cache_bucket_arn: Optional[Input[str]] = None,
        batch_bucket_arn: Optional[Input[str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:emr-serverless-analytics:{name}",
            name,
            None,
            opts,
        )

        region = aws.get_region().name
        account_id = aws.get_caller_identity().account_id

        # Convert inputs to Output
        langsmith_bucket_arn = Output.from_input(langsmith_export_bucket_arn)
        self.custom_image_uri = (
            Output.from_input(custom_image_uri) if custom_image_uri else None
        )
        self.cache_bucket_arn = (
            Output.from_input(cache_bucket_arn) if cache_bucket_arn else None
        )
        self.batch_bucket_arn = (
            Output.from_input(batch_bucket_arn) if batch_bucket_arn else None
        )

        # ============================================================
        # S3 Buckets
        # ============================================================

        # Artifacts bucket for Spark job code and venv
        self.artifacts_bucket = aws.s3.Bucket(
            f"{name}-artifacts",
            force_destroy=True,
            tags={
                "Name": f"{name}-artifacts",
                "Purpose": "EMR Spark job artifacts",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-artifacts-pab",
            bucket=self.artifacts_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.artifacts_bucket),
        )

        # Analytics output bucket
        self.analytics_bucket = aws.s3.Bucket(
            f"{name}-analytics-output",
            force_destroy=True,
            tags={
                "Name": f"{name}-analytics-output",
                "Purpose": "EMR Spark analytics results",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-analytics-pab",
            bucket=self.analytics_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.analytics_bucket),
        )

        # Lifecycle policy - expire analytics after 30 days
        aws.s3.BucketLifecycleConfiguration(
            f"{name}-analytics-lifecycle",
            bucket=self.analytics_bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="expire-old-analytics",
                    status="Enabled",
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=30,
                    ),
                ),
            ],
            opts=ResourceOptions(parent=self.analytics_bucket),
        )

        # ============================================================
        # EMR Serverless Application
        # ============================================================
        # Build application args
        # Use EMR 7.5.0 with custom image that has Python 3.12 installed
        # (EMR 8.0 preview doesn't have public Docker base images for custom images yet)
        emr_app_args = {
            "name": f"langsmith-analytics-{stack}",
            "release_label": "emr-7.5.0",
            "type": "SPARK",
            "initial_capacities": [
                aws.emrserverless.ApplicationInitialCapacityArgs(
                    initial_capacity_type="Driver",
                    initial_capacity_config=(
                        aws.emrserverless
                        .ApplicationInitialCapacityInitialCapacityConfigArgs(
                            worker_count=1,
                            worker_configuration=(
                                aws.emrserverless.ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs(  # pylint: disable=line-too-long
                                    cpu="2 vCPU",
                                    memory="4 GB",
                                )
                            ),
                        )
                    ),
                ),
                aws.emrserverless.ApplicationInitialCapacityArgs(
                    initial_capacity_type="Executor",
                    initial_capacity_config=(
                        aws.emrserverless
                        .ApplicationInitialCapacityInitialCapacityConfigArgs(
                            worker_count=4,
                            worker_configuration=(
                                aws.emrserverless.ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs(  # pylint: disable=line-too-long
                                    cpu="2 vCPU",
                                    memory="4 GB",
                                )
                            ),
                        )
                    ),
                ),
            ],
            "maximum_capacity": aws.emrserverless.ApplicationMaximumCapacityArgs(
                cpu="16 vCPU",
                memory="64 GB",
            ),
            "auto_start_configuration": aws.emrserverless.ApplicationAutoStartConfigurationArgs(
                enabled=True,
            ),
            "auto_stop_configuration": aws.emrserverless.ApplicationAutoStopConfigurationArgs(
                enabled=True,
                idle_timeout_minutes=15,
            ),
            "tags": {
                "Name": f"{name}-app",
                "Purpose": "LangSmith analytics",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
        }

        # Add custom image configuration if provided
        if self.custom_image_uri:
            emr_app_args["image_configuration"] = (
                aws.emrserverless.ApplicationImageConfigurationArgs(
                    image_uri=self.custom_image_uri,
                )
            )

        self.emr_application = aws.emrserverless.Application(
            f"{name}-app",
            **emr_app_args,
            opts=ResourceOptions(
                parent=self,
                # CodeBuild updates imageConfiguration directly after build,
                # so we tell Pulumi to ignore changes to avoid conflicts
                ignore_changes=["imageConfiguration"] if self.custom_image_uri else [],
            ),
        )

        # ============================================================
        # IAM Role for EMR Job Execution
        # ============================================================
        self.emr_job_role = aws.iam.Role(
            f"{name}-emr-job-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "emr-serverless.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Name": f"{name}-emr-job-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # EMR job execution policy
        # Build list of outputs to combine (include optional buckets if provided)
        policy_outputs = [
            langsmith_bucket_arn,  # 0
            self.artifacts_bucket.arn,  # 1
            self.analytics_bucket.arn,  # 2
        ]
        if self.cache_bucket_arn:
            policy_outputs.append(self.cache_bucket_arn)  # 3
        if self.batch_bucket_arn:
            policy_outputs.append(self.batch_bucket_arn)  # 4 (or 3 if no cache)

        def build_policy(args):
            """Build IAM policy with optional bucket permissions."""
            statements = [
                # Read LangSmith Parquet exports
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [args[0], f"{args[0]}/*"],
                },
                # Read Spark job artifacts and write logs
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:ListBucket",
                    ],
                    "Resource": [args[1], f"{args[1]}/*"],
                },
                # Write analytics output
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                    ],
                    "Resource": [args[2], f"{args[2]}/*"],
                },
                # CloudWatch Logs
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams",
                    ],
                    "Resource": [
                        f"arn:aws:logs:{region}:{account_id}"
                        ":log-group:/aws/emr-serverless/*",
                        f"arn:aws:logs:{region}:{account_id}"
                        ":log-group:/aws/emr-serverless/*:*",
                    ],
                },
                # ECR access for custom images
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:GetAuthorizationToken",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                    ],
                    "Resource": "*",
                },
            ]

            # Add cache bucket permissions if provided
            idx = 3
            if self.cache_bucket_arn and len(args) > idx:
                statements.append({
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                    ],
                    "Resource": [args[idx], f"{args[idx]}/*"],
                })
                idx += 1

            # Add batch bucket permissions if provided
            if self.batch_bucket_arn and len(args) > idx:
                statements.append({
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [args[idx], f"{args[idx]}/*"],
                })

            return json.dumps({"Version": "2012-10-17", "Statement": statements})

        aws.iam.RolePolicy(
            f"{name}-emr-job-policy",
            role=self.emr_job_role.id,
            policy=Output.all(*policy_outputs).apply(build_policy),
            opts=ResourceOptions(parent=self.emr_job_role),
        )

        # ============================================================
        # Upload Entry Point Scripts
        # ============================================================
        # Upload Spark job entry point scripts to S3
        spark_scripts_dir = (
            REPO_ROOT / "receipt_langsmith" / "receipt_langsmith" / "spark"
        )

        # Compute content hash to detect file changes
        def file_hash(path: Path) -> str:
            """Compute truncated MD5 hash for content change detection."""
            return hashlib.md5(path.read_bytes()).hexdigest()[:12]  # noqa: S324

        merged_job_path = spark_scripts_dir / "merged_job.py"
        label_validation_viz_cache_path = (
            spark_scripts_dir / "label_validation_viz_cache_job.py"
        )

        # Upload merged_job.py - unified job for analytics and/or viz-cache
        # Supports --job-type: analytics, viz-cache, or all
        self.merged_job_script = aws.s3.BucketObjectv2(
            f"{name}-merged-job-script",
            bucket=self.artifacts_bucket.id,
            key="spark/merged_job.py",
            source=FileAsset(str(merged_job_path)),
            source_hash=file_hash(merged_job_path),
            opts=ResourceOptions(parent=self.artifacts_bucket),
        )

        # Upload label_validation_viz_cache_job.py
        self.label_validation_viz_cache_job_script = aws.s3.BucketObjectv2(
            f"{name}-label-validation-viz-cache-job-script",
            bucket=self.artifacts_bucket.id,
            key="spark/label_validation_viz_cache_job.py",
            source=FileAsset(str(label_validation_viz_cache_path)),
            source_hash=file_hash(label_validation_viz_cache_path),
            opts=ResourceOptions(parent=self.artifacts_bucket),
        )

        # ============================================================
        # Exports
        # ============================================================
        self.register_outputs(
            {
                "emr_application_id": self.emr_application.id,
                "emr_application_arn": self.emr_application.arn,
                "emr_job_role_arn": self.emr_job_role.arn,
                "artifacts_bucket_name": self.artifacts_bucket.id,
                "artifacts_bucket_arn": self.artifacts_bucket.arn,
                "analytics_bucket_name": self.analytics_bucket.id,
                "analytics_bucket_arn": self.analytics_bucket.arn,
            }
        )


def create_emr_serverless_analytics(
    langsmith_export_bucket_arn: Input[str],
    custom_image_uri: Optional[Input[str]] = None,
    cache_bucket_arn: Optional[Input[str]] = None,
    batch_bucket_arn: Optional[Input[str]] = None,
    opts: Optional[ResourceOptions] = None,
) -> EMRServerlessAnalytics:
    """Factory function to create EMR Serverless analytics infrastructure.

    Args:
        langsmith_export_bucket_arn: ARN of the LangSmith export bucket
        custom_image_uri: Optional custom Docker image URI for EMR Serverless
        cache_bucket_arn: Optional ARN of the viz-cache bucket for merged jobs
        batch_bucket_arn: Optional ARN of the batch bucket for merged jobs
        opts: Pulumi resource options
    """
    return EMRServerlessAnalytics(
        f"emr-analytics-{stack}",
        langsmith_export_bucket_arn=langsmith_export_bucket_arn,
        custom_image_uri=custom_image_uri,
        cache_bucket_arn=cache_bucket_arn,
        batch_bucket_arn=batch_bucket_arn,
        opts=opts,
    )

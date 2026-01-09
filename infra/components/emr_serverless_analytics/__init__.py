"""
EMR Serverless Analytics Infrastructure.

This component creates infrastructure for running Spark analytics on LangSmith
trace exports using EMR Serverless, integrated with Step Functions.

Creates:
- EMR Serverless Application (Spark)
- S3 bucket for Spark job artifacts (venv tarball, entry point)
- S3 bucket for analytics output (Parquet)
- IAM role for EMR job execution
- CodeBuild project to package receipt_langsmith[pyspark]
"""

import json
import os
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileArchive,
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
    - EMR Serverless Application (auto-start, auto-stop)
    - S3 buckets for job artifacts and analytics output
    - IAM roles with appropriate permissions
    - CodeBuild project for packaging Python dependencies
    """

    def __init__(
        self,
        name: str,
        *,
        langsmith_export_bucket_arn: Input[str],
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

        # Convert input to Output
        langsmith_bucket_arn = Output.from_input(langsmith_export_bucket_arn)

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
        self.emr_application = aws.emrserverless.Application(
            f"{name}-app",
            name=f"langsmith-analytics-{stack}",
            release_label="emr-7.0.0",
            type="SPARK",
            initial_capacities=[
                aws.emrserverless.ApplicationInitialCapacityArgs(
                    initial_capacity_type="Driver",
                    initial_capacity_config=aws.emrserverless.ApplicationInitialCapacityInitialCapacityConfigArgs(
                        worker_count=1,
                        worker_configuration=aws.emrserverless.ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs(
                            cpu="2 vCPU",
                            memory="4 GB",
                        ),
                    ),
                ),
                aws.emrserverless.ApplicationInitialCapacityArgs(
                    initial_capacity_type="Executor",
                    initial_capacity_config=aws.emrserverless.ApplicationInitialCapacityInitialCapacityConfigArgs(
                        worker_count=4,
                        worker_configuration=aws.emrserverless.ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs(
                            cpu="2 vCPU",
                            memory="4 GB",
                        ),
                    ),
                ),
            ],
            maximum_capacity=aws.emrserverless.ApplicationMaximumCapacityArgs(
                cpu="16 vCPU",
                memory="64 GB",
            ),
            auto_start_configuration=aws.emrserverless.ApplicationAutoStartConfigurationArgs(
                enabled=True,
            ),
            auto_stop_configuration=aws.emrserverless.ApplicationAutoStopConfigurationArgs(
                enabled=True,
                idle_timeout_minutes=15,
            ),
            tags={
                "Name": f"{name}-app",
                "Purpose": "LangSmith analytics",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
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
        aws.iam.RolePolicy(
            f"{name}-emr-job-policy",
            role=self.emr_job_role.id,
            policy=Output.all(
                langsmith_bucket_arn,
                self.artifacts_bucket.arn,
                self.analytics_bucket.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # Read LangSmith Parquet exports
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:ListBucket"],
                                "Resource": [args[0], f"{args[0]}/*"],
                            },
                            # Read Spark job artifacts
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:ListBucket"],
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
                                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/emr-serverless/*",
                                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/emr-serverless/*:*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.emr_job_role),
        )

        # ============================================================
        # CodeBuild Role for Packaging
        # ============================================================
        self.codebuild_role = aws.iam.Role(
            f"{name}-codebuild-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "codebuild.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Name": f"{name}-codebuild-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # CodeBuild policy
        aws.iam.RolePolicy(
            f"{name}-codebuild-policy",
            role=self.codebuild_role.id,
            policy=self.artifacts_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
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
                            # S3 access for source and artifacts
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [arn, f"{arn}/*"],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.codebuild_role),
        )

        # ============================================================
        # CodeBuild Project for Packaging
        # ============================================================
        buildspec = """
version: 0.2

phases:
  install:
    runtime-versions:
      python: 3.12
    commands:
      - pip install virtualenv venv-pack

  build:
    commands:
      - echo "Creating virtual environment..."
      - python -m venv spark_env
      - source spark_env/bin/activate
      - echo "Installing receipt_langsmith[pyspark]..."
      - pip install ./receipt_langsmith[pyspark]
      - echo "Packaging environment..."
      - venv-pack -o spark_env.tar.gz

  post_build:
    commands:
      - echo "Uploading artifacts to S3..."
      - aws s3 cp spark_env.tar.gz s3://${ARTIFACTS_BUCKET}/spark/spark_env.tar.gz
      - aws s3 cp receipt_langsmith/spark/emr_job.py s3://${ARTIFACTS_BUCKET}/spark/emr_job.py
      - echo "Done!"

artifacts:
  files:
    - spark_env.tar.gz
"""

        self.codebuild_project = aws.codebuild.Project(
            f"{name}-packager",
            description="Packages receipt_langsmith[pyspark] for EMR Serverless",
            build_timeout=30,
            service_role=self.codebuild_role.arn,
            environment=aws.codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_MEDIUM",
                image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                type="LINUX_CONTAINER",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="ARTIFACTS_BUCKET",
                        value=self.artifacts_bucket.id,
                    ),
                ],
            ),
            source=aws.codebuild.ProjectSourceArgs(
                type="S3",
                location=self.artifacts_bucket.id.apply(
                    lambda b: f"{b}/source/source.zip"
                ),
                buildspec=buildspec,
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
            logs_config=aws.codebuild.ProjectLogsConfigArgs(
                cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                    group_name=f"/aws/codebuild/{name}-packager",
                    status="ENABLED",
                ),
            ),
            tags={
                "Name": f"{name}-packager",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Upload Source Archive
        # ============================================================
        # Create source archive with receipt_langsmith package
        receipt_langsmith_path = REPO_ROOT / "receipt_langsmith"

        self.source_object = aws.s3.BucketObjectv2(
            f"{name}-source-object",
            bucket=self.artifacts_bucket.id,
            key="source/source.zip",
            source=AssetArchive(
                {
                    "receipt_langsmith": FileArchive(
                        str(receipt_langsmith_path)
                    ),
                }
            ),
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
                "codebuild_project_name": self.codebuild_project.name,
            }
        )


def create_emr_serverless_analytics(
    langsmith_export_bucket_arn: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> EMRServerlessAnalytics:
    """Factory function to create EMR Serverless analytics infrastructure."""
    return EMRServerlessAnalytics(
        f"emr-analytics-{stack}",
        langsmith_export_bucket_arn=langsmith_export_bucket_arn,
        opts=opts,
    )

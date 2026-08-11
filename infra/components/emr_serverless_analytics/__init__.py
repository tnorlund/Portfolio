"""
EMR Serverless Analytics Infrastructure.

This component creates infrastructure for running Spark analytics on LangSmith
trace exports using EMR Serverless, integrated with Step Functions.

Creates:
- EMR Serverless Application (Spark 4 with Python 3.13)
- S3 bucket for Spark job artifacts (Python environment and entry points)
- S3 bucket for analytics output (Parquet)
- IAM role for EMR job execution
- CodeBuild project to package receipt_langsmith for EMR
"""

import hashlib
import json
import shlex
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileArchive,
    FileAsset,
    Input,
    Output,
    ResourceOptions,
)
from pulumi_aws.emrserverless import (
    ApplicationAutoStartConfigurationArgs,
    ApplicationAutoStopConfigurationArgs,
    ApplicationInitialCapacityArgs,
    ApplicationInitialCapacityInitialCapacityConfigArgs,
    ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs,
    ApplicationMaximumCapacityArgs,
    ApplicationRuntimeConfigurationArgs,
)

from infra.components.emr_serverless_runtime import (
    EMR_PYTHON_VERSION,
    EMR_SPARK_RELEASE,
    EMR_SPARK_VERSION,
    spark_runtime_properties,
)
from infra.shared.build_utils import compute_hash

# Get repo root (three levels up from this file)
REPO_ROOT = Path(__file__).resolve().parents[3]

# Get stack configuration
stack = pulumi.get_stack()


def _python_environment_buildspec() -> dict[str, object]:
    """Return the CodeBuild buildspec for the portable Python environment."""
    return {
        "version": 0.2,
        "phases": {
            "install": {
                "runtime-versions": {"python": EMR_PYTHON_VERSION},
                "commands": [
                    ("dnf install -y python3.13 python3.13-pip"),
                    "/usr/bin/python3.13 --version",
                ],
            },
            "build": {
                "commands": [
                    "echo Creating Python 3.13 environment for EMR Spark...",
                    "/usr/bin/python3.13 -m venv spark_env",
                    "spark_env/bin/python -m pip install --upgrade pip",
                    (
                        "spark_env/bin/python -m pip install "
                        "./receipt_langsmith venv-pack"
                    ),
                    (
                        "spark_env/bin/python -c "
                        '"from importlib.metadata import version; '
                        "print(version('receipt-langsmith'))\""
                    ),
                    (
                        "spark_env/bin/python -c "
                        '"import importlib.util; '
                        "assert importlib.util.find_spec('pyspark') is None; "
                        "assert importlib.util.find_spec('pyarrow') is None"
                        '"'
                    ),
                    (
                        "sed -i "
                        "'s/include-system-site-packages = false/"
                        "include-system-site-packages = true/' "
                        "spark_env/pyvenv.cfg"
                    ),
                    (
                        'test "$(readlink spark_env/bin/python3.13)" '
                        '= "/usr/bin/python3.13"'
                    ),
                    (
                        'VIRTUAL_ENV="$PWD/spark_env" '
                        "spark_env/bin/venv-pack -f "
                        "-o python-environment.tar.gz"
                    ),
                ]
            },
            "post_build": {
                "commands": [
                    (
                        "aws s3 cp python-environment.tar.gz "
                        '"s3://${ARTIFACTS_BUCKET}/${PYTHON_ENVIRONMENT_KEY}" '
                        "--no-progress"
                    ),
                    (
                        "echo Uploaded Python environment to "
                        '"s3://${ARTIFACTS_BUCKET}/'
                        '${PYTHON_ENVIRONMENT_KEY}"'
                    ),
                ]
            },
        },
    }


def _wait_for_codebuild_script(project_name: str) -> str:
    """Return a deployment command that builds and verifies the environment."""
    safe_project_name = shlex.quote(project_name)
    return f"""#!/usr/bin/env bash
set -eu
PROJECT_NAME={safe_project_name}
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT_NAME" \
  --query 'build.id' --output text)
echo "Started Python environment build: $BUILD_ID"
for ATTEMPT in $(seq 1 180); do
  STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
    --query 'builds[0].buildStatus' --output text)
  echo "CodeBuild status: $STATUS"
  case "$STATUS" in
    SUCCEEDED)
      echo "Python environment build completed"
      exit 0
      ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      echo "Python environment build failed: $STATUS"
      exit 1
      ;;
  esac
  sleep 10
done
echo "Timed out waiting for Python environment build"
exit 1
"""


class EMRServerlessAnalytics(ComponentResource):
    """EMR Serverless infrastructure for LangSmith Spark analytics.

    This component provides:
    - EMR Serverless Spark 8 application (auto-start, auto-stop)
    - Packaged Python 3.13 environment shared by drivers and executors
    - S3 buckets for job artifacts and analytics output
    - IAM roles with appropriate permissions
    """

    def __init__(
        self,
        name: str,
        *,
        langsmith_export_bucket_arn: Input[str],
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

        self.python_environment_uri = self._create_python_environment(name)

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
                    expiration=(
                        aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                            days=30,
                        )
                    ),
                ),
            ],
            opts=ResourceOptions(parent=self.analytics_bucket),
        )

        # ============================================================
        # EMR Serverless Application
        # ============================================================
        # EMR Spark 8 provides Spark 4.0.2 and native Python 3.13. The
        # application-wide runtime configuration attaches the packaged
        # receipt_langsmith environment to every job submission.
        # Pulumi's generated worker-configuration type exceeds 79 chars.
        # pylint: disable=line-too-long
        driver_worker = ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs(
            cpu="2 vCPU",
            memory="4 GB",
        )
        executor_worker = ApplicationInitialCapacityInitialCapacityConfigWorkerConfigurationArgs(
            cpu="2 vCPU",
            memory="4 GB",
        )
        # pylint: enable=line-too-long
        driver_capacity = ApplicationInitialCapacityInitialCapacityConfigArgs(
            worker_count=1,
            worker_configuration=driver_worker,
        )
        executor_capacity = (
            ApplicationInitialCapacityInitialCapacityConfigArgs(
                worker_count=4,
                worker_configuration=executor_worker,
            )
        )
        self.emr_application = aws.emrserverless.Application(
            f"{name}-app",
            name=f"langsmith-analytics-{stack}",
            release_label=EMR_SPARK_RELEASE,
            type="SPARK",
            runtime_configurations=[
                ApplicationRuntimeConfigurationArgs(
                    classification="spark-defaults",
                    properties=self.python_environment_uri.apply(
                        spark_runtime_properties
                    ),
                )
            ],
            initial_capacities=[
                ApplicationInitialCapacityArgs(
                    initial_capacity_type="Driver",
                    initial_capacity_config=driver_capacity,
                ),
                ApplicationInitialCapacityArgs(
                    initial_capacity_type="Executor",
                    initial_capacity_config=executor_capacity,
                ),
            ],
            maximum_capacity=ApplicationMaximumCapacityArgs(
                cpu="32 vCPU",
                memory="128 GB",
            ),
            auto_start_configuration=ApplicationAutoStartConfigurationArgs(
                enabled=True,
            ),
            auto_stop_configuration=ApplicationAutoStopConfigurationArgs(
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
        # Include optional buckets in the policy inputs when configured.
        policy_outputs = [
            langsmith_bucket_arn,  # 0
            self.artifacts_bucket.arn,  # 1
            self.analytics_bucket.arn,  # 2
        ]
        if self.cache_bucket_arn:
            policy_outputs.append(self.cache_bucket_arn)  # 3
        if self.batch_bucket_arn:
            policy_outputs.append(
                self.batch_bucket_arn
            )  # 4 (or 3 if no cache)

        def build_policy(args: list[str]) -> str:
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
            ]

            # Add cache bucket permissions if provided
            idx = 3
            if self.cache_bucket_arn and len(args) > idx:
                statements.append(
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [args[idx], f"{args[idx]}/*"],
                    }
                )
                idx += 1

            # Add batch bucket permissions if provided
            if self.batch_bucket_arn and len(args) > idx:
                statements.append(
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [args[idx], f"{args[idx]}/*"],
                    }
                )

            return json.dumps(
                {"Version": "2012-10-17", "Statement": statements}
            )

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
            return hashlib.md5(path.read_bytes()).hexdigest()[
                :12
            ]  # noqa: S324

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
                "python_environment_uri": self.python_environment_uri,
                "python_environment_build": (self.python_environment_build.id),
            }
        )

    def _create_python_environment(self, name: str) -> Output[str]:
        """Package non-native dependencies in an AL2023 environment."""
        package_root = REPO_ROOT / "receipt_langsmith"
        content_hash = compute_hash(
            [
                package_root / "pyproject.toml",
                package_root / "receipt_langsmith",
            ],
            include_globs=["**/*.py", "**/py.typed"],
            extra_strings={
                "emr_release": EMR_SPARK_RELEASE,
                "spark_version": EMR_SPARK_VERSION,
                "python_version": EMR_PYTHON_VERSION,
                "buildspec": json.dumps(
                    _python_environment_buildspec(),
                    sort_keys=True,
                ),
            },
        )
        short_hash = content_hash[:12]
        source_key = f"source/receipt-langsmith-{short_hash}.zip"
        environment_key = f"spark/python-environment-{short_hash}.tar.gz"

        source_archive = AssetArchive(
            {
                "receipt_langsmith": AssetArchive(
                    {
                        "pyproject.toml": FileAsset(
                            str(package_root / "pyproject.toml")
                        ),
                        "receipt_langsmith": FileArchive(
                            str(package_root / "receipt_langsmith")
                        ),
                    }
                )
            }
        )
        source_object = aws.s3.BucketObjectv2(
            f"{name}-python-environment-source",
            bucket=self.artifacts_bucket.id,
            key=source_key,
            source=source_archive,
            source_hash=content_hash,
            opts=ResourceOptions(parent=self.artifacts_bucket),
        )

        log_group = aws.cloudwatch.LogGroup(
            f"{name}-python-environment-logs",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )
        codebuild_role = aws.iam.Role(
            f"{name}-python-environment-role",
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
            opts=ResourceOptions(parent=self),
        )

        codebuild_policy = aws.iam.RolePolicy(
            f"{name}-python-environment-policy",
            role=codebuild_role.id,
            policy=Output.all(
                self.artifacts_bucket.arn,
                log_group.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [args[0], f"{args[0]}/*"],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": [args[1], f"{args[1]}:*"],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=codebuild_role),
        )

        self.python_environment_project = aws.codebuild.Project(
            f"{name}-python-environment",
            description=(
                "Package receipt_langsmith for EMR Spark 8 / Python 3.13"
            ),
            build_timeout=30,
            service_role=codebuild_role.arn,
            environment=aws.codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_MEDIUM",
                image="aws/codebuild/amazonlinux-x86_64-standard:5.0",
                type="LINUX_CONTAINER",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="ARTIFACTS_BUCKET",
                        value=self.artifacts_bucket.id,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PYTHON_ENVIRONMENT_KEY",
                        value=environment_key,
                    ),
                ],
            ),
            source=aws.codebuild.ProjectSourceArgs(
                type="S3",
                location=Output.concat(
                    self.artifacts_bucket.id,
                    "/",
                    source_key,
                ),
                buildspec=json.dumps(_python_environment_buildspec()),
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
            logs_config=aws.codebuild.ProjectLogsConfigArgs(
                cloudwatch_logs=(
                    aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                        group_name=log_group.name,
                        status="ENABLED",
                    )
                )
            ),
            opts=ResourceOptions(
                parent=self,
                depends_on=[source_object, codebuild_policy],
            ),
        )

        wait_command = self.python_environment_project.name.apply(
            _wait_for_codebuild_script
        )
        self.python_environment_build = command.local.Command(
            f"{name}-python-environment-build",
            create=wait_command,
            update=wait_command,
            triggers=[content_hash],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    source_object,
                    self.python_environment_project,
                ],
            ),
        )

        return Output.all(
            self.artifacts_bucket.id,
            self.python_environment_build.stdout,
        ).apply(lambda args: f"s3://{args[0]}/{environment_key}")


def create_emr_serverless_analytics(
    langsmith_export_bucket_arn: Input[str],
    cache_bucket_arn: Optional[Input[str]] = None,
    batch_bucket_arn: Optional[Input[str]] = None,
    opts: Optional[ResourceOptions] = None,
) -> EMRServerlessAnalytics:
    """Factory function to create EMR Serverless analytics infrastructure.

    Args:
        langsmith_export_bucket_arn: ARN of the LangSmith export bucket
        cache_bucket_arn: Optional ARN of the viz-cache bucket for merged jobs
        batch_bucket_arn: Optional ARN of the batch bucket for merged jobs
        opts: Pulumi resource options
    """
    return EMRServerlessAnalytics(
        f"emr-analytics-{stack}",
        langsmith_export_bucket_arn=langsmith_export_bucket_arn,
        cache_bucket_arn=cache_bucket_arn,
        batch_bucket_arn=batch_bucket_arn,
        opts=opts,
    )

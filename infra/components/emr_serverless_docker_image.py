"""
EMR Serverless Docker Image Builder.

Creates a custom Docker image for EMR Serverless Spark jobs with the
receipt_langsmith package pre-installed. Similar to CodeBuildDockerImage
but optimized for EMR Serverless.

The component:
- Creates an ECR repository for the Spark image
- Uploads the Dockerfile and receipt_langsmith package to S3
- Uses CodeBuild to build and push the image
- Configures EMR Serverless Application with the custom image URI
"""

import json
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

import pulumi
import pulumi_command as command
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import config, get_caller_identity
from pulumi_aws.codebuild import (
    Project,
    ProjectArtifactsArgs,
    ProjectEnvironmentArgs,
    ProjectEnvironmentEnvironmentVariableArgs,
    ProjectLogsConfigArgs,
    ProjectLogsConfigCloudwatchLogsArgs,
    ProjectSourceArgs,
)
from pulumi_aws.codepipeline import (
    Pipeline,
    PipelineArtifactStoreArgs,
    PipelineStageActionArgs,
    PipelineStageArgs,
)
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    RepositoryPolicy,
)
from pulumi_aws.iam import Role, RolePolicy

from infra.shared.build_utils import (
    compute_hash,
    make_artifact_bucket,
    make_log_group,
    resolve_build_config,
)
from infra.utils import _find_project_root

PROJECT_DIR = _find_project_root()


def _emr_spark_buildspec() -> Dict[str, Any]:
    """Generate CodeBuild buildspec for EMR Serverless Spark image."""
    return {
        "version": 0.2,
        "phases": {
            "pre_build": {
                "commands": [
                    "echo Logging in to Amazon ECR...",
                    (
                        "aws ecr get-login-password --region $AWS_DEFAULT_REGION | "
                        "docker login --username AWS --password-stdin $ECR_REGISTRY"
                    ),
                    "echo Logging in to public ECR for base image...",
                    (
                        "aws ecr-public get-login-password --region us-east-1 | "
                        "docker login --username AWS --password-stdin public.ecr.aws"
                    ),
                    "echo Listing build context...",
                    "ls -la",
                    "ls -la context/",
                ]
            },
            "build": {
                "commands": [
                    "echo Build started on `date`",
                    "cd context",
                    "echo Building EMR Serverless Spark image...",
                    "export DOCKER_BUILDKIT=1",
                    (
                        "docker build --platform linux/amd64 "
                        "--cache-from $ECR_REGISTRY/$REPOSITORY_NAME:cache "
                        "--build-arg BUILDKIT_INLINE_CACHE=1 "
                        "-t $ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG "
                        "-t $ECR_REGISTRY/$REPOSITORY_NAME:latest "
                        "-f Dockerfile ."
                    ),
                    "echo Build completed on `date`",
                ]
            },
            "post_build": {
                "commands": [
                    "echo Pushing Docker image to ECR...",
                    "docker push $ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG",
                    "docker push $ECR_REGISTRY/$REPOSITORY_NAME:latest",
                    (
                        "docker tag $ECR_REGISTRY/$REPOSITORY_NAME:latest "
                        "$ECR_REGISTRY/$REPOSITORY_NAME:cache"
                    ),
                    "docker push $ECR_REGISTRY/$REPOSITORY_NAME:cache || true",
                    "echo Getting image digest...",
                    (
                        "IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "
                        "$ECR_REGISTRY/$REPOSITORY_NAME:latest | cut -d'@' -f2)"
                    ),
                    "echo Image digest: $IMAGE_DIGEST",
                    "IMAGE_URI=$ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG",
                    "echo Image URI: $IMAGE_URI",
                    # Update EMR Application if EMR_APPLICATION_NAME is set
                    # Note: Uses jq instead of --query to handle hyphens in names
                    (
                        'if [ -n "$EMR_APPLICATION_NAME" ]; then '
                        'echo "Finding EMR Application by name: $EMR_APPLICATION_NAME..." && '
                        "EMR_APP_ID=$(aws emr-serverless list-applications --output json | "
                        'jq -r --arg name "$EMR_APPLICATION_NAME" '
                        "'.applications[] | select(.name == $name) | .id' | head -1) && "
                        'if [ -n "$EMR_APP_ID" ] && [ "$EMR_APP_ID" != "null" ]; then '
                        'echo "Found EMR Application: $EMR_APP_ID" && '
                        "STATE=$(aws emr-serverless get-application --application-id $EMR_APP_ID "
                        "--query 'application.state' --output text) && "
                        'echo "Current state: $STATE" && '
                        'if [ "$STATE" = "STARTED" ]; then '
                        'echo "Stopping EMR Application..." && '
                        "aws emr-serverless stop-application --application-id $EMR_APP_ID && "
                        "for i in 1 2 3 4 5 6 7 8 9 10 11 12; do sleep 5; "
                        "STATE=$(aws emr-serverless get-application --application-id $EMR_APP_ID "
                        "--query 'application.state' --output text); "
                        'echo "  State: $STATE"; '
                        '[ "$STATE" = "STOPPED" ] && break; done; fi && '
                        'echo "Updating EMR Application image to: $IMAGE_URI" && '
                        "aws emr-serverless update-application --application-id $EMR_APP_ID "
                        "--image-configuration imageUri=$IMAGE_URI && "
                        'echo "EMR Application updated successfully"; '
                        'else echo "EMR Application not found with name: $EMR_APPLICATION_NAME"; fi; fi'
                    ),
                    "echo Push completed on `date`",
                ]
            },
        },
    }


class EMRServerlessDockerImage(ComponentResource):
    """Docker image builder for EMR Serverless Spark jobs.

    This component:
    - Creates ECR repository for the Spark image
    - Uploads Dockerfile + receipt_langsmith package to S3
    - Uses CodeBuild to build the image with EMR Serverless base
    - Pushes to ECR with content-based tags
    - Provides image URI for EMR Serverless Application configuration
    """

    def __init__(
        self,
        name: str,
        *,
        emr_release: str = "emr-spark-8.0-preview",
        emr_application_name: Optional[str] = None,
        sync_mode: Optional[bool] = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        """Create EMR Serverless Docker image builder.

        Args:
            name: Resource name prefix
            emr_release: EMR release label (default: emr-spark-8.0-preview)
            emr_application_name: Name of EMR Application to update after build
            sync_mode: If True, wait for build to complete
            opts: Pulumi resource options
        """
        super().__init__(f"emr-serverless-docker:{name}", name, {}, opts)

        self.name = name
        self.emr_release = emr_release
        self.emr_application_name = emr_application_name
        self.stack = pulumi.get_stack()

        # Configure build mode
        (
            self.sync_mode,
            self.force_rebuild,
            self.debug_mode,
        ) = resolve_build_config(
            "emr-docker-build",
            sync_override=sync_mode,
            ci_default_sync=True,
        )

        # Calculate content hash for change detection
        content_hash = self._calculate_content_hash()

        if self.sync_mode:
            pulumi.log.info(
                f"Building EMR image '{self.name}' in SYNC mode (will wait)"
            )
        else:
            pulumi.log.info(
                f"EMR image '{self.name}' in ASYNC mode (fast pulumi up)"
            )
            pulumi.log.info(
                f"   Hash: {content_hash[:12]}... - will build only if changed"
            )

        # Create ECR repository
        self.ecr_repo = Repository(
            f"{self.name}-emr-repo",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Add ECR repository policy to allow EMR Serverless to pull images
        RepositoryPolicy(
            f"{self.name}-emr-repo-policy",
            repository=self.ecr_repo.name,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowEMRServerless",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "emr-serverless.amazonaws.com"
                            },
                            "Action": [
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:DescribeImages",
                            ],
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self.ecr_repo),
        )

        # Setup build pipeline
        self._setup_pipeline(content_hash)

        # Export outputs - use content hash as tag so EMR Application updates
        # when code changes (EMR caches resolved digest at application update time)
        self.repository_url = self.ecr_repo.repository_url
        self.content_hash = content_hash[:12]
        self.image_uri = self.ecr_repo.repository_url.apply(
            lambda url, tag=self.content_hash: f"{url}:{tag}"
        )

        self.register_outputs(
            {
                "repository_url": self.repository_url,
                "image_uri": self.image_uri,
                "content_hash": self.content_hash,
            }
        )

    def _calculate_content_hash(self) -> str:
        """Calculate hash of Dockerfile and receipt_langsmith package."""
        paths = []

        # Hash the receipt_langsmith package
        receipt_langsmith_path = Path(PROJECT_DIR) / "receipt_langsmith"
        if receipt_langsmith_path.exists():
            paths.append(receipt_langsmith_path)

        return compute_hash(
            paths,
            include_globs=[
                "**/*.py",
                "**/pyproject.toml",
                "**/requirements.txt",
            ],
            extra_strings={
                "emr_release": self.emr_release,
                # Include Dockerfile content so changes to generation logic trigger rebuilds
                "dockerfile": self._generate_dockerfile(),
            },
        )

    def _generate_dockerfile(self) -> str:
        """Generate Dockerfile content for EMR Serverless.

        Uses EMR 7.x base image with Python 3.12 installed on top,
        since EMR 8.0 preview doesn't have a public Docker base image yet.
        """
        # Extract base release (e.g., "7.5.0" from "emr-7.5.0" or use default)
        base_release = "7.5.0"  # EMR 7.5.0 supports custom images
        if "7." in self.emr_release:
            # Extract version number if provided
            parts = self.emr_release.replace("emr-", "").split("-")
            for part in parts:
                if part.startswith("7."):
                    base_release = part
                    break

        return f"""# EMR Serverless Spark image with receipt_langsmith and Python 3.12
# Base: EMR {base_release} with Python 3.12 installed
FROM public.ecr.aws/emr-serverless/spark/emr-{base_release}:latest

USER root

# Install Python 3.12 and set as PySpark default
RUN dnf install -y python3.12 python3.12-pip python3.12-devel && \\
    alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \\
    python3.12 -m pip install --upgrade pip

# Set PySpark to use Python 3.12
ENV PYSPARK_PYTHON=/usr/bin/python3.12
ENV PYSPARK_DRIVER_PYTHON=/usr/bin/python3.12

# Install receipt_langsmith with pyspark extras
COPY receipt_langsmith /tmp/receipt_langsmith
RUN python3.12 -m pip install /tmp/receipt_langsmith[pyspark] && \\
    rm -rf /tmp/receipt_langsmith

# Verify installation
RUN python3.12 -c "import receipt_langsmith; print(receipt_langsmith.__file__)"
RUN python3.12 --version

USER hadoop:hadoop
"""

    def _generate_upload_script(self, bucket: str, content_hash: str) -> str:
        """Generate script to upload build context to S3."""
        safe_bucket = shlex.quote(bucket)
        project_root_abs = str(Path(PROJECT_DIR).resolve())
        safe_project_root = shlex.quote(project_root_abs)

        dockerfile_content = self._generate_dockerfile()
        # Escape the Dockerfile content for shell
        safe_dockerfile = dockerfile_content.replace("'", "'\\''")

        return f"""#!/usr/bin/env bash
set -e

cd {safe_project_root}

BUCKET={safe_bucket}
HASH="{content_hash}"
NAME="{self.name}"
FORCE_REBUILD="{self.force_rebuild}"

echo "Checking if context upload needed for EMR image '$NAME'..."
STORED_HASH=$(aws s3 cp "s3://$BUCKET/$NAME/hash.txt" - 2>/dev/null || echo '')
if [ "$STORED_HASH" = "$HASH" ] && [ "$FORCE_REBUILD" != "True" ]; then
  echo "Context up-to-date. Skipping upload."
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "Preparing EMR Serverless build context..."
mkdir -p "$TMP/context"

# Copy receipt_langsmith package
echo "  - Copying receipt_langsmith package..."
rsync -a \\
    --exclude='__pycache__' \\
    --exclude='*.pyc' \\
    --exclude='.pytest_cache' \\
    --exclude='.mypy_cache' \\
    --exclude='tests/' \\
    --exclude='.venv/' \\
    --exclude='htmlcov/' \\
    --exclude='*.egg-info/' \\
    receipt_langsmith/ "$TMP/context/receipt_langsmith/"

# Generate Dockerfile
echo "  - Generating Dockerfile..."
cat > "$TMP/context/Dockerfile" << 'DOCKERFILE_EOF'
{safe_dockerfile}
DOCKERFILE_EOF

cd "$TMP"
echo "Creating context archive..."
zip -qr context.zip context
CONTEXT_SIZE=$(du -h context.zip | cut -f1)
echo "  Context size: $CONTEXT_SIZE"
cd - >/dev/null

echo "Uploading to S3..."
aws s3 cp "$TMP/context.zip" "s3://$BUCKET/$NAME/context.zip" --no-progress
echo -n "$HASH" | aws s3 cp - "s3://$BUCKET/$NAME/hash.txt"
HASH_SHORT=$(echo "$HASH" | cut -c1-12)
echo "Uploaded context.zip (hash: $HASH_SHORT..., size: $CONTEXT_SIZE)"
"""

    def _setup_pipeline(self, content_hash: str) -> None:
        """Setup S3, CodeBuild, and CodePipeline for Docker builds."""
        # Artifact bucket
        build_bucket, bucket_versioning, encryption = make_artifact_bucket(
            f"{self.name}-emr", parent=self
        )

        # Upload context command
        upload_cmd_deps = [build_bucket]
        if bucket_versioning:
            upload_cmd_deps.append(bucket_versioning)
        if encryption:
            upload_cmd_deps.append(encryption)

        upload_cmd = command.local.Command(
            f"{self.name}-emr-upload-context",
            create=build_bucket.bucket.apply(
                lambda b: self._generate_upload_script(b, content_hash)
            ),
            update=build_bucket.bucket.apply(
                lambda b: self._generate_upload_script(b, content_hash)
            ),
            triggers=[content_hash],
            opts=ResourceOptions(
                parent=self,
                delete_before_replace=True,
                depends_on=upload_cmd_deps,
            ),
        )

        # IAM role for CodeBuild
        codebuild_role = Role(
            f"{self.name}-emr-cb-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codebuild.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codepipeline.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        },
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create CloudWatch log group
        log_group = make_log_group(
            f"{self.name}-emr-builder-logs",
            retention_days=14,
            parent=self,
        )

        # CodeBuild policy
        log_resources = log_group.arn.apply(lambda arn: [arn, f"{arn}:*"])

        RolePolicy(
            f"{self.name}-emr-cb-policy",
            role=codebuild_role.id,
            policy=Output.all(
                build_bucket.arn,
                self.ecr_repo.arn,
                log_resources,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogGroup",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": args[2],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                    "s3:PutObject",
                                ],
                                "Resource": f"{args[0]}/*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetBucketAcl",
                                    "s3:GetBucketLocation",
                                    "s3:ListBucket",
                                ],
                                "Resource": args[0],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecr:GetAuthorizationToken",
                                    "ecr:BatchCheckLayerAvailability",
                                    "ecr:GetDownloadUrlForLayer",
                                    "ecr:BatchGetImage",
                                    "ecr:PutImage",
                                    "ecr:InitiateLayerUpload",
                                    "ecr:UploadLayerPart",
                                    "ecr:CompleteLayerUpload",
                                ],
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["ecr-public:GetAuthorizationToken"],
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["sts:GetServiceBearerToken"],
                                "Resource": "*",
                            },
                            # EMR Serverless permissions for updating application after build
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "emr-serverless:ListApplications",
                                    "emr-serverless:GetApplication",
                                    "emr-serverless:StopApplication",
                                    "emr-serverless:UpdateApplication",
                                ],
                                "Resource": "*",
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # CodeBuild project
        codebuild_project = Project(
            f"{self.name}-emr-builder",
            service_role=codebuild_role.arn,
            source=ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(_emr_spark_buildspec()),
            ),
            artifacts=ProjectArtifactsArgs(type="CODEPIPELINE"),
            environment=ProjectEnvironmentArgs(
                type="LINUX_CONTAINER",
                compute_type="BUILD_GENERAL1_MEDIUM",
                image="aws/codebuild/amazonlinux-x86_64-standard:5.0",
                privileged_mode=True,  # Required for Docker builds
                environment_variables=[
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="ECR_REGISTRY",
                        value=self.ecr_repo.repository_url.apply(
                            lambda url: url.split("/")[0]
                        ),
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="REPOSITORY_NAME",
                        value=self.ecr_repo.name,
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="IMAGE_TAG",
                        value=content_hash[:12],
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="EMR_APPLICATION_NAME",
                        value=self.emr_application_name or "",
                    ),
                ],
            ),
            build_timeout=30,
            logs_config=ProjectLogsConfigArgs(
                cloudwatch_logs=ProjectLogsConfigCloudwatchLogsArgs(
                    status="ENABLED",
                    group_name=log_group.name,
                ),
            ),
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # Pipeline role
        pipeline_role = Role(
            f"{self.name}-emr-pl-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codepipeline.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Pipeline policies
        RolePolicy(
            f"{self.name}-emr-pl-s3",
            role=pipeline_role.id,
            policy=build_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                    "s3:PutObject",
                                    "s3:GetBucketVersioning",
                                    "s3:ListBucketVersions",
                                    "s3:GetBucketAcl",
                                    "s3:GetBucketLocation",
                                    "s3:ListBucket",
                                ],
                                "Resource": [arn, f"{arn}/*"],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        RolePolicy(
            f"{self.name}-emr-pl-cb",
            role=pipeline_role.id,
            policy=codebuild_project.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "codebuild:StartBuild",
                                    "codebuild:BatchGetBuilds",
                                ],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        RolePolicy(
            f"{self.name}-emr-pl-passrole",
            role=pipeline_role.id,
            policy=codebuild_role.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["iam:PassRole"],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # CodePipeline
        pipeline = Pipeline(
            f"{self.name}-emr-pipeline",
            role_arn=pipeline_role.arn,
            artifact_stores=[
                PipelineArtifactStoreArgs(
                    type="S3",
                    location=build_bucket.bucket,
                )
            ],
            stages=[
                PipelineStageArgs(
                    name="Source",
                    actions=[
                        PipelineStageActionArgs(
                            name="Source",
                            category="Source",
                            owner="AWS",
                            provider="S3",
                            version="1",
                            output_artifacts=["SourceArtifact"],
                            configuration={
                                "S3Bucket": build_bucket.bucket,
                                "S3ObjectKey": f"{self.name}/context.zip",
                                "PollForSourceChanges": "false",
                            },
                            run_order=1,
                        )
                    ],
                ),
                PipelineStageArgs(
                    name="BuildAndPush",
                    actions=[
                        PipelineStageActionArgs(
                            name="Build",
                            category="Build",
                            owner="AWS",
                            provider="CodeBuild",
                            version="1",
                            input_artifacts=["SourceArtifact"],
                            run_order=1,
                            configuration={
                                "ProjectName": codebuild_project.name,
                                "PrimarySource": "SourceArtifact",
                            },
                        )
                    ],
                ),
            ],
            opts=ResourceOptions(
                parent=self,
                depends_on=(
                    [r for r in [bucket_versioning, encryption] if r] or None
                ),
            ),
        )

        # Trigger pipeline
        if not self.sync_mode:
            # Async: trigger and continue
            trigger_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "Triggering EMR image build pipeline for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} \\
  --query pipelineExecutionId --output text)
echo "Pipeline triggered: $EXEC_ID"
echo "View logs: https://console.aws.amazon.com/codesuite/codepipeline/pipelines/{pn}/view"
"""
            )
            trigger_deps = [upload_cmd, pipeline]
            if bucket_versioning:
                trigger_deps.append(bucket_versioning)
            if encryption:
                trigger_deps.append(encryption)

            command.local.Command(
                f"{self.name}-emr-trigger-pipeline",
                create=trigger_script,
                update=trigger_script,
                triggers=[content_hash],
                opts=ResourceOptions(parent=self, depends_on=trigger_deps),
            )
        else:
            # Sync: wait for completion
            sync_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "SYNC: Starting EMR image build pipeline for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} \\
  --query pipelineExecutionId --output text)
echo "Execution ID: $EXEC_ID"
sleep 5
while true; do
  STATUS=$(aws codepipeline get-pipeline-execution --pipeline-name {pn} \\
    --pipeline-execution-id $EXEC_ID \\
    --query "pipelineExecution.status" --output text)
  echo "Pipeline status: $STATUS"
  if [ "$STATUS" = "Succeeded" ]; then
    echo "EMR image build completed successfully"
    break
  elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Superseded" ]; then
    echo "Pipeline failed with status: $STATUS"
    exit 1
  fi
  sleep 15
done
"""
            )
            sync_deps = [upload_cmd, pipeline]
            if bucket_versioning:
                sync_deps.append(bucket_versioning)
            if encryption:
                sync_deps.append(encryption)

            command.local.Command(
                f"{self.name}-emr-sync-pipeline",
                create=sync_script,
                update=sync_script,
                triggers=[content_hash],
                opts=ResourceOptions(parent=self, depends_on=sync_deps),
            )


def create_emr_serverless_docker_image(
    name: str = "emr-spark",
    emr_release: str = "emr-spark-8.0-preview",
    emr_application_name: Optional[str] = None,
    sync_mode: Optional[bool] = None,
    opts: Optional[ResourceOptions] = None,
) -> EMRServerlessDockerImage:
    """Factory function to create EMR Serverless Docker image builder.

    Args:
        name: Resource name prefix
        emr_release: EMR release label (default: emr-spark-8.0-preview)
        emr_application_name: Name of EMR Application to update after build.
            If provided, CodeBuild will stop the application and update its
            image configuration after pushing the new image.
        sync_mode: If True, wait for build to complete
        opts: Pulumi resource options
    """
    stack = pulumi.get_stack()
    return EMRServerlessDockerImage(
        f"{name}-{stack}",
        emr_release=emr_release,
        emr_application_name=emr_application_name,
        sync_mode=sync_mode,
        opts=opts,
    )

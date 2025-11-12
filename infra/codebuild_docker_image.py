#!/usr/bin/env python3
"""
codebuild_docker_image.py

AWS CodeBuild-based Docker image builder for Lambda functions.
Offloads Docker builds to AWS for faster pulumi up times.

Similar to EcsLambda but for Docker images:
- Fast `pulumi up` (async by default)
- Simple architecture: S3 → CodePipeline → CodeBuild → ECR → Lambda
- Maintains Pulumi state
- Supports multi-stage builds with layer caching
"""

import hashlib
import json
import os
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import ComponentResource, Output, ResourceOptions
from utils import _find_project_root

PROJECT_DIR = _find_project_root()


class CodeBuildDockerImage(ComponentResource):
    """AWS CodeBuild-based Docker image builder with ECR push and Lambda update.

    This component:
    - Creates ECR repository
    - Uploads Dockerfile + build context to S3
    - Runs CodeBuild to build multi-stage Docker image
    - Pushes to ECR with content-based tags
    - Updates Lambda function with new image URI
    - Fast pulumi up (async by default, sync in CI)
    """

    def __init__(
        self,
        name: str,
        *,
        dockerfile_path: str,  # Path to Dockerfile relative to project root
        build_context_path: str,  # Path to build context (usually project root)
        source_paths: Optional[list[str]] = None,  # Specific paths to include in build
        lambda_function_name: Optional[str] = None,  # If provided, updates Lambda
        lambda_config: Optional[Dict[str, Any]] = None,  # Lambda configuration
        build_args: Optional[Dict[str, str]] = None,
        platform: str = "linux/arm64",
        sync_mode: Optional[bool] = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__(f"codebuild-docker:{name}", name, {}, opts)

        self.name = name
        # Keep paths relative - they'll be resolved from workspace root
        self.dockerfile_path = dockerfile_path
        self.build_context_path = build_context_path
        self.source_paths = source_paths or []  # Specific source paths for selective copying
        self.lambda_function_name = lambda_function_name or f"{name}-{pulumi.get_stack()}"
        self.lambda_config = lambda_config or {}
        self.build_args = build_args or {}
        self.platform = platform

        # Configure build mode
        if sync_mode is not None:
            self.sync_mode = sync_mode
        else:
            config = pulumi.Config("docker-build")
            if config.get_bool("sync-mode"):
                self.sync_mode = True
            elif os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
                self.sync_mode = True
            else:
                self.sync_mode = False

        # Additional config
        config = pulumi.Config("docker-build")
        self.force_rebuild = config.get_bool("force-rebuild") or False
        self.debug_mode = config.get_bool("debug-mode") or False

        # Calculate content hash for change detection
        content_hash = self._calculate_content_hash()

        if self.sync_mode:
            pulumi.log.info(f"🔄 Building image '{self.name}' in SYNC mode (will wait)")
        else:
            pulumi.log.info(f"⚡ Image '{self.name}' in ASYNC mode (fast pulumi up)")
            pulumi.log.info(f"   📦 Hash: {content_hash[:12]}... - will build only if changed")

        # Create ECR repository
        self.ecr_repo = aws.ecr.Repository(
            f"{self.name}-repo",
            image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
                scan_on_push=True
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Setup build pipeline
        (
            build_bucket,
            upload_cmd,
            pipeline,
            codebuild_project,
        ) = self._setup_pipeline(content_hash)

        # Push bootstrap image and create Lambda function if config provided
        if self.lambda_config:
            bootstrap_cmd = self._push_bootstrap_image()
            self._create_lambda_function(bootstrap_cmd, pipeline)
        else:
            self.lambda_function = None

        # Export outputs
        self.repository_url = self.ecr_repo.repository_url
        self.image_uri = self.ecr_repo.repository_url.apply(
            lambda url: f"{url}:latest"
        )
        # Digest is managed by CodeBuild, provide a placeholder
        # The actual digest is used during Lambda updates in post_build phase
        self.digest = pulumi.Output.from_input("sha256:placeholder")

        # Self-reference for compatibility with components that wrap this
        self.docker_image = self

        self.register_outputs({
            "repository_url": self.repository_url,
            "image_uri": self.image_uri,
            "digest": self.digest,
        })

    def _calculate_content_hash(self) -> str:
        """Calculate hash of Dockerfile and relevant context files."""
        h = hashlib.sha256()

        # Hash Dockerfile
        dockerfile = Path(PROJECT_DIR) / self.dockerfile_path
        if dockerfile.exists():
            with open(dockerfile, "rb") as f:
                h.update(f.read())

        # If source_paths specified, hash only those paths
        if self.source_paths:
            for source_path in sorted(self.source_paths):
                full_path = Path(PROJECT_DIR) / source_path
                if full_path.exists():
                    if full_path.is_file():
                        # Hash single file
                        with open(full_path, "rb") as f:
                            h.update(f.read())
                        h.update(source_path.encode())
                    else:
                        # Hash directory recursively
                        for file_path in sorted(full_path.rglob("*")):
                            if file_path.is_file() and not any(part.startswith('.') or part == '__pycache__' for part in file_path.parts):
                                try:
                                    with open(file_path, "rb") as f:
                                        h.update(f.read())
                                    # Include relative path in hash
                                    rel_path = file_path.relative_to(PROJECT_DIR)
                                    h.update(str(rel_path).encode())
                                except (IOError, OSError):
                                    pass

            # ALWAYS hash the handler directory (Lambda-specific code)
            handler_dir = Path(PROJECT_DIR) / Path(self.dockerfile_path).parent
            if handler_dir.exists():
                for file_path in sorted(handler_dir.rglob("*.py")):
                    if file_path.is_file() and not any(part.startswith('.') or part == '__pycache__' for part in file_path.parts):
                        try:
                            with open(file_path, "rb") as f:
                                h.update(f.read())
                            rel_path = file_path.relative_to(PROJECT_DIR)
                            h.update(str(rel_path).encode())
                        except (IOError, OSError):
                            pass
        else:
            # Hash only the files that will be included in the build context
            if self.build_context_path == ".":
                # Lambda images - hash Python packages and handler directory
                # Default packages that all Lambda images need
                packages_to_hash = [
                    "receipt_dynamo/receipt_dynamo",
                    "receipt_dynamo/pyproject.toml",
                    "receipt_label/receipt_label",
                    "receipt_label/pyproject.toml",
                ]

                # Add source_paths if specified (e.g., receipt_upload)
                if self.source_paths:
                    for source_path in self.source_paths:
                        packages_to_hash.append(source_path)

                for package_path in packages_to_hash:
                    full_path = Path(PROJECT_DIR) / package_path
                    if full_path.exists():
                        if full_path.is_file():
                            try:
                                with open(full_path, "rb") as f:
                                    h.update(f.read())
                                h.update(package_path.encode())
                            except (IOError, OSError):
                                pass
                        else:
                            # Hash directory recursively
                            for file_path in sorted(full_path.rglob("*.py")):
                                if file_path.is_file() and not any(part.startswith('.') or part == '__pycache__' for part in file_path.parts):
                                    try:
                                        with open(file_path, "rb") as f:
                                            h.update(f.read())
                                        rel_path = file_path.relative_to(PROJECT_DIR)
                                        h.update(str(rel_path).encode())
                                    except (IOError, OSError):
                                        pass

                # Also hash the handler directory
                handler_dir = Path(PROJECT_DIR) / Path(self.dockerfile_path).parent
                if handler_dir.exists():
                    for file_path in sorted(handler_dir.rglob("*.py")):
                        if file_path.is_file() and not any(part.startswith('.') or part == '__pycache__' for part in file_path.parts):
                            try:
                                with open(file_path, "rb") as f:
                                    h.update(f.read())
                                rel_path = file_path.relative_to(PROJECT_DIR)
                                h.update(str(rel_path).encode())
                            except (IOError, OSError):
                                pass
            else:
                # ECS images - hash the specific context directory
                context_path = Path(PROJECT_DIR) / self.build_context_path
                if context_path.exists():
                    for pattern in ["**/*.py", "**/requirements.txt", "**/pyproject.toml"]:
                        for file_path in context_path.glob(pattern):
                            if file_path.is_file() and not any(part.startswith('.') or part == '__pycache__' for part in file_path.parts):
                                try:
                                    with open(file_path, "rb") as f:
                                        h.update(f.read())
                                    rel_path = file_path.relative_to(context_path)
                                    h.update(str(rel_path).encode())
                                except (IOError, OSError):
                                    pass

        return h.hexdigest()

    def _generate_upload_script(self, bucket: str, content_hash: str) -> str:
        """Generate script to upload build context to S3."""
        safe_bucket = shlex.quote(bucket)
        safe_context = shlex.quote(str(self.build_context_path))
        safe_dockerfile = shlex.quote(str(self.dockerfile_path))

        # Build source paths string for script
        source_paths_str = ""
        if self.source_paths:
            # Convert paths to space-separated string
            paths = " ".join(shlex.quote(p) for p in self.source_paths)
            source_paths_str = paths

        # Paths are relative to project root
        # Get absolute project root path
        project_root_abs = str(Path(PROJECT_DIR).resolve())
        safe_project_root = shlex.quote(project_root_abs)
        safe_context = shlex.quote(self.build_context_path)
        safe_dockerfile = shlex.quote(self.dockerfile_path)

        return f"""#!/usr/bin/env bash
set -e

# Change to project root
cd {safe_project_root}

BUCKET={safe_bucket}
CONTEXT_PATH={safe_context}
DOCKERFILE={safe_dockerfile}
HASH="{content_hash}"
NAME="{self.name}"
FORCE_REBUILD="{self.force_rebuild}"
SOURCE_PATHS="{source_paths_str}"

echo "📦 Checking if context upload needed for image '$NAME'..."
STORED_HASH=$(aws s3 cp "s3://$BUCKET/$NAME/hash.txt" - 2>/dev/null || echo '')
if [ "$STORED_HASH" = "$HASH" ] && [ "$FORCE_REBUILD" != "True" ]; then
  echo "✅ Context up-to-date. Skipping upload."
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "📦 Preparing build context..."
mkdir -p "$TMP/context"

# Copy only required files using include patterns
echo "📦 Copying minimal context with include patterns..."

if [ "$CONTEXT_PATH" = "." ]; then
  # Lambda images - need packages from monorepo root
  # Default packages that all Lambda images need
  echo "  → Including receipt_dynamo and receipt_label packages..."
  rsync -a \
    --include='receipt_dynamo/' \
    --include='receipt_dynamo/pyproject.toml' \
    --include='receipt_dynamo/receipt_dynamo/' \
    --include='receipt_dynamo/receipt_dynamo/**' \
    --include='receipt_dynamo/docs/' \
    --include='receipt_dynamo/docs/README.md' \
    --include='receipt_label/' \
    --include='receipt_label/pyproject.toml' \
    --include='receipt_label/receipt_label/' \
    --include='receipt_label/receipt_label/**' \
    --include='receipt_label/README.md' \
    --include='receipt_label/LICENSE' \
    --exclude='*' \
    "$CONTEXT_PATH/" "$TMP/context/"

  # Copy additional source paths if specified (e.g., receipt_upload)
  if [ -n "$SOURCE_PATHS" ]; then
    echo "  → Including additional source paths: $SOURCE_PATHS"
    for SOURCE_PATH in $SOURCE_PATHS; do
      if [ -d "$SOURCE_PATH" ]; then
        rsync -a \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          --exclude='.git' \
          "$SOURCE_PATH/" "$TMP/context/$SOURCE_PATH/"
      fi
    done
  fi

  # Also copy the specific infra directory for this image
  # Extract the infra path from DOCKERFILE variable
  INFRA_DIR=$(dirname "$DOCKERFILE")
  if [ -d "$INFRA_DIR" ]; then
    echo "  → Including handler directory: $INFRA_DIR"
    # Create parent directories before rsync (GNU rsync requires this on Linux)
    mkdir -p "$TMP/context/$INFRA_DIR"
    rsync -a \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      "$INFRA_DIR/" "$TMP/context/$INFRA_DIR/"
  fi
else
  # ECS images - context path is already specific directory
  echo "  → Copying ECS context from $CONTEXT_PATH"
  rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "$CONTEXT_PATH/" "$TMP/context/"
fi

# Copy Dockerfile to root of context if not already present (for non-source_paths mode)
if [ ! -f "$TMP/context/Dockerfile" ]; then
  cp "$DOCKERFILE" "$TMP/context/Dockerfile"
  echo "  ✓ Copied Dockerfile to context root"
fi

cd "$TMP"
echo "📦 Creating context archive..."
zip -qr context.zip context
CONTEXT_SIZE=$(du -h context.zip | cut -f1)
echo "  Context size: $CONTEXT_SIZE"
cd - >/dev/null

echo "📤 Uploading to S3..."
aws s3 cp "$TMP/context.zip" "s3://$BUCKET/$NAME/context.zip" --no-progress
echo -n "$HASH" | aws s3 cp - "s3://$BUCKET/$NAME/hash.txt"
HASH_SHORT=$(echo "$HASH" | cut -c1-12)
echo "✅ Uploaded context.zip (hash: $HASH_SHORT..., size: $CONTEXT_SIZE)"
"""

    def _buildspec(self) -> Dict[str, Any]:
        """Generate CodeBuild buildspec for Docker build and push."""

        build_args_str = " ".join([
            f"--build-arg {k}={v}" for k, v in self.build_args.items()
        ])

        platform_flag = f"--platform {self.platform}" if self.platform else ""

        return {
            "version": 0.2,
            "phases": {
                "pre_build": {
                    "commands": [
                        "echo Logging in to Amazon ECR...",
                        "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY",
                        "echo Listing current directory...",
                        "ls -la",
                        "echo Entering context directory...",
                        "cd context",
                        "ls -la",
                    ]
                },
                "build": {
                    "commands": [
                        "echo Build started on `date`",
                        "echo Building Docker image with multi-stage caching...",
                        # Build with BuildKit for better caching
                        "export DOCKER_BUILDKIT=1",
                        # Use timestamp as cache buster to force handler layer rebuild
                        "export CACHE_BUST=$(date +%s)",
                        f"docker build {platform_flag} {build_args_str} "
                        f"--cache-from $ECR_REGISTRY/$REPOSITORY_NAME:cache "
                        f"--build-arg BUILDKIT_INLINE_CACHE=1 "
                        f"--build-arg CACHE_BUST=$CACHE_BUST "
                        f"-t $ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG "
                        f"-t $ECR_REGISTRY/$REPOSITORY_NAME:latest "
                        f"-f Dockerfile .",
                        "echo Build completed on `date`",
                    ]
                },
                "post_build": {
                    "commands": [
                        "echo Pushing Docker image to ECR...",
                        "docker push $ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG",
                        "docker push $ECR_REGISTRY/$REPOSITORY_NAME:latest",
                        # Also push cache layer
                        "docker tag $ECR_REGISTRY/$REPOSITORY_NAME:latest $ECR_REGISTRY/$REPOSITORY_NAME:cache",
                        "docker push $ECR_REGISTRY/$REPOSITORY_NAME:cache || true",
                        "echo Getting image digest...",
                        "IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' $ECR_REGISTRY/$REPOSITORY_NAME:latest | cut -d'@' -f2)",
                        "IMAGE_URI=$ECR_REGISTRY/$REPOSITORY_NAME@$IMAGE_DIGEST",
                        "echo Image URI: $IMAGE_URI",
                        # Create or update Lambda function if specified
                        # Note: Lambda service role must have ECR permissions for this to work
                        'if [ -n "$LAMBDA_FUNCTION_NAME" ]; then echo "Checking if Lambda function $LAMBDA_FUNCTION_NAME exists..." && if aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" >/dev/null 2>&1; then echo "Updating existing Lambda function..." && aws lambda update-function-code --function-name "$LAMBDA_FUNCTION_NAME" --image-uri "$IMAGE_URI" >/dev/null && echo "✅ Lambda function updated"; else echo "Lambda function does not exist - will be created by Pulumi"; fi; fi',
                        "echo Push completed on `date`",
                    ]
                },
            },
        }

    def _setup_pipeline(self, content_hash: str):
        """Setup S3, CodeBuild, and CodePipeline for Docker builds."""

        # Artifact bucket
        build_bucket = aws.s3.Bucket(
            f"{self.name}-artifacts",
            force_destroy=True,
            opts=ResourceOptions(parent=self),
        )

        # Enable versioning for CodePipeline
        bucket_versioning = aws.s3.BucketVersioning(
            f"{self.name}-versioning",
            bucket=build_bucket.id,
            versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Upload context command
        upload_cmd = command.local.Command(
            f"{self.name}-upload-context",
            create=build_bucket.bucket.apply(
                lambda b: self._generate_upload_script(b, content_hash)
            ),
            update=build_bucket.bucket.apply(
                lambda b: self._generate_upload_script(b, content_hash)
            ),
            triggers=[content_hash],
            opts=ResourceOptions(parent=self, delete_before_replace=True),
        )

        # IAM role for CodeBuild
        # Shorten role name to avoid AWS 64-char limit
        codebuild_role = aws.iam.Role(
            f"{self.name}-cb-role",
            assume_role_policy=json.dumps({
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
            }),
            opts=ResourceOptions(parent=self),
        )

        # CodeBuild policy
        aws.iam.RolePolicy(
            f"{self.name}-cb-policy",
            role=codebuild_role.id,
            policy=Output.all(
                build_bucket.arn,
                self.ecr_repo.arn,
                self.ecr_repo.repository_url,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                        ],
                        "Resource": [
                            f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*",
                        ],
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
                        "Action": [
                            "lambda:UpdateFunctionCode",
                            "lambda:GetFunction",
                            "lambda:GetFunctionConfiguration",
                        ],
                        "Resource": f"arn:aws:lambda:{aws.config.region}:{aws.get_caller_identity().account_id}:function:{self.lambda_function_name}" if self.lambda_config else "*",
                    },
                ],
            })),
            opts=ResourceOptions(parent=self),
        )

        # Create CloudWatch log group with retention to control costs
        # Include stack name to avoid collisions between dev/prod
        stack = pulumi.get_stack()
        log_group = aws.cloudwatch.LogGroup(
            f"{self.name}-builder-logs",
            name=f"/aws/codebuild/{self.name}-{stack}-builder",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # CodeBuild project with Docker support
        codebuild_project = aws.codebuild.Project(
            f"{self.name}-builder",
            service_role=codebuild_role.arn,
            source=aws.codebuild.ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(self._buildspec()),
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="CODEPIPELINE"),
            environment=aws.codebuild.ProjectEnvironmentArgs(
                type="ARM_CONTAINER" if "arm" in self.platform else "LINUX_CONTAINER",
                compute_type="BUILD_GENERAL1_LARGE",  # Need large for Docker
                image="aws/codebuild/amazonlinux-aarch64-standard:3.0" if "arm" in self.platform else "aws/codebuild/standard:7.0",
                privileged_mode=True,  # Required for Docker builds
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="ECR_REGISTRY",
                        value=self.ecr_repo.repository_url.apply(lambda url: url.split("/")[0]),
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="REPOSITORY_NAME",
                        value=self.ecr_repo.name,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="IMAGE_TAG",
                        value=content_hash[:12],
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="LAMBDA_FUNCTION_NAME",
                        value=self.lambda_function_name if self.lambda_config else "",
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="DEBUG_MODE",
                        value=str(self.debug_mode),
                    ),
                ],
            ),
            build_timeout=60,  # Docker builds can take time
            cache=aws.codebuild.ProjectCacheArgs(
                type="S3",
                location=Output.concat(build_bucket.bucket, "/cache/", self.name),
            ),
            logs_config=aws.codebuild.ProjectLogsConfigArgs(
                cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                    status="ENABLED",
                ),
            ),
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # Pipeline role
        pipeline_role = aws.iam.Role(
            f"{self.name}-pl-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codepipeline.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        # Pipeline policies
        aws.iam.RolePolicy(
            f"{self.name}-pl-s3",
            role=pipeline_role.id,
            policy=build_bucket.arn.apply(lambda arn: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:*"],
                        "Resource": [arn, f"{arn}/*"],
                    },
                ],
            })),
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicy(
            f"{self.name}-pl-cb",
            role=pipeline_role.id,
            policy=codebuild_project.arn.apply(lambda arn: json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "codebuild:StartBuild",
                        "codebuild:BatchGetBuilds",
                    ],
                    "Resource": arn,
                }],
            })),
            opts=ResourceOptions(parent=self),
        )

        # ECR permissions for CodePipeline to access ECR images
        aws.iam.RolePolicy(
            f"{self.name}-pl-ecr",
            role=pipeline_role.id,
            policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "ecr:GetAuthorizationToken",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                    ],
                    "Resource": "*",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        # CodePipeline
        pipeline = aws.codepipeline.Pipeline(
            f"{self.name}-pipeline",
            role_arn=pipeline_role.arn,
            artifact_stores=[
                aws.codepipeline.PipelineArtifactStoreArgs(
                    type="S3",
                    location=build_bucket.bucket,
                )
            ],
            stages=[
                aws.codepipeline.PipelineStageArgs(
                    name="Source",
                    actions=[
                        aws.codepipeline.PipelineStageActionArgs(
                            name="Source",
                            category="Source",
                            owner="AWS",
                            provider="S3",
                            version="1",
                            output_artifacts=["SourceArtifact"],
                            configuration={
                                "S3Bucket": build_bucket.bucket,
                                "S3ObjectKey": f"{self.name}/context.zip",
                            },
                            run_order=1,
                        )
                    ],
                ),
                aws.codepipeline.PipelineStageArgs(
                    name="BuildAndPush",
                    actions=[
                        aws.codepipeline.PipelineStageActionArgs(
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
            opts=ResourceOptions(parent=self, depends_on=[bucket_versioning]),
        )

        # Trigger pipeline (async or sync based on mode)
        if not self.sync_mode:
            # Async: trigger and continue
            trigger_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Triggering Docker build pipeline for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} --query pipelineExecutionId --output text)
echo "✅ Pipeline triggered: $EXEC_ID"
echo "   View logs: https://console.aws.amazon.com/codesuite/codepipeline/pipelines/{pn}/view"
"""
            )
            command.local.Command(
                f"{self.name}-trigger-pipeline",
                create=trigger_script,
                update=trigger_script,
                triggers=[content_hash],
                opts=ResourceOptions(parent=self, depends_on=[upload_cmd, pipeline]),
            )
        else:
            # Sync: wait for completion
            sync_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 SYNC: Starting Docker build pipeline for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} --query pipelineExecutionId --output text)
echo "Execution ID: $EXEC_ID"
sleep 5
while true; do
  STATUS=$(aws codepipeline get-pipeline-execution --pipeline-name {pn} --pipeline-execution-id $EXEC_ID --query "pipelineExecution.status" --output text)
  echo "🔄 Pipeline status: $STATUS"
  if [ "$STATUS" = "Succeeded" ]; then
    echo "✅ Docker build completed successfully"
    break
  elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Superseded" ]; then
    echo "❌ Pipeline failed with status: $STATUS"
    exit 1
  fi
  sleep 15
done
"""
            )
            command.local.Command(
                f"{self.name}-sync-pipeline",
                create=sync_script,
                update=sync_script,
                triggers=[content_hash],
                opts=ResourceOptions(parent=self, depends_on=[upload_cmd, pipeline]),
            )

        return build_bucket, upload_cmd, pipeline, codebuild_project

    def _push_bootstrap_image(self):
        """Push a minimal bootstrap image to ECR so Lambda can be created."""
        bootstrap_script = self.ecr_repo.repository_url.apply(
            lambda repo_url: f"""#!/usr/bin/env bash
set -e

REPO_URL="{repo_url}"
REGION=$(echo "$REPO_URL" | cut -d'.' -f4)

# Check if Docker is available first (fast check)
if ! command -v docker &> /dev/null; then
  echo "⚠️  Docker not found locally. Skipping bootstrap image push."
  echo "   The Lambda function will be created after CodeBuild completes the first build."
  echo "   This is safe - CodeBuild will build and push the image automatically."
  exit 0
fi

# Only check ECR if Docker is available
echo "🔄 Checking if bootstrap image exists in ECR..."
if aws ecr describe-images --repository-name $(echo "$REPO_URL" | cut -d'/' -f2) --region $REGION --image-ids imageTag=latest >/dev/null 2>&1; then
  echo "✅ Bootstrap image already exists, skipping"
  exit 0
fi

echo "📦 Pushing minimal bootstrap image to ECR..."
# Pull public Lambda base image
docker pull public.ecr.aws/lambda/python:3.12-arm64

# Tag it for our ECR repo
docker tag public.ecr.aws/lambda/python:3.12-arm64 "$REPO_URL:latest"

# Login to ECR
# Note: On macOS, credential helper may fail but login still succeeds
# Check for "Login Succeeded" in output instead of relying on exit code
LOGIN_OUTPUT=$(aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$REPO_URL" 2>&1)
echo "$LOGIN_OUTPUT"
if echo "$LOGIN_OUTPUT" | grep -q "Login Succeeded"; then
  echo "✅ ECR login successful"
else
  echo "❌ ECR login failed"
  exit 1
fi

# Push to our ECR
docker push "$REPO_URL:latest"

echo "✅ Bootstrap image pushed to $REPO_URL:latest"
"""
        )

        return command.local.Command(
            f"{self.name}-bootstrap-image",
            create=bootstrap_script,
            # Don't fail if Docker isn't available - bootstrap is optional
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.ecr_repo],
                # Allow the command to exit successfully even if Docker isn't available
            ),
        )

    def _create_lambda_function(self, bootstrap_cmd, pipeline):
        """Create Lambda function that will be updated by CodeBuild."""

        # Use our ECR repo with :latest tag as initial image
        # CodeBuild will update this once it builds and pushes the real image
        initial_image_uri = self.ecr_repo.repository_url.apply(
            lambda url: f"{url}:latest"
        )

        # Build Lambda function arguments
        lambda_args = {
            "name": self.lambda_function_name,
            "package_type": "Image",
            "image_uri": initial_image_uri,
            "role": self.lambda_config.get("role_arn"),
            "timeout": self.lambda_config.get("timeout", 30),
            "memory_size": self.lambda_config.get("memory_size", 512),
            "architectures": ["arm64"],
        }

        # Add environment if provided
        if self.lambda_config.get("environment"):
            lambda_args["environment"] = aws.lambda_.FunctionEnvironmentArgs(
                variables=self.lambda_config.get("environment")
            )

        # Add ephemeral storage if provided
        if self.lambda_config.get("ephemeral_storage"):
            lambda_args["ephemeral_storage"] = aws.lambda_.FunctionEphemeralStorageArgs(
                size=self.lambda_config.get("ephemeral_storage")
            )

        # Add reserved concurrent executions if provided
        if self.lambda_config.get("reserved_concurrent_executions"):
            lambda_args["reserved_concurrent_executions"] = self.lambda_config.get("reserved_concurrent_executions")

        # Add description if provided
        if self.lambda_config.get("description"):
            lambda_args["description"] = self.lambda_config.get("description")

        # Add tags if provided
        if self.lambda_config.get("tags"):
            lambda_args["tags"] = self.lambda_config.get("tags")

        # Add VPC config if provided
        if self.lambda_config.get("vpc_config"):
            vpc_cfg = self.lambda_config.get("vpc_config")
            lambda_args["vpc_config"] = aws.lambda_.FunctionVpcConfigArgs(
                subnet_ids=vpc_cfg.get("subnet_ids"),
                security_group_ids=vpc_cfg.get("security_group_ids"),
            )

        # Add file system config if provided
        if self.lambda_config.get("file_system_config"):
            fs_cfg = self.lambda_config.get("file_system_config")
            lambda_args["file_system_config"] = aws.lambda_.FunctionFileSystemConfigArgs(
                arn=fs_cfg.get("arn"),
                local_mount_path=fs_cfg.get("local_mount_path"),
            )

        # Create Lambda function after bootstrap image is pushed
        depends_on_list = [bootstrap_cmd] if bootstrap_cmd else []

        self.lambda_function = aws.lambda_.Function(
            f"{self.name}-function",
            **lambda_args,
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["image_uri", "image_config"],  # CodeBuild updates these
                depends_on=depends_on_list,
            ),
        )

        self.function_arn = self.lambda_function.arn
        self.function_name = self.lambda_function.name


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

# mypy: ignore-errors

# pylint: disable=import-error

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
    ProjectCacheArgs,
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
from pulumi_aws.iam import Role as ROLE
from pulumi_aws.iam import RolePolicy
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
    FunctionFileSystemConfigArgs,
    FunctionImageConfigArgs,
    FunctionVpcConfigArgs,
)

from infra.shared.build_utils import (
    compute_hash,
    make_artifact_bucket,
    make_log_group,
    resolve_build_config,
)
from infra.shared.buildspecs import docker_image_buildspec
from infra.utils import _find_project_root

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
        source_paths: Optional[
            list[str]
        ] = None,  # Specific paths to include in build
        extra_context_paths: Optional[
            list[str]
        ] = None,  # Literal dirs/files copied verbatim into the context
        lambda_function_name: Optional[
            str
        ] = None,  # If provided, updates Lambda
        lambda_function_names: Optional[
            list[str]
        ] = None,  # Existing Lambdas to update from one shared image build
        lambda_config: Optional[Dict[str, Any]] = None,  # Lambda configuration
        build_args: Optional[Dict[str, str]] = None,
        platform: str = "linux/arm64",
        sync_mode: Optional[bool] = None,
        lambda_aliases: Optional[
            list[str]
        ] = None,  # Pulumi aliases for Lambda rename
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__(f"codebuild-docker:{name}", name, {}, opts)

        self.name = name
        # Keep paths relative - they'll be resolved from workspace root
        self.dockerfile_path = dockerfile_path
        self.build_context_path = build_context_path
        self.source_paths = (
            source_paths or []
        )  # Specific source paths for selective copying
        # Literal paths (relative to project root) copied verbatim into the
        # build context, preserving their relative layout. Unlike source_paths
        # these are NOT treated as monorepo packages, so nested dirs such as
        # ``tools/glyph-studio/py`` are supported. Only used with a "."
        # build context.
        self.extra_context_paths = extra_context_paths or []
        self.lambda_function_name = (
            lambda_function_name or f"{name}-{pulumi.get_stack()}"
        )
        self.lambda_config = lambda_config or {}
        self.lambda_function_names = list(lambda_function_names or [])
        if (
            self.lambda_config
            and self.lambda_function_name not in self.lambda_function_names
        ):
            self.lambda_function_names.append(self.lambda_function_name)
        self.build_args = build_args or {}
        self.platform = platform
        self.lambda_aliases = (
            lambda_aliases or []
        )  # Pulumi aliases for Lambda rename

        # Configure build mode and flags
        (
            self.sync_mode,
            self.force_rebuild,
            self.debug_mode,
        ) = resolve_build_config(
            "docker-build",
            sync_override=sync_mode,
            ci_default_sync=True,
        )

        # Calculate content hash for change detection
        content_hash = self._calculate_content_hash()

        if self.sync_mode:
            pulumi.log.info(
                f"🔄 Building image '{self.name}' in SYNC mode (will wait)"
            )
        else:
            pulumi.log.info(
                f"⚡ Image '{self.name}' in ASYNC mode (fast pulumi up)"
            )
            pulumi.log.info(
                f"   📦 Hash: {content_hash[:12]}... - will build only if changed"
            )

        # Create ECR repository
        self.ecr_repo = Repository(
            f"{self.name}-repo",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Add ECR repository policy to allow Lambda to pull images
        if self.lambda_config:
            account_id = get_caller_identity().account_id
            RepositoryPolicy(
                f"{self.name}-repo-policy",
                repository=self.ecr_repo.name,
                policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "LambdaECRImageRetrievalPolicy",
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": [
                                    "ecr:BatchGetImage",
                                    "ecr:GetDownloadUrlForLayer",
                                ],
                                "Condition": {
                                    "StringEquals": {
                                        "aws:SourceAccount": account_id
                                    }
                                },
                            }
                        ],
                    }
                ),
                opts=ResourceOptions(parent=self, depends_on=[self.ecr_repo]),
            )

        # Setup build pipeline
        (
            _build_bucket,
            _upload_cmd,
            self.pipeline,
            _codebuild_project,
            pipeline_trigger_cmd,
        ) = self._setup_pipeline(content_hash)

        # A shared image can update several Lambda functions without owning
        # their Pulumi resources. It still needs a bootstrap ``latest`` tag so
        # those functions can be created before the asynchronous build ends.
        bootstrap_cmd = None
        if self.lambda_config or self.lambda_function_names:
            bootstrap_cmd = self._push_bootstrap_image()

        # Create a Lambda directly only for the legacy single-function mode.
        if self.lambda_config:
            self._create_lambda_function(bootstrap_cmd, pipeline_trigger_cmd)
        else:
            self.lambda_function = None

        # Consumers that create Lambda resources from this image must wait for
        # both the bootstrap image and the build trigger.  In async mode the
        # trigger returns before CodeBuild pushes ``latest``, so depending on
        # it alone can race a fresh ECR repository.
        self.lambda_ready_dependencies = [
            dependency
            for dependency in (bootstrap_cmd, pipeline_trigger_cmd)
            if dependency is not None
        ] or [self.pipeline]

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

        self.register_outputs(
            {
                "repository_url": self.repository_url,
                "image_uri": self.image_uri,
                "digest": self.digest,
            }
        )

    def _validate_source_path(self, path: str) -> bool:
        """Validate source path contains only safe characters.

        Args:
            path: Source path to validate

        Returns:
            True if path is safe, False otherwise
        """
        import re

        return bool(re.match(r"^[a-zA-Z0-9_/-]+$", path))

    def _generate_package_rsync_patterns(self, packages: list[str]) -> str:
        """Generate rsync include/exclude patterns for Python packages.

        Args:
            packages: List of package names (e.g., ['receipt_dynamo', 'receipt_agent'])

        Returns:
            Bash script snippet with rsync command and patterns

        Raises:
            ValueError: If any package name contains unsafe characters
        """
        # Validate all packages for shell safety
        for pkg in packages:
            if not self._validate_source_path(pkg):
                raise ValueError(
                    f"Invalid source path '{pkg}': must contain only alphanumeric, "
                    f"underscore, hyphen, and forward slash characters"
                )

        includes = []
        excludes = []

        for pkg in packages:
            # Include package directory and essential files
            includes.extend(
                [
                    f"--include='{pkg}/'",
                    f"--include='{pkg}/pyproject.toml'",
                    f"--include='{pkg}/README.md'",
                    f"--include='{pkg}/LICENSE'",
                    f"--include='{pkg}/docs/'",
                    f"--include='{pkg}/docs/**'",
                    f"--include='{pkg}/{pkg}/'",
                    f"--include='{pkg}/{pkg}/**'",
                ]
            )

            # Exclude test/cache/build artifacts
            excludes.extend(
                [
                    f"--exclude='{pkg}/__pycache__/'",
                    f"--exclude='{pkg}/**/__pycache__/'",
                    f"--exclude='{pkg}/tests/'",
                    f"--exclude='{pkg}/tests/**'",
                    f"--exclude='{pkg}/venv/'",
                    f"--exclude='{pkg}/venv/**'",
                    f"--exclude='{pkg}/.venv/'",
                    f"--exclude='{pkg}/.venv/**'",
                    f"--exclude='{pkg}/htmlcov/'",
                    f"--exclude='{pkg}/htmlcov/**'",
                    f"--exclude='{pkg}/*.egg-info/'",
                    f"--exclude='{pkg}/*.egg-info/**'",
                    f"--exclude='{pkg}/conftest.py'",
                    f"--exclude='{pkg}/coverage.json'",
                ]
            )

        # Build single rsync command
        all_patterns = includes + excludes + ["--exclude='*'"]
        patterns_str = " \\\n    ".join(all_patterns)

        return f"""  rsync -a \\
    {patterns_str} \\
    "$CONTEXT_PATH/" "$TMP/context/"
"""

    def _calculate_content_hash(self) -> str:
        """Calculate hash of Dockerfile and relevant context files."""
        paths: list[Path] = []

        # Include Dockerfile
        dockerfile = Path(PROJECT_DIR) / self.dockerfile_path
        if dockerfile.exists():
            paths.append(dockerfile)

        # Extra literal context paths always contribute to the hash.
        for extra in sorted(self.extra_context_paths):
            full_path = Path(PROJECT_DIR) / extra
            if full_path.exists():
                paths.append(full_path)

        # Hash only explicitly declared package paths.  Docker image callers
        # own their build context: unrelated monorepo packages must not
        # invalidate an image simply because its context root is ".".
        if self.source_paths:
            for source_path in sorted(self.source_paths):
                full_path = Path(PROJECT_DIR) / source_path
                if full_path.exists():
                    paths.append(full_path)

            # ALWAYS hash the handler directory (Lambda-specific code)
            handler_dir = Path(PROJECT_DIR) / Path(self.dockerfile_path).parent
            if handler_dir.exists():
                for file_path in sorted(handler_dir.rglob("*.py")):
                    if file_path.is_file() and not any(
                        part.startswith(".") or part == "__pycache__"
                        for part in file_path.parts
                    ):
                        paths.append(file_path)
        else:
            # Hash only the files that will be included in the build context
            if self.build_context_path == ".":
                # Root-context Lambda images always include their handler
                # directory. Package trees are opt-in via source_paths.
                handler_dir = (
                    Path(PROJECT_DIR) / Path(self.dockerfile_path).parent
                )
                if handler_dir.exists():
                    for file_path in sorted(handler_dir.rglob("*.py")):
                        if file_path.is_file() and not any(
                            part.startswith(".") or part == "__pycache__"
                            for part in file_path.parts
                        ):
                            paths.append(file_path)
            else:
                # ECS images - hash the specific context directory
                context_path = Path(PROJECT_DIR) / self.build_context_path
                if context_path.exists():
                    paths.append(context_path)

        # Extra context paths may carry baked non-code assets (e.g. glyph font
        # JSONs: font.json, stylemap.json, glyphs/*.json) that the include_globs
        # below deliberately exclude, so a font-only edit would not rebuild the
        # image. Fold those JSONs in via a sub-hash keyed only when extra paths
        # exist, keeping the digest byte-identical for callers with none.
        extra_strings = None
        if self.extra_context_paths:
            extra_roots = [
                Path(PROJECT_DIR) / extra
                for extra in sorted(self.extra_context_paths)
            ]
            json_hash = compute_hash(
                [p for p in extra_roots if p.exists()],
                include_globs=["**/*.json"],
            )
            extra_strings = {"extra_context_json": json_hash}

        return compute_hash(
            paths,
            include_globs=[
                "**/*.py",
                "**/pyproject.toml",
                "**/requirements.txt",
                "Dockerfile",
            ],
            extra_strings=extra_strings,
        )

    def _generate_upload_script(self, bucket: str, content_hash: str) -> str:
        """Generate script to upload build context to S3."""
        safe_bucket = shlex.quote(bucket)

        # Build source paths string for script
        source_paths_str = ""
        if self.source_paths:
            # Paths are repo-controlled; join directly for shell iteration
            source_paths_str = " ".join(self.source_paths)

        # Build extra literal context paths string (validated for shell safety)
        for extra in self.extra_context_paths:
            if not self._validate_source_path(extra):
                raise ValueError(
                    f"Invalid extra context path '{extra}': must contain only "
                    "alphanumeric, underscore, hyphen, and forward slash "
                    "characters"
                )
        extra_context_paths_str = " ".join(self.extra_context_paths)

        # Package trees are explicit. This keeps an unrelated receipt_chroma
        # edit from rebuilding a Lambda whose Dockerfile never copies it.
        packages_to_include = sorted(set(self.source_paths))

        # Build rsync include patterns for each package
        rsync_includes = self._generate_package_rsync_patterns(
            packages_to_include
        )

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
EXTRA_CONTEXT_PATHS="{extra_context_paths_str}"
PACKAGES_TO_INCLUDE="{' '.join(packages_to_include)}"

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
  echo "  → Including packages: $PACKAGES_TO_INCLUDE"

  # Dynamically generate rsync command with includes for each package
{rsync_includes}

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

  # Copy any extra literal context paths (non-package dirs/files), preserving
  # their relative layout so the Dockerfile can COPY them by repo path.
  for EXTRA in $EXTRA_CONTEXT_PATHS; do
    if [ -d "$EXTRA" ]; then
      echo "  → Including extra context dir: $EXTRA"
      mkdir -p "$TMP/context/$EXTRA"
      rsync -a \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "$EXTRA/" "$TMP/context/$EXTRA/"
    elif [ -f "$EXTRA" ]; then
      echo "  → Including extra context file: $EXTRA"
      mkdir -p "$TMP/context/$(dirname "$EXTRA")"
      cp "$EXTRA" "$TMP/context/$EXTRA"
    fi
  done
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
        return docker_image_buildspec(
            build_args=self.build_args,
            platform=self.platform,
            debug_mode=self.debug_mode,
        )

    def _setup_pipeline(self, content_hash: str):
        """Setup S3, CodeBuild, and CodePipeline for Docker builds."""

        # Artifact bucket
        build_bucket, bucket_versioning, encryption = make_artifact_bucket(
            self.name, parent=self
        )

        # Upload context command - depends on versioning and encryption to ensure bucket is ready
        upload_cmd_deps = [build_bucket]
        if bucket_versioning:
            upload_cmd_deps.append(bucket_versioning)
        if encryption:
            upload_cmd_deps.append(encryption)

        upload_cmd = command.local.Command(
            f"{self.name}-upload-context",
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
        # Shorten role name to avoid AWS 64-char limit
        codebuild_role = ROLE(
            f"{self.name}-cb-role",
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
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "codepipeline.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole",
                        },
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create CloudWatch log group with retention to control costs
        # Include stack name to avoid collisions between dev/prod
        log_group = make_log_group(
            f"{self.name}-builder-logs",
            retention_days=14,
            parent=self,
        )

        # CodeBuild policy
        log_resources = log_group.arn.apply(lambda arn: [arn, f"{arn}:*"])

        RolePolicy(
            f"{self.name}-cb-policy",
            role=codebuild_role.id,
            policy=Output.all(
                build_bucket.arn,
                self.ecr_repo.arn,
                self.ecr_repo.repository_url,
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
                                "Resource": args[3],
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
                                "Resource": [
                                    f"arn:aws:lambda:{config.region}:"
                                    f"{get_caller_identity().account_id}:function:"
                                    f"{function_name}"
                                    for function_name in self.lambda_function_names
                                ]
                                or "*",
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # CodeBuild project with Docker support
        codebuild_project = Project(
            f"{self.name}-builder",
            service_role=codebuild_role.arn,
            source=ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(self._buildspec()),
            ),
            artifacts=ProjectArtifactsArgs(type="CODEPIPELINE"),
            environment=ProjectEnvironmentArgs(
                type=(
                    "ARM_CONTAINER"
                    if "arm" in self.platform
                    else "LINUX_CONTAINER"
                ),
                # Medium keeps Docker support while reducing CodeBuild cost
                compute_type="BUILD_GENERAL1_MEDIUM",
                image=(
                    "aws/codebuild/amazonlinux-aarch64-standard:3.0"
                    if "arm" in self.platform
                    else "aws/codebuild/standard:7.0"
                ),
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
                        name="LAMBDA_FUNCTION_NAME",
                        value=(
                            self.lambda_function_name
                            if self.lambda_config
                            else ""
                        ),
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="LAMBDA_FUNCTION_NAMES",
                        value=" ".join(self.lambda_function_names),
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="DEBUG_MODE",
                        value=str(self.debug_mode),
                    ),
                ],
            ),
            build_timeout=60,  # Docker builds can take time
            cache=ProjectCacheArgs(
                type="S3",
                location=Output.concat(
                    build_bucket.bucket, "/cache/", self.name
                ),
            ),
            logs_config=ProjectLogsConfigArgs(
                cloudwatch_logs=ProjectLogsConfigCloudwatchLogsArgs(
                    status="ENABLED",
                    group_name=log_group.name,
                ),
            ),
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # Pipeline role
        pipeline_role = ROLE(
            f"{self.name}-pl-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "codepipeline.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Pipeline policies
        RolePolicy(
            f"{self.name}-pl-s3",
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
            f"{self.name}-pl-cb",
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

        # Allow CodePipeline to pass the CodeBuild service role
        RolePolicy(
            f"{self.name}-pl-passrole",
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

        # ECR permissions for CodePipeline to access ECR images
        RolePolicy(
            f"{self.name}-pl-ecr",
            role=pipeline_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # CodePipeline
        pipeline = Pipeline(
            f"{self.name}-pipeline",
            role_arn=pipeline_role.arn,
            pipeline_type="V2",
            execution_mode="SUPERSEDED",
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

        # Trigger pipeline (async or sync based on mode)
        if not self.sync_mode:
            # Async: trigger and continue
            trigger_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Triggering Docker build pipeline for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} \
  --query pipelineExecutionId --output text)
echo "✅ Pipeline triggered: $EXEC_ID"
echo "   View logs: https://console.aws.amazon.com/codesuite/codepipeline/pipelines/{pn}/view"
"""
            )
            # Async trigger also depends on versioning and encryption
            trigger_deps = [upload_cmd, pipeline]
            if bucket_versioning:
                trigger_deps.append(bucket_versioning)
            if encryption:
                trigger_deps.append(encryption)

            pipeline_trigger_cmd = command.local.Command(
                f"{self.name}-trigger-pipeline",
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
echo "🔄 SYNC: Starting Docker build pipeline for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} \
  --query pipelineExecutionId --output text)
echo "Execution ID: $EXEC_ID"
sleep 5
while true; do
  STATUS=$(aws codepipeline get-pipeline-execution --pipeline-name {pn} \
    --pipeline-execution-id $EXEC_ID \
    --query "pipelineExecution.status" --output text)
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
            # Sync pipeline command also depends on versioning and encryption
            sync_deps = [upload_cmd, pipeline]
            if bucket_versioning:
                sync_deps.append(bucket_versioning)
            if encryption:
                sync_deps.append(encryption)

            pipeline_trigger_cmd = command.local.Command(
                f"{self.name}-sync-pipeline",
                create=sync_script,
                update=sync_script,
                triggers=[content_hash],
                opts=ResourceOptions(parent=self, depends_on=sync_deps),
            )

        return (
            build_bucket,
            upload_cmd,
            pipeline,
            codebuild_project,
            pipeline_trigger_cmd,
        )

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
if aws ecr describe-images --repository-name $(echo "$REPO_URL" | cut -d'/' -f2) \
  --region $REGION --image-ids imageTag=latest >/dev/null 2>&1; then
  echo "✅ Bootstrap image already exists, skipping"
  exit 0
fi

echo "📦 Pushing minimal bootstrap image to ECR..."
# Pull public Lambda base image (with error handling)
if ! docker pull public.ecr.aws/lambda/python:3.12-arm64; then
  echo "⚠️  Failed to pull base image. Skipping bootstrap image push."
  echo "   The Lambda function will be created after CodeBuild completes the first build."
  exit 0
fi

# Tag it for our ECR repo
docker tag public.ecr.aws/lambda/python:3.12-arm64 "$REPO_URL:latest"

# Login to ECR
# Note: On macOS, credential helper may fail but login still succeeds
# Check for "Login Succeeded" in output instead of relying on exit code
LOGIN_OUTPUT=$(aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin "$REPO_URL" 2>&1)
echo "$LOGIN_OUTPUT"
if echo "$LOGIN_OUTPUT" | grep -q "Login Succeeded"; then
  echo "✅ ECR login successful"
else
  echo "❌ ECR login failed"
  exit 1
fi

# Push to our ECR (with error handling)
if ! docker push "$REPO_URL:latest"; then
  echo "⚠️  Failed to push bootstrap image. Skipping."
  echo "   The Lambda function will be created after CodeBuild completes the first build."
  exit 0
fi

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

    def _create_lambda_function(self, bootstrap_cmd, pipeline_trigger_cmd):
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
            lambda_args["environment"] = FunctionEnvironmentArgs(
                variables=self.lambda_config.get("environment")
            )

        # Add ephemeral storage if provided
        if self.lambda_config.get("ephemeral_storage"):
            lambda_args["ephemeral_storage"] = FunctionEphemeralStorageArgs(
                size=self.lambda_config.get("ephemeral_storage")
            )

        # Add reserved concurrent executions if provided
        if self.lambda_config.get("reserved_concurrent_executions"):
            lambda_args["reserved_concurrent_executions"] = (
                self.lambda_config.get("reserved_concurrent_executions")
            )

        # Add description if provided
        if self.lambda_config.get("description"):
            lambda_args["description"] = self.lambda_config.get("description")

        # Add tags if provided
        if self.lambda_config.get("tags"):
            lambda_args["tags"] = self.lambda_config.get("tags")

        # Add VPC config if provided
        if self.lambda_config.get("vpc_config"):
            vpc_cfg = self.lambda_config.get("vpc_config")
            lambda_args["vpc_config"] = FunctionVpcConfigArgs(
                subnet_ids=vpc_cfg.get("subnet_ids"),
                security_group_ids=vpc_cfg.get("security_group_ids"),
            )

        # Add file system config if provided
        if self.lambda_config.get("file_system_config"):
            fs_cfg = self.lambda_config.get("file_system_config")
            lambda_args["file_system_config"] = FunctionFileSystemConfigArgs(
                arn=fs_cfg.get("arn"),
                local_mount_path=fs_cfg.get("local_mount_path"),
            )

        # Add image config if provided (for container-based Lambda handler)
        if self.lambda_config.get("image_config"):
            img_cfg = self.lambda_config.get("image_config")
            lambda_args["image_config"] = FunctionImageConfigArgs(
                command=img_cfg.get("command"),
                entry_point=img_cfg.get("entry_point"),
                working_directory=img_cfg.get("working_directory"),
            )

        # Create Lambda function after bootstrap image is pushed
        # In sync mode (CI/CD), skip bootstrap dependency and wait for pipeline instead
        # Bootstrap may exit early without pushing if Docker isn't available
        # Always depend on bootstrap_cmd if it exists to ensure ECR repo is ready
        if self.sync_mode:
            depends_on_list = []
            if bootstrap_cmd:
                depends_on_list.append(bootstrap_cmd)
            if pipeline_trigger_cmd:
                depends_on_list.append(pipeline_trigger_cmd)
        else:
            # In async mode: ALWAYS wait for bootstrap_cmd to complete
            depends_on_list = [bootstrap_cmd] if bootstrap_cmd else []

        # Add aliases if provided (for renaming existing Lambda functions)
        lambda_opts = ResourceOptions(
            parent=self,
            ignore_changes=[
                "image_uri",
                "image_config",
            ],  # CodeBuild updates these
            depends_on=depends_on_list,
        )
        if self.lambda_aliases:
            # Pulumi ResourceOptions.aliases accepts strings directly (URNs as strings)
            # No need to wrap in URN() - strings are used as-is
            lambda_opts.aliases = self.lambda_aliases

        self.lambda_function = Function(
            f"{self.name}-function",
            **lambda_args,
            opts=lambda_opts,
        )

        self.function_arn = self.lambda_function.arn
        self.function_name = self.lambda_function.name

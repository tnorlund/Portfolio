"""
lambda_layer.py

A hybrid Lambda Layer component that gives you the best of both worlds:
- Fast `pulumi up` for development (async builds with real ARNs)
- Simple architecture (no Step Functions/SQS complexity)
- Easy debugging and monitoring

Modes:
- development (default): Fast `pulumi up`, builds happen in background
- sync: Wait for builds to complete (useful for CI/CD)
"""

# pylint: disable=import-error

import base64
import glob
import json
import os
import shlex
import tempfile
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_command as command
from pulumi import ComponentResource, Output
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
from pulumi_aws.iam import Role as ROLE
from pulumi_aws.iam import RolePolicy
from pulumi_aws.lambda_ import LayerVersion

from infra.shared.build_utils import (
    compute_hash,
    make_artifact_bucket,
    make_log_group,
    resolve_build_config,
)
from infra.shared.buildspecs import lambda_layer_buildspec
from infra.utils import _find_project_root

PROJECT_DIR = _find_project_root()
# config will be initialized when needed in Pulumi context


class LambdaLayer(ComponentResource):
    """
    A hybrid Lambda Layer component optimized for development speed.

    Features:
    - Fast `pulumi up` (async builds by default)
    - Simple architecture (no Step Functions complexity)
    - Easy debugging with clear status
    - Configurable sync mode for CI/CD
    """

    def __init__(
        self,
        name: str,
        package_dir: str,
        python_versions: List[str],
        description: Optional[str] = None,
        needs_pillow: bool = False,
        sync_mode: Optional[bool] = None,
        package_extras: Optional[str] = None,  # e.g., extras for optional dependencies
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__(f"lambda-layer:{name}", name, {}, opts)

        self.name = name
        self.layer_name = f"{name}-{pulumi.get_stack()}"
        self.package_dir = package_dir

        # Accept either a single version string or a list
        if isinstance(python_versions, str):
            self.python_versions: List[str] = [python_versions]
        else:
            self.python_versions = list(python_versions)

        self.description = (
            description or f"Automatically built Lambda layer for {name}"
        )
        self.needs_pillow = needs_pillow
        self.package_extras = package_extras
        self.opts = opts

        # Determine build mode
        # Priority: parameter > config > CI detection > default (async)
        (
            self.sync_mode,
            self.force_rebuild,
            self.debug_mode,
        ) = resolve_build_config(
            "lambda-layer", sync_override=sync_mode, ci_default_sync=True
        )

        # Validate package directory
        self._validate_package_dir()

        # Calculate package hash for change detection
        package_hash = self._calculate_package_hash()
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        # Show build mode and change detection info
        if self.sync_mode:
            pulumi.log.info(
                f"🔄 Building layer '{self.name}' in SYNC mode (will wait for completion)"
            )
        else:
            pulumi.log.info(
                f"⚡ Layer '{self.name}' in ASYNC mode (fast pulumi up)"
            )
            if self.force_rebuild:
                pulumi.log.info(
                    "   🔨 Force rebuild enabled - will trigger build"
                )
            else:
                pulumi.log.info(
                    f"   📦 Hash: {package_hash[:12]}... - will build only if changed"
                )

        self._setup_fast_build(package_hash, package_path)

    def _validate_package_dir(self) -> None:
        """Validate that the package directory exists and contains the necessary files."""
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        if not os.path.exists(package_path):
            raise ValueError(
                f"Package directory {package_path} does not exist"
            )

        required_files = ["pyproject.toml"]
        missing_files = [
            f
            for f in required_files
            if not os.path.exists(os.path.join(package_path, f))
        ]
        if missing_files:
            raise ValueError(
                f"Package directory {package_path} is missing required files: "
                f"{', '.join(missing_files)}"
            )

        python_files = glob.glob(
            os.path.join(package_path, "**/*.py"), recursive=True
        )
        if not python_files:
            raise ValueError(
                f"Package directory {package_path} contains no Python files"
            )

    def _calculate_package_hash(self) -> str:
        """Calculate a hash of the package contents to detect changes."""
        package_path = os.path.join(PROJECT_DIR, self.package_dir)
        return compute_hash(
            [package_path],
            include_globs=["**/*.py", "pyproject.toml"],
            extra_strings={"package_dir": self.package_dir},
        )

    def _get_local_dependencies(self) -> List[str]:
        """Get list of local package dependencies from pyproject.toml."""
        package_path = os.path.join(PROJECT_DIR, self.package_dir)
        pyproject_path = os.path.join(package_path, "pyproject.toml")

        local_deps = []
        if os.path.exists(pyproject_path):
            try:
                # Try Python 3.11+ built-in tomllib first
                try:
                    import tomllib

                    with open(pyproject_path, "rb") as f:
                        data = tomllib.load(f)
                except ImportError:
                    # Fall back to toml package if available
                    try:
                        import toml

                        with open(pyproject_path, "r") as f:
                            data = toml.load(f)
                    except ImportError:
                        # If neither is available, parse manually for basic dependencies
                        pulumi.log.warn(
                            f"TOML parser not available, using basic parsing for {self.name}"
                        )
                        data = self._parse_pyproject_basic(pyproject_path)

                # Check main dependencies
                deps = data.get("project", {}).get("dependencies", [])

                # Also check optional dependencies if using extras
                if self.package_extras:
                    optional_deps = data.get("project", {}).get(
                        "optional-dependencies", {}
                    )
                    if self.package_extras in optional_deps:
                        deps.extend(optional_deps[self.package_extras])

                # Filter for local packages (receipt-*)
                for dep in deps:
                    # Extract package name from version spec
                    dep_name = (
                        dep.split("[")[0]
                        .split(">")[0]
                        .split("<")[0]
                        .split("=")[0]
                        .strip()
                    )
                    if dep_name.startswith("receipt-"):
                        # Convert package name to directory name (receipt-dynamo -> receipt_dynamo)
                        dir_name = dep_name.replace("-", "_")
                        local_path = os.path.join(PROJECT_DIR, dir_name)
                        if os.path.exists(local_path):
                            local_deps.append(dir_name)
                            pulumi.log.info(
                                f"📦 Found local dependency: {dir_name} for {self.name}"
                            )
            except (OSError, ValueError) as e:
                pulumi.log.warn(f"Could not parse pyproject.toml: {e}")
                raise

        return local_deps

    def _parse_pyproject_basic(self, pyproject_path: str) -> Dict[str, Any]:
        """
        Basic parser for pyproject.toml to extract dependencies when toml module
        is not available.
        """
        dependencies: List[str] = []
        optional_deps: Dict[str, List[str]] = {}
        result: Dict[str, Any] = {
            "project": {
                "dependencies": dependencies,
                "optional-dependencies": optional_deps,
            }
        }

        try:
            with open(pyproject_path, "r") as f:
                lines = f.readlines()

            in_dependencies = False
            in_optional = False
            current_extra = None

            for line in lines:
                line = line.strip()

                # Start of dependencies section
                if line == "dependencies = [":
                    in_dependencies = True
                    continue

                # End of dependencies section
                if in_dependencies and line == "]":
                    in_dependencies = False
                    continue

                # Check for optional dependencies
                if "[project.optional-dependencies]" in line:
                    in_optional = True
                    continue

                # Parse optional dependency sections
                if in_optional and "= [" in line:
                    current_extra = line.split("=")[0].strip()
                    optional_deps[current_extra] = []
                    continue

                # End of optional section
                if in_optional and line == "]":
                    current_extra = None
                    continue

                # Parse dependency lines
                if in_dependencies and line and line != "]":
                    # Remove quotes and commas
                    dep = line.strip(" \",'")
                    if dep and not dep.startswith("#"):
                        dependencies.append(dep)

                # Parse optional dependency lines
                if current_extra and line and line != "]":
                    dep = line.strip(" \",'")
                    if dep and not dep.startswith("#") and current_extra:
                        optional_deps[current_extra].append(dep)

        except (OSError, ValueError) as e:
            pulumi.log.warn(f"Basic parsing failed: {e}")
            raise

        return result

    def _encode_shell_script(self, script_content: str) -> str:
        """Encode a shell script to base64 for use in buildspec to avoid parsing issues."""
        return base64.b64encode(script_content.encode("utf-8")).decode("utf-8")

    def _get_update_functions_script(self) -> str:
        """Generate the shell script for updating Lambda functions."""
        return '''#!/bin/bash
set -e
LAYER_BASE_ARN=$(echo "$NEW_LAYER_ARN" | sed "s/:[^:]*$//")

# Function to update a single Lambda function (for parallel execution)
update_function() {
  local FUNC_NAME="$1"
  local FUNC_ARN="$2"

  echo "Checking function: $FUNC_NAME"
  ENV_TAG=$(aws lambda list-tags --resource "$FUNC_ARN" \
    --query "Tags.environment" --output text 2>/dev/null || echo "None")

  if [ "$ENV_TAG" != "$STACK_NAME" ]; then
    echo "  Skipping $FUNC_NAME (environment: $ENV_TAG)"
    return 0
  fi

  echo "  Function $FUNC_NAME matches environment $STACK_NAME"
  CURRENT_LAYERS=$(aws lambda get-function-configuration --function-name "$FUNC_NAME" \
    --query "Layers[*].Arn" --output text)

  # Quick check: does this function actually use the layer we are updating?
  if [ -z "$CURRENT_LAYERS" ] || [ "$CURRENT_LAYERS" = "None" ]; then
    echo "  Skipping $FUNC_NAME (no layers)"
    return 0
  fi

  # Check if function uses the layer being updated
  USES_LAYER=false
  for LAYER in $CURRENT_LAYERS; do
    LAYER_BASE=$(echo "$LAYER" | sed "s/:[^:]*$//")
    if [ "$LAYER_BASE" = "$LAYER_BASE_ARN" ]; then
      USES_LAYER=true
      break
    fi
  done

  if [ "$USES_LAYER" = "false" ]; then
    echo "  Skipping $FUNC_NAME (does not use this layer)"
    return 0
  fi

  # Build new layer list
  NEW_LAYERS=""
  for LAYER in $CURRENT_LAYERS; do
    LAYER_BASE=$(echo "$LAYER" | sed "s/:[^:]*$//")
    LAYER_NAME=$(echo "$LAYER_BASE" | sed "s/.*://")

    if [ "$LAYER_BASE" = "$LAYER_BASE_ARN" ]; then
      echo "    Replacing old version: $LAYER"
      continue
    fi

    if echo "$LAYER_NAME" | grep -q "\\-$STACK_NAME$"; then
      NEW_LAYERS="$NEW_LAYERS $LAYER"
      echo "    Keeping env-specific layer: $LAYER"
    elif echo "$LAYER_BASE" | grep -q "\\-$STACK_NAME:"; then
      NEW_LAYERS="$NEW_LAYERS $LAYER"
      echo "    Keeping env-specific layer: $LAYER"
    else
      NEW_LAYERS="$NEW_LAYERS $LAYER"
      BASE_LAYER_NAME=$(echo "$LAYER_NAME" | sed "s/\\-[^\\-]*$//")
  echo "    Keeping cross-env layer: $LAYER "
  echo "      (consider migrating to ${BASE_LAYER_NAME}-$STACK_NAME)"
    fi
  done

  NEW_LAYERS="$NEW_LAYERS $NEW_LAYER_ARN"
  NEW_LAYERS=$(echo "$NEW_LAYERS" | xargs)
  echo "  Updating $FUNC_NAME with layers: $NEW_LAYERS"

  if aws lambda update-function-configuration --function-name "$FUNC_NAME" \
    --layers $NEW_LAYERS >/dev/null 2>&1; then
    echo "  ✅ Updated $FUNC_NAME successfully"
  else
    echo "  ❌ Failed to update $FUNC_NAME"
  fi
}

# Export function for parallel execution
export -f update_function
export STACK_NAME LAYER_BASE_ARN NEW_LAYER_ARN

echo "🔍 Finding Lambda functions that use this layer..."

# Get functions and filter in parallel (max 10 concurrent updates)
aws lambda list-functions --query "Functions[*].[FunctionName,FunctionArn]" --output text | \
  grep -v "^None" | \
  xargs -n 2 -P 10 bash -c 'update_function "$@"' _

echo "🎉 Parallel function updates completed!"'''

    def _get_buildspec(self, version: str | None = None) -> Dict[str, Any]:
        """Return a buildspec dict for CodeBuild."""
        return lambda_layer_buildspec(
            version=version,
            python_versions=self.python_versions,
            package_extras=self.package_extras,
            needs_pillow=self.needs_pillow,
            package_name=self.name,
            layer_name=self.layer_name,
            debug_mode=self.debug_mode,
        )

    def _setup_fast_build(self, package_hash: str, package_path: str) -> None:
        """Set up the fast build process with CodePipeline and per-version CodeBuild projects."""

        # Create S3 bucket for artifacts - let Pulumi auto-generate unique name
        build_bucket, _bucket_versioning = make_artifact_bucket(
            self.name, parent=self
        )

        # Upload source command (runs on create and update, triggers on package_hash)
        upload_cmd = command.local.Command(
            f"{self.name}-upload-source",
            create=build_bucket.bucket.apply(
                lambda b: self._create_and_run_upload_script(
                    b, package_path, package_hash
                )
            ),
            update=build_bucket.bucket.apply(
                lambda b: self._create_and_run_upload_script(
                    b, package_path, package_hash
                )
            ),
            triggers=[package_hash],
            opts=pulumi.ResourceOptions(
                parent=self,
                delete_before_replace=True,
            ),
        )

        # Create IAM role for CodeBuild/CodePipeline
        codebuild_role = ROLE(
            f"{self.name}-codebuild-role",
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create log groups for CodeBuild projects
        # Note: We create these early so we can reference them in the IAM policy
        publish_log_group = make_log_group(
            f"{self.name}-publish-logs",
            retention_days=14,
            parent=self,
        )

        # Create CodeBuild policy with permissions for layer publishing and function updates
        RolePolicy(
            f"{self.name}-codebuild-policy",
            role=codebuild_role.id,
            policy=pulumi.Output.all(
                build_bucket.arn,
                self.layer_name,
                publish_log_group.arn
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
                                "Resource": [
                                    (
                                        f"arn:aws:logs:{config.region}:"
                                        f"{get_caller_identity().account_id}:"
                                        "log-group:/aws/codebuild/*"
                                    ),
                                    (
                                        f"arn:aws:logs:{config.region}:"
                                        f"{get_caller_identity().account_id}:"
                                        "log-group:/aws/codebuild/*:*"
                                    ),
                                    # Include the specific publish log group
                                    args[2],  # publish_log_group.arn
                                    f"{args[2]}:*",  # publish_log_group.arn with wildcard for log streams
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
                                "Action": ["lambda:PublishLayerVersion"],
                                "Resource": [
                                    f"arn:aws:lambda:*:*:layer:{args[1]}",
                                    f"arn:aws:lambda:*:*:layer:{args[1]}:*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "lambda:UpdateFunctionConfiguration",
                                    "lambda:ListFunctions",
                                    "lambda:ListTags",
                                    "lambda:GetFunctionConfiguration",
                                    "lambda:GetLayerVersion",
                                ],
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "codebuild:StartBuild",
                                    "codebuild:StartBuildBatch",
                                    "codebuild:BatchGetBuilds",
                                    "codebuild:BatchGetBuildBatches",
                                ],
                                "Resource": (
                                    f"arn:aws:codebuild:{config.region}:"
                                    f"{get_caller_identity().account_id}:project/*"
                                ),
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for CodePipeline
        pipeline_role = ROLE(
            f"{self.name}-pipeline-role",
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Grant CodePipeline read/write access to the S3 artifact bucket
        RolePolicy(
            f"{self.name}-pipeline-s3-policy",
            role=pipeline_role.id,
            policy=pulumi.Output.all(build_bucket.arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # 1) Bucket-level read/list
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:ListBucket",
                                    "s3:GetBucketLocation",
                                    "s3:GetBucketVersioning",
                                    "s3:GetBucketAcl",
                                    "s3:GetBucketPolicy",
                                    "s3:GetBucketPublicAccessBlock",
                                ],
                                "Resource": args[0],
                            },
                            # 2) Read your source objects
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                    "s3:GetObjectAcl",
                                    "s3:GetObjectVersionAcl",
                                    "s3:GetObjectTagging",
                                    "s3:GetObjectVersionTagging",
                                ],
                                "Resource": f"{args[0]}/{self.name}/*",
                            },
                            # 3) Write pipeline artifacts anywhere in the bucket
                            {
                                "Effect": "Allow",
                                "Action": ["s3:PutObject"],
                                "Resource": f"{args[0]}/*",
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Grant CodePipeline permission to invoke CodeBuild projects (including StartBuildBatch)
        RolePolicy(
            f"{self.name}-pipeline-codebuild-policy",
            role=pipeline_role.id,
            policy=Output.all(
                config.region, get_caller_identity().account_id
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "codebuild:StartBuild",
                                    "codebuild:StartBuildBatch",
                                    "codebuild:BatchGetBuilds",
                                    "codebuild:BatchGetBuildBatches",
                                    "codebuild:BatchGetProjects",
                                    "codebuild:ListBuildsForProject",
                                ],
                                "Resource": (
                                    f"arn:aws:codebuild:{args[0]}:{args[1]}:project/*"
                                ),
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create a CodeBuild project for each Python version
        build_projects = {}
        stack_name = pulumi.get_stack()
        for v in self.python_versions:
            project = Project(
                # Pulumi logical name with stack
                f"{self.name}-build-py{v.replace('.', '')}-{stack_name}",
                service_role=codebuild_role.arn,
                source=ProjectSourceArgs(
                    type="S3",
                    # instead of lambda b: f"{self.name}/source.zip", do:
                    location=build_bucket.bucket.apply(
                        lambda b: f"{b}/{self.name}/source.zip"
                    ),
                    buildspec=json.dumps(self._get_buildspec(version=v)),
                ),
                artifacts=ProjectArtifactsArgs(
                    type="S3",
                    location=build_bucket.bucket,
                    path=f"{self.name}/py{v.replace('.', '')}",
                    name="layer.zip",
                    packaging="ZIP",
                    namespace_type="NONE",
                ),
                environment=ProjectEnvironmentArgs(
                    type="ARM_CONTAINER",
                    compute_type="BUILD_GENERAL1_SMALL",
                    image="aws/codebuild/amazonlinux-aarch64-standard:3.0",
                    environment_variables=[
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="PYTHON_VERSION", value=v
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="LAYER_NAME", value=self.layer_name
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="PACKAGE_NAME", value=self.name
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="BUCKET_NAME", value=build_bucket.bucket
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="PACKAGE_DIR", value="source"
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="STACK_NAME", value=pulumi.get_stack()
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="NEEDS_PILLOW", value=str(self.needs_pillow)
                        ),
                        ProjectEnvironmentEnvironmentVariableArgs(
                            name="DEBUG_MODE", value=str(self.debug_mode)
                        ),
                    ],
                ),
                cache=ProjectCacheArgs(
                    type="S3",
                    location=build_bucket.bucket.apply(
                        lambda b: f"{b}/{self.name}/cache"
                    ),
                ),
                logs_config=ProjectLogsConfigArgs(
                    cloudwatch_logs=ProjectLogsConfigCloudwatchLogsArgs(
                        status="ENABLED",
                        # Auto-generate log group name
                    ),
                ),
                opts=pulumi.ResourceOptions(parent=self),
            )
            build_projects[v] = project

        def publish_buildspec() -> Dict[str, Any]:
            # This buildspec merges all version artifacts under a single
            # python/lib/python<ver>/site-packages tree, zips, uploads to S3,
            # and publishes as one layer from S3.
            commands = []
            # Step 1: Prepare merged directory
            commands.append('echo "Preparing merged layer directory..."')
            commands.append("rm -rf merged && mkdir -p merged")
            # Step 2: Merge already-flattened artifacts (build stage already flattened to python/*)
            commands.append(
                'echo "Setting up merged python directory (already flattened)..."'
            )
            commands.append("rm -rf merged/python && mkdir -p merged/python")
            for idx, v in enumerate(self.python_versions):
                commands.append(
                    f'echo "Merging flattened artifacts for Python {v}..."'
                )
                if idx == 0:
                    # Primary artifact in root workspace - already flattened to python/*
                    commands.append("cp -r python/* merged/python/")
                else:
                    # Secondary artifacts under CODEBUILD_SRC_DIR_py<ver> (ver without dots)
                    # These are also already flattened to python/*
                    ver = v.replace(".", "")
                    commands.append(
                        f"cp -r $CODEBUILD_SRC_DIR_py{ver}/python/* merged/python/"
                    )
            # Validate the flattened structure before zipping
            commands.append('echo "Validating flattened structure..."')
            commands.append(
                'if [ -d "merged/python/lib" ]; then '
                'echo "ERROR: Nested lib directory found! Layer should be flattened."; '
                "exit 1; fi"
            )
            commands.append(
                'echo "Structure is correctly flattened (no nested lib directory)"'
            )
            # Step 3: Zip the merged python directory
            commands.append('echo "Zipping merged layer..."')
            commands.append("cd merged && zip -r ../layer.zip python && cd ..")
            # Validate the zip file
            commands.append('echo "Validating layer.zip..."')
            commands.append(
                "[ -f layer.zip ] || { echo 'ERROR: layer.zip not created'; exit 1; }"
            )
            commands.append(
                "ZIP_SIZE=$(stat -c%s layer.zip 2>/dev/null || "
                "stat -f%z layer.zip 2>/dev/null || echo 0)"
            )
            commands.append('echo "Layer zip size: $ZIP_SIZE bytes"')
            commands.append(
                "[ \"$ZIP_SIZE\" -gt 0 ] || { echo 'ERROR: layer.zip is empty'; exit 1; }"
            )
            # Check if zip size exceeds Lambda limits
            commands.append(
                "MAX_SIZE=$((250 * 1024 * 1024))  # 250MB in bytes"
            )
            commands.append(
                'if [ "$ZIP_SIZE" -gt "$MAX_SIZE" ]; then '
                'echo "WARNING: Layer size exceeds Lambda limit (250MB)"; '
                'echo "Size: $(($ZIP_SIZE / 1024 / 1024))MB"; fi'
            )
            # Step 3.1: Upload combined zip to artifact bucket
            commands.append('echo "Uploading merged layer.zip to S3..."')
            commands.append(
                "aws s3 cp layer.zip s3://$BUCKET_NAME/${PACKAGE_NAME}/combined/layer.zip"
            )
            # Step 4: Publish the merged layer from S3
            commands.append(
                'echo "Publishing merged layer from S3 to Lambda..."'
            )
            commands.append(
                "NEW_LAYER_ARN=$(aws lambda publish-layer-version "
                '--layer-name "$LAYER_NAME" '
                '--content S3Bucket="$BUCKET_NAME",S3Key="${PACKAGE_NAME}/combined/layer.zip" '
                "--compatible-runtimes "
                + " ".join([f"python{v}" for v in self.python_versions])
                + " --compatible-architectures arm64 "
                f'--description "{self.description}" '
                '--query "LayerVersionArn" --output text 2>&1) || '
                '{ echo "ERROR: Failed to publish layer version"; '
                'echo "Output: $NEW_LAYER_ARN"; exit 1; }'
            )
            commands.append('echo "New layer ARN: $NEW_LAYER_ARN"')
            commands.append(
                "[ -n \"$NEW_LAYER_ARN\" ] || { echo 'ERROR: No layer ARN returned'; exit 1; }"
            )
            commands.append("export NEW_LAYER_ARN")
            commands.append(
                f'echo "{self._encode_shell_script(self._get_update_functions_script())}" '
                "| base64 -d > update_layers.sh"
            )
            commands.append("chmod +x update_layers.sh")
            commands.append("./update_layers.sh")
            commands.append(
                'echo "All layer versions published and functions updated."'
            )
            return {
                "version": 0.2,
                "phases": {
                    "build": {
                        "commands": commands,
                    }
                },
            }

        # Note: publish_log_group was already created earlier (line 481)
        # and is referenced in the IAM policy. Don't create it again here.

        # Create the publish CodeBuild project
        publish_project = Project(
            f"{self.name}-publish-{stack_name}",  # Pulumi logical name with stack
            service_role=codebuild_role.arn,
            source=ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(publish_buildspec()),
            ),
            artifacts=ProjectArtifactsArgs(type="CODEPIPELINE"),
            environment=ProjectEnvironmentArgs(
                type="ARM_CONTAINER",
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/amazonlinux-aarch64-standard:3.0",
                environment_variables=[
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="LAYER_NAME", value=self.layer_name
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="PACKAGE_NAME", value=self.name
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="BUCKET_NAME", value=build_bucket.bucket
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="STACK_NAME", value=pulumi.get_stack()
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="NEEDS_PILLOW", value=str(self.needs_pillow)
                    ),
                    ProjectEnvironmentEnvironmentVariableArgs(
                        name="DEBUG_MODE", value=str(self.debug_mode)
                    ),
                ],
            ),
            build_timeout=60,
            logs_config=ProjectLogsConfigArgs(
                cloudwatch_logs=ProjectLogsConfigCloudwatchLogsArgs(
                    status="ENABLED",
                    group_name=publish_log_group.name,
                ),
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Define CodePipeline to run all builds in parallel and then publish layer versions
        pipeline = Pipeline(
            f"{self.name}-pipeline-{stack_name}",  # Pulumi logical name with stack
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
                                "S3ObjectKey": f"{self.name}/source.zip",
                            },
                            run_order=1,
                        )
                    ],
                ),
                PipelineStageArgs(
                    name="Build",
                    actions=[
                        PipelineStageActionArgs(
                            name=f"Build_py{v.replace('.', '')}",
                            category="Build",
                            owner="AWS",
                            provider="CodeBuild",
                            version="1",
                            input_artifacts=["SourceArtifact"],
                            output_artifacts=[f"py{v.replace('.', '')}"],
                            run_order=1,
                            configuration={
                                "ProjectName": build_projects[v].name,
                            },
                        )
                        for v in self.python_versions
                    ],
                ),
                PipelineStageArgs(
                    name="Deploy",
                    actions=[
                        PipelineStageActionArgs(
                            name="PublishAndUpdate",
                            category="Build",
                            owner="AWS",
                            provider="CodeBuild",
                            version="1",
                            input_artifacts=[
                                f"py{v.replace('.', '')}"
                                for v in self.python_versions
                            ],
                            run_order=1,
                            configuration={
                                "ProjectName": publish_project.name,
                                "PrimarySource": f"py{self.python_versions[0].replace('.', '')}",
                            },
                        )
                    ],
                ),
            ],
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=(
                    [_bucket_versioning]
                    if _bucket_versioning is not None
                    else None
                ),
            ),
        )

        # Trigger pipeline run when source is updated
        trigger_script = pipeline.name.apply(
            lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Changes detected, starting CodePipeline execution for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} \
  --query pipelineExecutionId --output text)
echo "Triggered pipeline: $EXEC_ID"
"""
        )
        # Only create trigger-pipeline command if NOT in sync mode (to avoid duplicate triggers)
        if not self.sync_mode:
            command.local.Command(
                f"{self.name}-trigger-pipeline",
                create=trigger_script,
                update=trigger_script,
                triggers=[package_hash],
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[upload_cmd, pipeline],
                ),
            )

        # If sync_mode, start the pipeline and wait for it to complete before finishing Pulumi up
        if self.sync_mode:
            sync_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Sync: Starting CodePipeline execution for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} \
  --query pipelineExecutionId --output text)
echo "Execution ID: $EXEC_ID"
sleep 2
while true; do
    STATUS=$(aws codepipeline get-pipeline-execution \\
        --pipeline-name {pn} \\
        --pipeline-execution-id $EXEC_ID \\
        --query "pipelineExecution.status" --output text)
    echo "🔄 Pipeline status: $STATUS"
    if [ "$STATUS" = "Succeeded" ]; then
        echo "✅ Pipeline completed successfully"
        break
    elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Superseded" ]; then
        echo "❌ Pipeline failed with status: $STATUS"
        exit 1
    fi
    sleep 10
done
"""
            )
            sync_cmd = command.local.Command(
                f"{self.name}-sync-pipeline",
                create=sync_script,
                update=sync_script,  # Also run on updates
                triggers=[package_hash],  # Trigger on package changes
                opts=pulumi.ResourceOptions(
                    parent=self, depends_on=[upload_cmd, pipeline]
                ),
            )
            # Ensure Pulumi waits for pipeline before proceeding
            pulumi.log.info(f"Sync command added for pipeline {self.name}")
        else:
            sync_cmd = None

        # In sync mode, create LayerVersion resource and wait for pipeline
        # In async mode, create LayerVersion resource for ARN but let pipeline manage updates
        self.layer_version = LayerVersion(
            f"{self.name}-lambda-layer",
            layer_name=self.layer_name,
            compatible_runtimes=[f"python{v}" for v in self.python_versions],
            compatible_architectures=["x86_64", "arm64"],
            description=self.description,
            s3_bucket=build_bucket.bucket,
            s3_key=f"{self.name}/combined/layer.zip",
            opts=pulumi.ResourceOptions(
                depends_on=(
                    [sync_cmd] if (self.sync_mode and sync_cmd) else [pipeline]
                ),
                parent=self,
                # Let pipeline manage the actual layer content via aws lambda publish-layer-version
                ignore_changes=["s3_key"] if not self.sync_mode else None,
            ),
        )
        self.arn = self.layer_version.arn

    def _create_and_run_upload_script(
        self, bucket: str, package_path: str, package_hash: str
    ) -> str:
        """Create a script file and return just the execution command."""
        try:
            # Generate the script content with embedded variables to avoid argument issues
            script_content = self._generate_upload_script(
                bucket, package_path, package_hash
            )

            # Create a persistent script file in a secure temp location
            fd, script_path = tempfile.mkstemp(
                prefix=f"pulumi-upload-{self.name}-{package_hash[:8]}-",
                suffix=".sh",
            )
            os.close(fd)

            # Write the script file
            with open(script_path, "w") as f:
                f.write(script_content)

            # Restrict permissions to the current user
            os.chmod(script_path, 0o700)

            # Return just the simple command to execute the script
            # No arguments or environment variables in the command line
            return f"/bin/bash {script_path}"
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to create upload script: {e}") from e

    def _generate_upload_script(
        self, bucket: str, package_path: str, package_hash: str
    ) -> str:
        """Generate script to upload source package with safely embedded paths.

        Includes improved error handling and validation.
        """
        # Escape the paths to handle special characters
        safe_package_path = shlex.quote(package_path)
        safe_bucket = shlex.quote(bucket)

        # Get local dependencies that need to be included
        local_deps = self._get_local_dependencies()

        return f"""#!/bin/bash
set -e

# Set variables within the script to avoid command line length issues
BUCKET={safe_bucket}
PACKAGE_PATH={safe_package_path}
HASH="{package_hash}"
LAYER_NAME="{self.name}"
FORCE_REBUILD="{self.force_rebuild}"
LOCAL_DEPS="{' '.join(local_deps)}"

echo "📦 Checking if source upload needed for layer '$LAYER_NAME'..."
if [ -n "$LOCAL_DEPS" ]; then
    echo "📚 Including local dependencies: $LOCAL_DEPS"
fi

# Check if we need to upload
STORED_HASH=$(aws s3 cp "s3://$BUCKET/$LAYER_NAME/hash.txt" - 2>/dev/null || echo '')
if [ "$STORED_HASH" = "$HASH" ] && [ "$FORCE_REBUILD" != "True" ]; then
    HASH_SHORT=$(echo "$HASH" | cut -c1-12)
    echo "✅ Source already up-to-date (hash: $HASH_SHORT...). Skipping upload."
    exit 0
fi

if [ "$STORED_HASH" != "$HASH" ]; then
    echo "📝 Source changes detected, uploading..."
elif [ "$FORCE_REBUILD" = "True" ]; then
    echo "🔨 Force rebuild enabled, re-uploading source..."
fi

# Validate package structure before upload
if [ ! -f "$PACKAGE_PATH/pyproject.toml" ]; then
    echo "❌ Error: pyproject.toml not found in $PACKAGE_PATH"
    echo "Package contents:"
    ls -la "$PACKAGE_PATH" 2>/dev/null || echo "Directory not accessible"
    echo "Current directory: $(pwd)"
    echo "Looking for package in: $PACKAGE_PATH"
    exit 1
fi

# Check if package has Python files
PY_FILES=$(find "$PACKAGE_PATH" -name "*.py" -type f | head -5)
if [ -z "$PY_FILES" ]; then
    echo "⚠️  Warning: No Python files found in package"
else
    echo "✓ Found Python files in package"
fi

# Upload source
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Creating source package structure..."
mkdir -p "$TMP_DIR/source"

# Copy the main package directory structure
cp -r "$PACKAGE_PATH"/* "$TMP_DIR/source/"

# Include local dependencies if present
if [ -n "$LOCAL_DEPS" ]; then
    echo "Including local dependencies in source package..."
    for dep in $LOCAL_DEPS; do
        DEP_PATH="$(dirname "$PACKAGE_PATH")/$dep"
        if [ -d "$DEP_PATH" ]; then
            echo "  - Adding $dep from $DEP_PATH"
            mkdir -p "$TMP_DIR/dependencies/$dep"
            cp -r "$DEP_PATH"/* "$TMP_DIR/dependencies/$dep/"
        else
            echo "  Warning: Local dependency $dep not found at $DEP_PATH"
        fi
    done
fi

# Create zip quietly to reduce output
cd "$TMP_DIR"
if [ -d dependencies ]; then
    zip -qr source.zip source dependencies
else
    zip -qr source.zip source
fi
cd - >/dev/null

echo "Uploading to S3..."
# Add retry logic for S3 operations
for attempt in 1 2 3; do
    if aws s3 cp "$TMP_DIR/source.zip" "s3://$BUCKET/$LAYER_NAME/source.zip"; then
        echo "✓ Source zip uploaded"
        break
    else
        echo "Attempt $attempt failed, retrying..."
        sleep 2
    fi
    if [ $attempt -eq 3 ]; then
        echo "❌ Failed to upload source.zip after 3 attempts"
        exit 1
    fi
done

echo -n "$HASH" | aws s3 cp - "s3://$BUCKET/$LAYER_NAME/hash.txt"

echo "✅ Source uploaded successfully"
"""

    def _generate_trigger_script(
        self, bucket: str, project_name: str, package_hash: str
    ) -> str:
        """Generate script to trigger build without waiting."""
        return f"""#!/bin/bash
set -e

BUCKET="{bucket}"
PROJECT="{project_name}"
HASH="{package_hash}"

echo "🚀 Checking if build needed for layer '{self.name}'..."

# Check if we need to build
STORED_HASH=$(aws s3 cp s3://$BUCKET/{self.name}/hash.txt - 2>/dev/null || echo '')
if [ "$STORED_HASH" = "$HASH" ] && [ "{self.force_rebuild}" != "True" ]; then
    HASH_SHORT=$(echo "$HASH" | cut -c1-12)
    echo "✅ No changes detected (hash: $HASH_SHORT...). Skipping build."
    echo "💡 To force rebuild: pulumi up --config lambda-layer:force-rebuild=true"
    exit 0
fi

if [ "$STORED_HASH" != "$HASH" ]; then
    echo "📝 Code changes detected:"
    STORED_HASH_SHORT=$(echo "$STORED_HASH" | cut -c1-12)
    HASH_SHORT=$(echo "$HASH" | cut -c1-12)
    echo "   Old hash: $STORED_HASH_SHORT..."
    echo "   New hash: $HASH_SHORT..."
fi

if [ "{self.force_rebuild}" = "True" ]; then
    echo "🔨 Force rebuild enabled"
fi

# Check if there's already a build in progress
BUILD_STATUS=$(aws codebuild list-builds-for-project --project-name "$PROJECT" \
  --query 'ids[0]' --output text 2>/dev/null || echo "None")
if [ "$BUILD_STATUS" != "None" ]; then
    CURRENT_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_STATUS" \
      --query 'builds[0].buildStatus' --output text 2>/dev/null || echo "UNKNOWN")
    if [ "$CURRENT_STATUS" = "IN_PROGRESS" ]; then
        echo "⏳ Build already in progress. Skipping new build."
        exit 0
    fi
fi

# Start async build
echo "🏗️  Starting async build..."
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)
echo "✅ Build started: $BUILD_ID"
echo "📊 Monitor at: https://console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT/\\"
echo "build/$BUILD_ID"
echo "⚡ Continuing with fast pulumi up (not waiting for completion)"
"""

    def _generate_initial_build_script(
        self, bucket: str, project_name: str
    ) -> str:
        """Generate script to ensure initial layer exists."""
        return f"""#!/bin/bash
set -e

BUCKET="{bucket}"
PROJECT="{project_name}"

echo "🔍 Checking if initial layer exists for '{self.name}'..."

# Check if layer exists
if aws s3api head-object --bucket "$BUCKET" --key "{self.name}/combined/layer.zip" &>/dev/null; then
    echo "✅ Layer already exists. No initial build needed."
    exit 0
fi

echo "🏗️  No layer found. Running initial build (this will wait for completion)..."

# Start build and wait
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)
echo "Build ID: $BUILD_ID"

while true; do
BUILD_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
  --query 'builds[0].buildStatus' --output text)
    echo "Build status: $BUILD_STATUS"

    if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
        echo "✅ Initial build completed successfully!"
        break
elif [ "$BUILD_STATUS" = "FAILED" ] || [ "$BUILD_STATUS" = "FAULT" ] || \
     [ "$BUILD_STATUS" = "STOPPED" ] || [ "$BUILD_STATUS" = "TIMED_OUT" ]; then
        echo "❌ Initial build failed with status: $BUILD_STATUS"
        exit 1
    fi

    sleep 30
done
"""

    def _generate_sync_script(
        self,
        bucket: str,
        project_name: str,
        layer_name: str,
        package_path: str,
        package_hash: str,
    ) -> str:
        """Generate script for sync mode (waits for completion)."""
        return f"""#!/bin/bash
set -e

BUCKET="{bucket}"
PROJECT="{project_name}"
LAYER_NAME="{layer_name}"
PACKAGE_PATH="{package_path}"
HASH="{package_hash}"

echo "🔄 Building layer '{self.name}' in SYNC mode..."

# Check if we need to rebuild
if [ "$(aws s3 cp s3://$BUCKET/{self.name}/hash.txt - 2>/dev/null || echo '')" \
     = "$HASH" ] && [ "{self.force_rebuild}" != "True" ]; then
    echo "✅ No changes detected. Skipping rebuild."

    # Ensure layer exists
    if ! aws s3api head-object --bucket "$BUCKET" --key "{self.name}/combined/layer.zip" &>/dev/null; then
        echo "❌ Layer missing but hash matches. Please run with force-rebuild."
        exit 1
    fi
    exit 0
fi

# Upload source
echo "📦 Uploading source..."
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/source"
cp -r "$PACKAGE_PATH"/* "$TMP_DIR/source/"

# Use cd instead of pushd/popd for better shell compatibility
cd "$TMP_DIR"
zip -r source.zip source
cd - >/dev/null

aws s3 cp "$TMP_DIR/source.zip" "s3://$BUCKET/{self.name}/source.zip"

# Start build and wait
echo "🏗️  Starting build..."
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)
echo "Build ID: $BUILD_ID"

while true; do
BUILD_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
  --query 'builds[0].buildStatus' --output text)
    echo "Build status: $BUILD_STATUS"

    if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
        echo "✅ Build completed successfully!"
        break
elif [ "$BUILD_STATUS" = "FAILED" ] || [ "$BUILD_STATUS" = "FAULT" ] || \
     [ "$BUILD_STATUS" = "STOPPED" ] || [ "$BUILD_STATUS" = "TIMED_OUT" ]; then
        echo "❌ Build failed with status: $BUILD_STATUS"
        exit 1
    fi

    sleep 30
done

# Save hash
echo -n "$HASH" | aws s3 cp - "s3://$BUCKET/{self.name}/hash.txt"
echo "✅ Layer build process completed!"
"""


# Define the layers to build
layers_to_build = [
    {
        "package_dir": "receipt_dynamo",
        "name": "receipt-dynamo",
        "description": "DynamoDB layer for receipt-dynamo",
        "python_versions": ["3.12"],
        "needs_pillow": False,
    },
    {
        "package_dir": "receipt_dynamo_stream",
        "name": "receipt-dynamo-stream",
        "description": "DynamoDB stream parsing layer",
        "python_versions": ["3.12"],
        "needs_pillow": False,
    },
    {
        "package_dir": "receipt_upload",
        "name": "receipt-upload",
        "description": "Upload layer for receipt-upload",
        "python_versions": ["3.12"],
        "needs_pillow": False,  # Not needed - no image processing in upload lambdas
    },
]

# Create Lambda layers using the fast approach
# TEMPORARILY SKIP LAYER BUILDING
SKIP_LAYER_BUILDING = (
    os.environ.get("PYTEST_RUNNING") == "1" or False
)  # Skip building during tests

# SYNC MODE: Set via Pulumi config: pulumi config set lambda-layer:sync-mode true
# Or pass sync_mode parameter when creating LambdaLayer
# Default is False (async mode) for faster pulumi up
# Use sync mode when ARNs are needed immediately (e.g., first-time creation)
USE_SYNC_MODE = None  # None means read from config instead of hardcoding

# Create Lambda layers using the hybrid approach
lambda_layers = {}

# Only create layers when running in a Pulumi context
try:
    pulumi.get_stack()  # This will throw if not in a Pulumi context
    _in_pulumi_context = not SKIP_LAYER_BUILDING
except pulumi.RunError:
    _in_pulumi_context = False

if _in_pulumi_context:
    for layer_config in layers_to_build:
        lambda_layer = LambdaLayer(
            name=layer_config["name"],  # type: ignore
            package_dir=layer_config["package_dir"],  # type: ignore
            python_versions=layer_config["python_versions"],  # type: ignore
            description=layer_config["description"],  # type: ignore
            needs_pillow=layer_config["needs_pillow"],  # type: ignore
            package_extras=layer_config.get("package_extras"),  # type: ignore
            sync_mode=USE_SYNC_MODE,  # None = read from config, True/False = override
        )
        lambda_layers[layer_config["name"]] = lambda_layer

    # Access the built layers by name
    dynamo_layer = lambda_layers["receipt-dynamo"]
    dynamo_stream_layer = lambda_layers["receipt-dynamo-stream"]
    upload_layer = lambda_layers["receipt-upload"]

    # Export the layer ARNs for reference
    pulumi.export("dynamo_layer_arn", dynamo_layer.arn)
    pulumi.export("dynamo_stream_layer_arn", dynamo_stream_layer.arn)
    pulumi.export("upload_layer_arn", upload_layer.arn)
else:
    # Create dummy objects when skipping or not in Pulumi context
    class DummyLayer:
        def __init__(self, name: str) -> None:
            self.name = name
            self.arn = None

    dynamo_layer = DummyLayer("receipt-dynamo")  # type: ignore
    dynamo_stream_layer = DummyLayer("receipt-dynamo-stream")  # type: ignore
    upload_layer = DummyLayer("receipt-upload")  # type: ignore

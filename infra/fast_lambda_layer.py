#!/usr/bin/env python3
"""
fast_lambda_layer.py

A hybrid Lambda Layer component that gives you the best of both worlds:
- Fast `pulumi up` for development (async builds)
- Simple architecture (no Step Functions/SQS complexity)
- Easy debugging and monitoring

Modes:
- development (default): Fast `pulumi up`, builds happen in background
- sync: Wait for builds to complete (useful for CI/CD)
"""

import base64
import glob
import hashlib
import json
import os
import shlex
import tempfile
from typing import Optional, List, Dict, Any

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import ComponentResource, Output
from utils import _find_project_root

PROJECT_DIR = _find_project_root()
config = pulumi.Config("lambda-layer")


class FastLambdaLayer(ComponentResource):
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
        package_extras: Optional[str] = None,  # e.g., "lambda" for receipt_label[lambda]
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__(f"fast-lambda-layer:{name}", name, {}, opts)

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
        if sync_mode is not None:
            self.sync_mode = sync_mode
        elif config.get_bool("sync-mode"):
            self.sync_mode = True
        elif os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
            self.sync_mode = True  # Use sync mode in CI
        else:
            self.sync_mode = False  # Fast async mode for development

        # Validate package directory
        self._validate_package_dir()

        # Get the force-rebuild config
        self.force_rebuild = config.get_bool("force-rebuild") or False
        
        # Get debug mode config
        self.debug_mode = config.get_bool("debug-mode") or False

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
        hash_obj = hashlib.sha256()
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        files_to_hash = []
        for root, _, files in os.walk(package_path):
            for file in files:
                if file.endswith(".py") or file == "pyproject.toml":
                    files_to_hash.append(os.path.join(root, file))

        for file_path in sorted(files_to_hash):
            with open(file_path, "rb") as f:
                hash_obj.update(f.read())
            rel_path = os.path.relpath(file_path, package_path)
            hash_obj.update(rel_path.encode())

        return hash_obj.hexdigest()

    def _encode_shell_script(self, script_content: str) -> str:
        """Encode a shell script to base64 for use in buildspec to avoid parsing issues."""
        return base64.b64encode(script_content.encode("utf-8")).decode("utf-8")
    
    def _generate_batched_pip_install(self, packages: List[str], target_dir: str, 
                                     python_version: str, batch_size: int = 10) -> List[str]:
        """Generate batched pip install commands to avoid ARG_MAX errors.
        
        Args:
            packages: List of package names to install
            target_dir: Target directory for installation  
            python_version: Python version to use
            batch_size: Number of packages per batch
            
        Returns:
            List of pip install commands
        """
        if not packages:
            return []
            
        commands = []
        for i in range(0, len(packages), batch_size):
            batch = packages[i:i + batch_size]
            pkg_str = " ".join(batch)
            commands.append(
                f"python{python_version} -m pip install --no-cache-dir --no-compile "
                f"{pkg_str} -t {target_dir}"
            )
        return commands

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
  ENV_TAG=$(aws lambda list-tags --resource "$FUNC_ARN" --query "Tags.environment" --output text 2>/dev/null || echo "None")

  if [ "$ENV_TAG" != "$STACK_NAME" ]; then
    echo "  Skipping $FUNC_NAME (environment: $ENV_TAG)"
    return 0
  fi

  echo "  Function $FUNC_NAME matches environment $STACK_NAME"
  CURRENT_LAYERS=$(aws lambda get-function-configuration --function-name "$FUNC_NAME" --query "Layers[*].Arn" --output text)

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
      echo "    Keeping cross-env layer: $LAYER (consider migrating to ${BASE_LAYER_NAME}-$STACK_NAME)"
    fi
  done

  NEW_LAYERS="$NEW_LAYERS $NEW_LAYER_ARN"
  NEW_LAYERS=$(echo "$NEW_LAYERS" | xargs)
  echo "  Updating $FUNC_NAME with layers: $NEW_LAYERS"

  if aws lambda update-function-configuration --function-name "$FUNC_NAME" --layers $NEW_LAYERS >/dev/null 2>&1; then
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
        """Return a buildspec dict for CodeBuild.

        If ``version`` is provided, the buildspec targets a single Python
        version. Otherwise, it handles all versions listed in
        ``self.python_versions``.
        
        Includes ARG_MAX protection for long command lines.
        """

        versions = [version] if version else self.python_versions
        primary = versions[0]

        install_commands = [
            "echo Installing native libraries for Pillow…",
            "dnf install -y libjpeg-turbo libpng libtiff libwebp freetype lcms2 zlib",
            "pip install build",
            # Add error handling for pip install
            'echo "Setting up pip error handling..."',
            "set -e",  # Exit on any error
        ]

        if version:
            build_commands = [
                "echo Build directory prep",
                # Add debug logging if enabled (use || true to prevent failure when DEBUG_MODE is false)
                '[ "$DEBUG_MODE" = "True" ] && echo "DEBUG: Starting build for Python ${PYTHON_VERSION}" || true',
                '[ "$DEBUG_MODE" = "True" ] && echo "DEBUG: Current directory:" && pwd || true',
                '[ "$DEBUG_MODE" = "True" ] && echo "DEBUG: Directory contents:" && ls -la || true',
                "echo Checking source structure:",
                "ls -la source/ || echo 'source directory not found'",
                "ls -la source/pyproject.toml || echo 'pyproject.toml not found in source'",
                "rm -rf build && mkdir -p build",
                f"mkdir -p build/python/lib/python{version}/site-packages",
                'echo "Building wheel"',
                "cd source && python3 -m build --wheel --outdir ../dist/ && cd ..",
                'echo "Installing wheel with optimization exclusions for Lambda layer"',
                # Find the wheel file and install with extras if specified
                'echo "Finding wheel file..."',
                'WHEEL_FILE=$(ls dist/*.whl | head -1)',
                'echo "Found wheel: $WHEEL_FILE"',
                # Install with extras if specified (e.g., [lambda] for lightweight chromadb-client)
                f"python{version} -m pip install --no-cache-dir --no-compile "
                f"\"$WHEEL_FILE{f'[{self.package_extras}]' if self.package_extras else ''}\" "
                f"-t build/python/lib/python{version}/site-packages || "
                f"{{ echo 'First pip install attempt failed, retrying...'; "
                f"python{version} -m pip install --no-cache-dir --no-compile "
                f"\"$WHEEL_FILE{f'[{self.package_extras}]' if self.package_extras else ''}\" "
                f"-t build/python/lib/python{version}/site-packages; }}",
                'echo "Removing packages provided by AWS Lambda runtime"',
                "rm -rf build/python/lib/python*/site-packages/boto* || true",
                "rm -rf build/python/lib/python*/site-packages/*boto* || true",
                "rm -rf build/python/lib/python*/site-packages/dateutil* || true",
                "rm -rf build/python/lib/python*/site-packages/jmespath* || true",
                "rm -rf build/python/lib/python*/site-packages/s3transfer* || true",
                "rm -rf build/python/lib/python*/site-packages/six* || true",
                'echo "Removing numpy (use AWS Lambda Layer instead)"',
                "rm -rf build/python/lib/python*/site-packages/numpy* || true",
                'echo "Cleaning up unnecessary files from all packages"',
                "find build -type d -name '__pycache__' -exec rm -rf {} + "
                "2>/dev/null || true",
                "find build -type d -name 'tests' -o -name 'test' -exec rm -rf {} + "
                "2>/dev/null || true",
                "find build -type f -name '*.pyc' -o -name '*.pyo' -exec rm -f {} + "
                "2>/dev/null || true",
                "find build -type f \\( -name '*.md' -o -name '*.txt' -o -name '*.yml' "
                "-o -name '*.yaml' -o -name '*.rst' \\) -not -path "
                "'*/dist-info/top_level.txt' -exec rm -f {} + 2>/dev/null || true",
                "find build -type f -name '*.dist-info/RECORD' -exec sed -i "
                "'/\\.pyc/d' {} + 2>/dev/null || true",
                'echo "Copying native libraries"',
                "mkdir -p build/lib && cp /usr/lib64/libjpeg*.so* "
                "/usr/lib64/libpng*.so* /usr/lib64/libtiff*.so* "
                "/usr/lib64/libwebp*.so* /usr/lib64/liblcms2*.so* "
                "/usr/lib64/libfreetype*.so* build/lib || true",
                'echo "Flattening site-packages to root python directory"',
                "cp -r build/python/lib/python*/site-packages/. build/python/ || true",
                'echo "Final cleanup after flattening"',
                "rm -rf build/python/boto* || true",
                "rm -rf build/python/*boto* || true",
                "rm -rf build/python/dateutil* || true",
                "rm -rf build/python/jmespath* || true",
                "rm -rf build/python/s3transfer* || true",
                "rm -rf build/python/six* || true",
                "rm -rf build/python/numpy* || true",
                "chmod -R 755 build",
                # Validate layer output
                'echo "Validating build output..."',
                "[ -d build/python ] || { echo 'ERROR: build/python not found'; exit 1; }",
                "ls -la build/python/ | head -20 || true",
                'echo "Build validation complete"',
            ]
            if self.needs_pillow:
                build_commands.append('echo "Installing Pillow"')
                build_commands.append(
                    f"python{version} -m pip install --no-cache-dir Pillow -t build/python/lib/python{version}/site-packages"
                )
            pre_build_phase = {
                "commands": [
                    'if [ "$NEEDS_PILLOW" = "True" ]; then '
                    'echo "Pre-build: generating Pillow bundle"; '
                    f"cd source && python{version} -m pip install --no-cache-dir Pillow -t ../build/pillow && cd ..; "
                    "mkdir -p build/lib && cp -r build/pillow/. build/lib/; "
                    'echo "Static Pillow bundle added"; '
                    "fi"
                ]
            }
            artifacts = {
                "files": ["python/**/*", "lib/**/*"],
                "base-directory": "build",
            }
        else:
            build_commands = [
                "echo Build directory prep",
                "pwd",
                "ls -la",
                "echo Checking source structure:",
                "ls -la source/ || echo 'source directory not found'",
                "ls -la source/pyproject.toml || echo 'pyproject.toml not found in source'",
                "rm -rf build && mkdir -p build",
                'for v in $(echo "$PYTHON_VERSIONS" | tr "," " "); do mkdir -p build/python/lib/python${v}/site-packages; done',
                'echo "Building wheel"',
                "cd source && python3 -m build --wheel --outdir ../dist/ && cd ..",
                'echo "Installing wheel and Pillow for each runtime with Lambda optimizations"',
                'for v in $(echo "$PYTHON_VERSIONS" | tr "," " "); do python${v} -m pip install --no-cache-dir --no-compile dist/*.whl Pillow -t build/python/lib/python${v}/site-packages; done',
                'echo "Removing boto3/botocore (provided by AWS Lambda runtime)"',
                "find build -type d -name 'boto*' -exec rm -rf {} + 2>/dev/null || true",
                'echo "Cleaning up unnecessary files from all packages"',
                "find build -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true",
                "find build -type d -name 'tests' -o -name 'test' -exec rm -rf {} + 2>/dev/null || true",
                "find build -type f -name '*.pyc' -o -name '*.pyo' -exec rm -f {} + 2>/dev/null || true",
                "find build -type f \\( -name '*.md' -o -name '*.txt' -o -name '*.yml' -o -name '*.yaml' -o -name '*.rst' \\) -not -path '*/dist-info/top_level.txt' -exec rm -f {} + 2>/dev/null || true",
                'echo "Copying native libraries"',
                "mkdir -p build/lib && cp /usr/lib64/libjpeg*.so* /usr/lib64/libpng*.so* /usr/lib64/libtiff*.so* /usr/lib64/libwebp*.so* /usr/lib64/liblcms2*.so* /usr/lib64/libfreetype*.so* build/lib || true",
                'echo "Flattening site-packages to root python directory"',
                "cp -r build/python/lib/python*/site-packages/. build/python/ || true",
                'echo "Final cleanup after flattening"',
                "rm -rf build/python/boto* || true",
                "rm -rf build/python/*boto* || true",
                "rm -rf build/python/dateutil* || true",
                "rm -rf build/python/jmespath* || true",
                "rm -rf build/python/s3transfer* || true",
                "rm -rf build/python/six* || true",
                "rm -rf build/python/numpy* || true",
                "chmod -R 755 build",
            ]
            pre_build_phase = {
                "commands": [
                    'if [ "$NEEDS_PILLOW" = "True" ]; then '
                    'echo "Pre-build: generating Pillow bundle"; '
                    'echo "Installing Pillow for each runtime for static bundle"; '
                    'for v in $(echo "$PYTHON_VERSIONS" | tr "," " "); do cd source && python${v} -m pip install --no-cache-dir Pillow -t ../build/pillow && cd ..; done; '
                    "mkdir -p build/lib && cp -r build/pillow/. build/lib/; "
                    'echo "Static Pillow bundle added"; '
                    "fi"
                ]
            }
            artifacts = {
                "files": ["python/**/*"],
                "base-directory": "build",
            }

        return {
            "version": 0.2,
            "phases": {
                "pre_build": pre_build_phase,
                "install": {
                    "runtime-versions": {"python": primary},
                    "commands": install_commands,
                },
                "build": {"commands": build_commands},
            },
            "artifacts": artifacts,
        }

    def _setup_fast_build(self, package_hash: str, package_path: str) -> None:
        """Set up the fast build process with CodePipeline and per-version CodeBuild projects."""

        # Create S3 bucket for artifacts
        # Truncate to fit AWS S3 bucket naming limits (63 chars)
        stack = pulumi.get_stack()
        
        # Use a hash of the full name for uniqueness when truncating
        full_name = f"{self.name}-{stack}"
        name_hash = hashlib.md5(full_name.encode()).hexdigest()[:8]
        
        # Build a bucket name that's guaranteed to be under 63 chars
        # Pattern: "fll-{name_short}-{stack_short}-{hash}"
        name_short = self.name[:10]
        stack_short = stack[:15] if len(stack) > 15 else stack
        
        bucket_name = f"fll-{name_short}-{stack_short}-{name_hash}"
        # Ensure lowercase and replace underscores
        bucket_name = bucket_name.lower().replace("_", "-").replace("--", "-")
        
        build_bucket = aws.s3.Bucket(
            resource_name=f"fast-lambda-layer-{self.name}-artifacts-{stack}",
            bucket=bucket_name,
            force_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Configure versioning as a separate resource
        aws.s3.BucketVersioningV2(
            f"fast-lambda-layer-{self.name}-artifacts-versioning",
            bucket=build_bucket.id,
            versioning_configuration=(
                aws.s3.BucketVersioningV2VersioningConfigurationArgs(
                    status="Enabled"
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
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
        codebuild_role = aws.iam.Role(
            f"{self.name}-fast-codebuild-role",
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

        # Create CodeBuild policy with permissions for layer publishing and function updates
        aws.iam.RolePolicy(
            f"{self.name}-fast-codebuild-policy",
            role=codebuild_role.id,
            policy=pulumi.Output.all(build_bucket.arn, self.layer_name).apply(
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
                                    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*",
                                    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*:*",
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
                                "Resource": [
                                    f"arn:aws:codebuild:{aws.config.region}:{aws.get_caller_identity().account_id}:project/{self.name}-publish-{pulumi.get_stack()}",
                                    f"arn:aws:codebuild:{aws.config.region}:{aws.get_caller_identity().account_id}:project/{self.name}-*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for CodePipeline
        pipeline_role = aws.iam.Role(
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
        aws.iam.RolePolicy(
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
        aws.iam.RolePolicy(
            f"{self.name}-pipeline-codebuild-policy",
            role=pipeline_role.id,
            policy=Output.all(
                aws.config.region, aws.get_caller_identity().account_id
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
                                "Resource": f"arn:aws:codebuild:{args[0]}:{args[1]}:project/{self.name}-*",
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create a CodeBuild project for each Python version
        build_projects = {}
        for v in self.python_versions:
            project = aws.codebuild.Project(
                resource_name=f"{self.name}-build-py{v.replace('.', '')}",
                name=f"{self.name}-build-py{v.replace('.', '')}-{pulumi.get_stack()}",
                service_role=codebuild_role.arn,
                source=aws.codebuild.ProjectSourceArgs(
                    type="S3",
                    # instead of lambda b: f"{self.name}/source.zip", do:
                    location=build_bucket.bucket.apply(
                        lambda b: f"{b}/{self.name}/source.zip"
                    ),
                    buildspec=json.dumps(self._get_buildspec(version=v)),
                ),
                artifacts=aws.codebuild.ProjectArtifactsArgs(
                    type="S3",
                    location=build_bucket.bucket,
                    path=f"{self.name}/py{v.replace('.', '')}",
                    name="layer.zip",
                    packaging="ZIP",
                    namespace_type="NONE",
                ),
                environment=aws.codebuild.ProjectEnvironmentArgs(
                    type="ARM_CONTAINER",
                    compute_type="BUILD_GENERAL1_SMALL",
                    image=f"aws/codebuild/amazonlinux-aarch64-standard:3.0",
                    environment_variables=[
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="PYTHON_VERSION", value=v
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="LAYER_NAME", value=self.layer_name
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="PACKAGE_NAME", value=self.name
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="BUCKET_NAME", value=build_bucket.bucket
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="PACKAGE_DIR", value="source"
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="STACK_NAME", value=pulumi.get_stack()
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="NEEDS_PILLOW", value=str(self.needs_pillow)
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="DEBUG_MODE", value=str(self.debug_mode)
                        ),
                    ],
                ),
                cache=aws.codebuild.ProjectCacheArgs(
                    type="S3",
                    location=build_bucket.bucket.apply(
                        lambda b: f"{b}/{self.name}/cache"
                    ),
                ),
                logs_config=aws.codebuild.ProjectLogsConfigArgs(
                    cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                        status="ENABLED",
                        group_name=f"/aws/codebuild/{self.name}-build-py{v.replace('.', '')}",
                        stream_name=f"{self.name}-build-stream",
                    ),
                ),
                opts=pulumi.ResourceOptions(parent=self),
            )
            build_projects[v] = project

        def publish_buildspec() -> Dict[str, Any]:
            # This buildspec will merge all version artifacts under a single python/lib/python<ver>/site-packages tree, zip, upload to S3, and publish as one layer from S3.
            commands = []
            # Step 1: Prepare merged directory
            commands.append('echo "Preparing merged layer directory..."')
            commands.append("rm -rf merged && mkdir -p merged")
            # Step 2: Merge each version's unpacked artifact into python/lib/python<ver>/site-packages
            commands.append('echo "Setting up merged python/lib directory..."')
            commands.append(
                "rm -rf merged/python && mkdir -p merged/python/lib"
            )
            for idx, v in enumerate(self.python_versions):
                commands.append(f'echo "Merging artifacts for Python {v}..."')
                # Use the version string directly (e.g., "3.11", "3.12")
                commands.append(
                    f"mkdir -p merged/python/lib/python{v}/site-packages"
                )
                if idx == 0:
                    # Primary artifact in root workspace
                    commands.append(
                        f"cp -r python/* merged/python/lib/python{v}/site-packages/"
                    )
                else:
                    # Secondary artifacts under CODEBUILD_SRC_DIR_py<ver> (ver without dots)
                    ver = v.replace(".", "")
                    commands.append(
                        f"cp -r $CODEBUILD_SRC_DIR_py{ver}/python/* merged/python/lib/python{v}/site-packages/"
                    )
            # Step 3: Zip the merged python directory
            commands.append('echo "Zipping merged layer..."')
            commands.append("cd merged && zip -r ../layer.zip python && cd ..")
            # Validate the zip file
            commands.append('echo "Validating layer.zip..."')
            commands.append("[ -f layer.zip ] || { echo 'ERROR: layer.zip not created'; exit 1; }")
            commands.append("ZIP_SIZE=$(stat -c%s layer.zip 2>/dev/null || stat -f%z layer.zip 2>/dev/null || echo 0)")
            commands.append('echo "Layer zip size: $ZIP_SIZE bytes"')
            commands.append("[ \"$ZIP_SIZE\" -gt 0 ] || { echo 'ERROR: layer.zip is empty'; exit 1; }")
            # Check if zip size exceeds Lambda limits
            commands.append("MAX_SIZE=$((250 * 1024 * 1024))  # 250MB in bytes")
            commands.append("if [ \"$ZIP_SIZE\" -gt \"$MAX_SIZE\" ]; then echo \"WARNING: Layer size exceeds Lambda limit (250MB)\"; echo \"Size: $(($ZIP_SIZE / 1024 / 1024))MB\"; fi")
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
                '{ echo "ERROR: Failed to publish layer version"; echo "Output: $NEW_LAYER_ARN"; exit 1; }'
            )
            commands.append('echo "New layer ARN: $NEW_LAYER_ARN"')
            commands.append("[ -n \"$NEW_LAYER_ARN\" ] || { echo 'ERROR: No layer ARN returned'; exit 1; }")
            commands.append("export NEW_LAYER_ARN")
            commands.append(
                f'echo "{self._encode_shell_script(self._get_update_functions_script())}" | base64 -d > update_layers.sh'
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

        # Create the publish CodeBuild project
        publish_project = aws.codebuild.Project(
            f"{self.name}-publish",
            name=f"{self.name}-publish-{pulumi.get_stack()}",
            service_role=codebuild_role.arn,
            source=aws.codebuild.ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(publish_buildspec()),
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="CODEPIPELINE"),
            environment=aws.codebuild.ProjectEnvironmentArgs(
                type="ARM_CONTAINER",
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/amazonlinux-aarch64-standard:3.0",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="LAYER_NAME", value=self.layer_name
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PACKAGE_NAME", value=self.name
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="BUCKET_NAME", value=build_bucket.bucket
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="STACK_NAME", value=pulumi.get_stack()
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="NEEDS_PILLOW", value=str(self.needs_pillow)
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="DEBUG_MODE", value=str(self.debug_mode)
                    ),
                ],
            ),
            build_timeout=60,
            logs_config=aws.codebuild.ProjectLogsConfigArgs(
                cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                    status="ENABLED",
                    group_name=f"/aws/codebuild/{self.name}-publish",
                    stream_name=f"{self.name}-publish-stream",
                ),
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Define CodePipeline to run all builds in parallel and then publish layer versions
        pipeline = aws.codepipeline.Pipeline(
            resource_name=f"{self.name}-pipeline-{pulumi.get_stack()}",
            name=f"{self.name}-pipeline-{pulumi.get_stack()}",
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
                                "S3ObjectKey": f"{self.name}/source.zip",
                            },
                            run_order=1,
                        )
                    ],
                ),
                aws.codepipeline.PipelineStageArgs(
                    name="Build",
                    actions=[
                        aws.codepipeline.PipelineStageActionArgs(
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
                aws.codepipeline.PipelineStageArgs(
                    name="Deploy",
                    actions=[
                        aws.codepipeline.PipelineStageActionArgs(
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Trigger pipeline run when source is updated
        trigger_script = pipeline.name.apply(
            lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Changes detected, starting CodePipeline execution for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} --query pipelineExecutionId --output text)
echo "Triggered pipeline: $EXEC_ID"
"""
        )
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
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} --query pipelineExecutionId --output text)
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
            command.local.Command(
                f"{self.name}-sync-pipeline",
                create=sync_script,
                opts=pulumi.ResourceOptions(
                    parent=self, depends_on=[pipeline]
                ),
            )
            # Ensure Pulumi waits for pipeline before proceeding
            pulumi.log.info(f"Sync command added for pipeline {self.name}")

        # Pulumi no longer manages the LayerVersion resource; layer publication is handled by CodePipeline.
        self.arn = None  # Placeholder: Pulumi does not manage or export the layer ARN directly.

    def _create_and_run_upload_script(
        self, bucket: str, package_path: str, package_hash: str
    ) -> str:
        """Create a script file and return just the execution command."""
        try:
            # Generate the script content with embedded variables to avoid argument issues
            script_content = self._generate_upload_script(
                bucket, package_path, package_hash
            )

            # Create a persistent script file in /tmp with a unique name
            script_name = f"pulumi-upload-{self.name}-{package_hash[:8]}.sh"
            script_path = os.path.join("/tmp", script_name)

            # Write the script file
            with open(script_path, "w") as f:
                f.write(script_content)

            # Make it executable
            os.chmod(script_path, 0o755)

            # Return just the simple command to execute the script
            # No arguments or environment variables in the command line
            return f"/bin/bash {script_path}"
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to create upload script: {e}") from e

    def _generate_upload_script(self, bucket: str, package_path: str, package_hash: str) -> str:
        """Generate script to upload source package with safely embedded paths.
        
        Includes improved error handling and validation.
        """
        # Escape the paths to handle special characters
        safe_package_path = shlex.quote(package_path)
        safe_bucket = shlex.quote(bucket)

        return f"""#!/bin/bash
set -e

# Set variables within the script to avoid command line length issues
BUCKET={safe_bucket}
PACKAGE_PATH={safe_package_path}
HASH="{package_hash}"
LAYER_NAME="{self.name}"
FORCE_REBUILD="{self.force_rebuild}"

echo "📦 Checking if source upload needed for layer '$LAYER_NAME'..."

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

# Copy the entire package directory structure
cp -r "$PACKAGE_PATH"/* "$TMP_DIR/source/"

# Create zip quietly to reduce output
cd "$TMP_DIR"
zip -qr source.zip source
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

    def _generate_trigger_script(self, bucket: str, project_name: str, package_hash: str) -> str:
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
BUILD_STATUS=$(aws codebuild list-builds-for-project --project-name "$PROJECT" --query 'ids[0]' --output text 2>/dev/null || echo "None")
if [ "$BUILD_STATUS" != "None" ]; then
    CURRENT_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_STATUS" --query 'builds[0].buildStatus' --output text 2>/dev/null || echo "UNKNOWN")
    if [ "$CURRENT_STATUS" = "IN_PROGRESS" ]; then
        echo "⏳ Build already in progress. Skipping new build."
        exit 0
    fi
fi

# Start async build
echo "🏗️  Starting async build..."
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)
echo "✅ Build started: $BUILD_ID"
echo "📊 Monitor at: https://console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT/build/$BUILD_ID"
echo "⚡ Continuing with fast pulumi up (not waiting for completion)"
"""

    def _generate_initial_build_script(self, bucket: str, project_name: str) -> str:
        """Generate script to ensure initial layer exists."""
        return f"""#!/bin/bash
set -e

BUCKET="{bucket}"
PROJECT="{project_name}"

echo "🔍 Checking if initial layer exists for '{self.name}'..."

# Check if layer exists
if aws s3api head-object --bucket "$BUCKET" --key "{self.name}/layer.zip" &>/dev/null; then
    echo "✅ Layer already exists. No initial build needed."
    exit 0
fi

echo "🏗️  No layer found. Running initial build (this will wait for completion)..."

# Start build and wait
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)
echo "Build ID: $BUILD_ID"

while true; do
    BUILD_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
    echo "Build status: $BUILD_STATUS"

    if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
        echo "✅ Initial build completed successfully!"
        break
    elif [ "$BUILD_STATUS" = "FAILED" ] || [ "$BUILD_STATUS" = "FAULT" ] || [ "$BUILD_STATUS" = "STOPPED" ] || [ "$BUILD_STATUS" = "TIMED_OUT" ]; then
        echo "❌ Initial build failed with status: $BUILD_STATUS"
        exit 1
    fi

    sleep 30
done
"""

    def _generate_sync_script(
        self, bucket: str, project_name: str, layer_name: str, package_path: str, package_hash: str
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
if [ "$(aws s3 cp s3://$BUCKET/{self.name}/hash.txt - 2>/dev/null || echo '')" = "$HASH" ] && [ "{self.force_rebuild}" != "True" ]; then
    echo "✅ No changes detected. Skipping rebuild."

    # Ensure layer exists
    if ! aws s3api head-object --bucket "$BUCKET" --key "{self.name}/layer.zip" &>/dev/null; then
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
    BUILD_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
    echo "Build status: $BUILD_STATUS"

    if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
        echo "✅ Build completed successfully!"
        break
    elif [ "$BUILD_STATUS" = "FAILED" ] || [ "$BUILD_STATUS" = "FAULT" ] || [ "$BUILD_STATUS" = "STOPPED" ] || [ "$BUILD_STATUS" = "TIMED_OUT" ]; then
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
        "package_dir": "receipt_label",
        "name": "receipt-label",
        "description": "Label layer for receipt-label",
        "python_versions": ["3.12"],
        "needs_pillow": False,
        "package_extras": "lambda",  # Use lightweight chromadb-client for Lambda layer
    },
    {
        "package_dir": "receipt_upload",
        "name": "receipt-upload",
        "description": "Upload layer for receipt-upload",
        "python_versions": ["3.12"],
        "needs_pillow": True,
    },
]

# Create Lambda layers using the fast approach
# TEMPORARILY SKIP LAYER BUILDING
SKIP_LAYER_BUILDING = False  # Set to False to enable layer building

fast_lambda_layers = {}

if not SKIP_LAYER_BUILDING:
    for layer_config in layers_to_build:
        fast_layer = FastLambdaLayer(
            name=layer_config["name"],  # type: ignore
            package_dir=layer_config["package_dir"],  # type: ignore
            python_versions=layer_config["python_versions"],  # type: ignore
            description=layer_config["description"],  # type: ignore
            needs_pillow=layer_config["needs_pillow"],  # type: ignore
            package_extras=layer_config.get("package_extras"),  # type: ignore
        )
        fast_lambda_layers[layer_config["name"]] = fast_layer

    # Access the built layers by name
    fast_dynamo_layer = fast_lambda_layers["receipt-dynamo"]
    fast_label_layer = fast_lambda_layers["receipt-label"]
    fast_upload_layer = fast_lambda_layers["receipt-upload"]
else:
    # Create dummy objects when skipping
    class DummyLayer:
        def __init__(self, name: str) -> None:
            self.name = name
            self.arn = None
    
    fast_dynamo_layer = DummyLayer("receipt-dynamo")  # type: ignore
    fast_label_layer = DummyLayer("receipt-label")  # type: ignore
    fast_upload_layer = DummyLayer("receipt-upload")  # type: ignore

# Create aliases for backward compatibility
dynamo_layer = fast_dynamo_layer
label_layer = fast_label_layer
upload_layer = fast_upload_layer

# Export the layer ARNs for reference
pulumi.export("fast_dynamo_layer_arn", fast_dynamo_layer.arn)
pulumi.export("fast_label_layer_arn", fast_label_layer.arn)
pulumi.export("fast_upload_layer_arn", fast_upload_layer.arn)

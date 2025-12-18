#!/usr/bin/env python3
"""
simple_lambda_layer.py

A simplified Lambda Layer component that uses local commands to:
1. Upload package to S3
2. Trigger CodeBuild
3. Wait for build completion
4. Update Lambda functions

This eliminates the complexity of Step Functions, SQS, EventBridge, etc.
"""

import glob
import hashlib
import json
import os
import time
from pathlib import Path

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import ComponentResource, Output
from utils import _find_project_root

PROJECT_DIR = _find_project_root()
config = pulumi.Config("lambda-layer")


class SimpleLambdaLayer(ComponentResource):
    """
    A simplified Lambda Layer component that uses local commands for orchestration.

    This approach is much simpler and more reliable than the event-driven architecture.
    """

    def __init__(
        self,
        name: str,
        package_dir: str,
        python_versions,
        description: str = None,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__(f"simple-lambda-layer:{name}", name, {}, opts)

        self.name = name
        self.layer_name = f"{name}-{pulumi.get_stack()}"
        self.package_dir = package_dir

        # Accept either a single version string or a list
        if isinstance(python_versions, str):
            self.python_versions = [python_versions]
        else:
            self.python_versions = list(python_versions)

        self.description = description or f"Automatically built Lambda layer for {name}"
        self.opts = opts

        # Validate package directory
        self._validate_package_dir()

        # Get the force-rebuild config
        self.force_rebuild = config.get_bool("force-rebuild") or False

        self._setup_simple_build()

    def _validate_package_dir(self):
        """Validate that the package directory exists and contains the necessary files."""
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        if not os.path.exists(package_path):
            raise ValueError(f"Package directory {package_path} does not exist")

        required_files = ["pyproject.toml"]
        missing_files = [
            f
            for f in required_files
            if not os.path.exists(os.path.join(package_path, f))
        ]
        if missing_files:
            raise ValueError(
                f"Package directory {package_path} is missing required files: {', '.join(missing_files)}"
            )

        python_files = glob.glob(os.path.join(package_path, "**/*.py"), recursive=True)
        if not python_files:
            raise ValueError(
                f"Package directory {package_path} contains no Python files"
            )

    def _calculate_package_hash(self):
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

    def _get_buildspec(self):
        """Generate the buildspec.yml content for CodeBuild."""
        primary = self.python_versions[0]
        return {
            "version": 0.2,
            "phases": {
                "install": {
                    "runtime-versions": {"python": primary},
                    "commands": [
                        "echo Installing build tooling ...",
                        "yum install -y libjpeg-devel zlib-devel",
                        "pip install --upgrade pip build",
                    ],
                },
                "build": {
                    "commands": [
                        "echo Build directory prep",
                        "rm -rf build",
                        "mkdir -p build",
                        'for v in $(echo "$PYTHON_VERSIONS" | tr "," " "); do '
                        "mkdir -p build/python/lib/python${v}/site-packages; "
                        "done",
                        'echo "Building wheel"',
                        "python -m build source --wheel --outdir dist/",
                        'echo "Installing wheel into layer structure"',
                        'for v in $(echo "$PYTHON_VERSIONS" | tr "," " "); do '
                        "pip install dist/*.whl -t build/python/lib/python${v}/site-packages; "
                        "done",
                        "chmod -R 755 build/python",
                    ],
                },
            },
            "artifacts": {"files": ["python/**/*"], "base-directory": "build"},
        }

    def _setup_simple_build(self):
        """Set up the simplified build process using local commands."""

        # Create S3 bucket for artifacts
        build_bucket = aws.s3.Bucket(
            resource_name=f"simple-lambda-layer-{self.name}-artifacts-{pulumi.get_stack()}",
            bucket=f"simple-lambda-layer-{self.name}-artifacts-{pulumi.get_stack()}",
            force_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for CodeBuild
        codebuild_role = aws.iam.Role(
            f"{self.name}-simple-codebuild-role",
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create CodeBuild policy
        codebuild_policy = aws.iam.RolePolicy(
            f"{self.name}-simple-codebuild-policy",
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
                                    "s3:PutObject",
                                    "s3:GetObjectVersion",
                                ],
                                "Resource": f"{args[0]}/{self.name}/*",
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
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create CodeBuild project
        codebuild_project = aws.codebuild.Project(
            f"{self.name}-simple-layer-build-{pulumi.get_stack()}",  # Pulumi logical name with stack
            service_role=codebuild_role.arn,
            source=aws.codebuild.ProjectSourceArgs(
                type="S3",
                location=pulumi.Output.concat(
                    build_bucket.bucket, f"/{self.name}/source.zip"
                ),
                buildspec=pulumi.Output.from_input(self._get_buildspec()).apply(
                    lambda spec: json.dumps(spec)
                ),
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(
                type="S3",
                location=build_bucket.bucket,
                path=self.name,
                name="layer.zip",
                packaging="ZIP",
                namespace_type="NONE",
            ),
            environment=aws.codebuild.ProjectEnvironmentArgs(
                type="LINUX_CONTAINER",
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="LAYER_NAME", value=self.layer_name
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PACKAGE_DIR", value="source"
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PYTHON_VERSIONS",
                        value=",".join(self.python_versions),
                    ),
                ],
            ),
            cache=aws.codebuild.ProjectCacheArgs(
                type="S3",
                location=pulumi.Output.concat(
                    build_bucket.bucket, f"/{self.name}/cache"
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[codebuild_role, codebuild_policy],
            ),
        )

        # Calculate package hash for change detection
        package_hash = self._calculate_package_hash()
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        # Single orchestration command that does everything
        orchestration_command = command.local.Command(
            f"{self.name}-simple-orchestration",
            create=pulumi.Output.all(
                build_bucket.bucket, codebuild_project.name, self.layer_name
            ).apply(
                lambda args: self._create_and_run_orchestration_script(
                    args[0], args[1], args[2], package_path, package_hash
                )
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[codebuild_project]),
        )

        # Create the Lambda layer version resource
        self.layer_version = aws.lambda_.LayerVersion(
            f"{self.name}-simple-lambda-layer",
            layer_name=self.layer_name,
            compatible_runtimes=[f"python{v}" for v in self.python_versions],
            compatible_architectures=["x86_64", "arm64"],
            description=self.description,
            s3_bucket=build_bucket.bucket,
            s3_key=f"{self.name}/layer.zip",
            opts=pulumi.ResourceOptions(
                depends_on=[orchestration_command], parent=self
            ),
        )

        self.arn = self.layer_version.arn

    def _create_and_run_orchestration_script(
        self, bucket, project_name, layer_name, package_path, package_hash
    ):
        """Create a script file and return just the execution command."""
        import os
        import tempfile

        try:
            # Generate the script content with embedded variables to avoid argument issues
            script_content = self._generate_orchestration_script(
                bucket, project_name, layer_name, package_path, package_hash
            )

            # Create a persistent script file in /tmp with a unique name
            script_name = f"pulumi-orchestrate-{self.name}-{package_hash[:8]}.sh"
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
            raise RuntimeError(f"Failed to create orchestration script: {e}") from e

    def _generate_orchestration_script(
        self, bucket, project_name, layer_name, package_path, package_hash
    ):
        """Generate a single script that safely embeds all paths."""
        # Escape the paths to handle special characters
        import shlex

        safe_bucket = shlex.quote(bucket)
        safe_project = shlex.quote(project_name)
        safe_layer = shlex.quote(layer_name)
        safe_package_path = shlex.quote(package_path)

        return f"""#!/bin/bash
set -e

# Set variables within the script to avoid command line length issues
BUCKET={safe_bucket}
PROJECT={safe_project}
LAYER_NAME={safe_layer}
PACKAGE_PATH={safe_package_path}
HASH="{package_hash}"
STACK="{pulumi.get_stack()}"
FORCE_REBUILD="{self.force_rebuild}"

echo "Starting simplified layer build and update process..."

# Step 1: Check if we need to rebuild
LAYER_KEY="{self.name}"
if ! aws s3api head-object --bucket "$BUCKET" --key "$LAYER_KEY/hash.txt" &>/dev/null; then
    NEEDS_REBUILD=true
    echo "No previous hash found. Building layer."
elif [ "$(aws s3 cp s3://$BUCKET/$LAYER_KEY/hash.txt - 2>/dev/null || echo '')" != "$HASH" ]; then
    NEEDS_REBUILD=true
    echo "Hash changed. Rebuilding layer."
elif [ "$FORCE_REBUILD" = "True" ]; then
    NEEDS_REBUILD=true
    echo "Force rebuild enabled. Rebuilding layer."
else
    NEEDS_REBUILD=false
    echo "No changes detected. Skipping rebuild."
fi

if [ "$NEEDS_REBUILD" = "true" ]; then
    # Step 2: Upload source package
    echo "Uploading source package..."
    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TMP_DIR"' EXIT

    mkdir -p "$TMP_DIR/source"
    cp -r "$PACKAGE_PATH"/* "$TMP_DIR/source/"

    # Use cd instead of pushd/popd for better shell compatibility
    cd "$TMP_DIR"
    zip -r source.zip source
    cd - >/dev/null

    aws s3 cp "$TMP_DIR/source.zip" "s3://$BUCKET/$LAYER_KEY/source.zip"

    # Step 3: Start CodeBuild and wait for completion
    echo "Starting CodeBuild..."
    BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)
    echo "Build ID: $BUILD_ID"

    echo "Waiting for build to complete..."
    while true; do
        BUILD_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
        echo "Build status: $BUILD_STATUS"

        if [ "$BUILD_STATUS" = "SUCCEEDED" ]; then
            echo "Build completed successfully!"
            break
        elif [ "$BUILD_STATUS" = "FAILED" ] || [ "$BUILD_STATUS" = "FAULT" ] || [ "$BUILD_STATUS" = "STOPPED" ] || [ "$BUILD_STATUS" = "TIMED_OUT" ]; then
            echo "Build failed with status: $BUILD_STATUS"
            exit 1
        fi

        sleep 30
    done

    # Step 4: Publish new layer version
    echo "Publishing new layer version..."
    NEW_LAYER_ARN=$(aws lambda publish-layer-version \
        --layer-name "$LAYER_NAME" \
        --content S3Bucket="$BUCKET",S3Key="$LAYER_KEY/layer.zip" \
        --compatible-runtimes {' '.join([f'"python{v}"' for v in self.python_versions])} \
        --compatible-architectures "x86_64" "arm64" \
        --description "{self.description}" \
        --query 'LayerVersionArn' \
        --output text)

    echo "New layer ARN: $NEW_LAYER_ARN"

    # Step 5: Update all Lambda functions that use this layer
    echo "Updating Lambda functions..."
    aws lambda list-functions --query 'Functions[*].[FunctionName,FunctionArn]' --output text | \
    while read -r FUNC_NAME FUNC_ARN; do
        # Check if function has the correct environment tag
        TAGS=$(aws lambda list-tags --resource "$FUNC_ARN" --query 'Tags.environment' --output text 2>/dev/null || echo "None")
        if [ "$TAGS" = "$STACK" ]; then
            echo "Checking function: $FUNC_NAME"

            # Get current layers
            CURRENT_LAYERS=$(aws lambda get-function-configuration --function-name "$FUNC_NAME" --query 'Layers[*].Arn' --output text)

            # Build new layer list (remove old versions of same layer, add new version)
            NEW_LAYERS=""
            for LAYER in $CURRENT_LAYERS; do
                # Extract layer name without version (everything except last part after last colon)
                LAYER_BASE=$(echo "$LAYER" | sed 's/:[^:]*$//')
                NEW_LAYER_BASE=$(echo "$NEW_LAYER_ARN" | sed 's/:[^:]*$//')

                # If this is not the same layer family, keep it
                if [ "$LAYER_BASE" != "$NEW_LAYER_BASE" ]; then
                    NEW_LAYERS="$NEW_LAYERS $LAYER"
                fi
            done

            # Add the new layer version
            NEW_LAYERS="$NEW_LAYERS $NEW_LAYER_ARN"

            # Update function if it has any layers
            if [ -n "$NEW_LAYERS" ]; then
                echo "Updating $FUNC_NAME with layers: $NEW_LAYERS"
                aws lambda update-function-configuration \
                    --function-name "$FUNC_NAME" \
                    --layers $NEW_LAYERS >/dev/null
                echo "Updated $FUNC_NAME successfully"
            fi
        fi
    done

    # Step 6: Save the new hash
    echo "$HASH" | aws s3 cp - "s3://$BUCKET/$LAYER_KEY/hash.txt"
    echo "Process completed successfully!"
else
    echo "No rebuild needed. Checking if layer version exists..."
    if ! aws s3api head-object --bucket "$BUCKET" --key "$LAYER_KEY/layer.zip" &>/dev/null; then
        echo "Layer zip not found. This shouldn't happen. Please run with force-rebuild."
        exit 1
    fi
    echo "Layer exists. Process completed."
fi
"""


# Define the layers to build
layers_to_build = [
    {
        "package_dir": "receipt_dynamo",
        "name": "receipt-dynamo",
        "description": "DynamoDB layer for receipt-dynamo",
        "python_versions": ["3.12"],
    },
    {
        "package_dir": "receipt_upload",
        "name": "receipt-upload",
        "description": "Upload layer for receipt-upload",
        "python_versions": ["3.12"],
    },
]

# Create Lambda layers using the simplified approach
simple_lambda_layers = {}

for layer_config in layers_to_build:
    simple_layer = SimpleLambdaLayer(
        name=layer_config["name"],
        package_dir=layer_config["package_dir"],
        python_versions=layer_config["python_versions"],
        description=layer_config["description"],
    )
    simple_lambda_layers[layer_config["name"]] = simple_layer

# Access the built layers by name
simple_dynamo_layer = simple_lambda_layers["receipt-dynamo"]
simple_upload_layer = simple_lambda_layers["receipt-upload"]

# Create aliases for backward compatibility
dynamo_layer = simple_dynamo_layer
upload_layer = simple_upload_layer

# Export the layer ARNs for reference
pulumi.export("simple_dynamo_layer_arn", simple_dynamo_layer.arn)
pulumi.export("simple_upload_layer_arn", simple_upload_layer.arn)

#!/usr/bin/env python3
"""
lambda_layer.py

This module defines the LambdaLayer component resource, which automates the process of building,
publishing, and updating AWS Lambda Layers via AWS CodeBuild and Step Functions. The component manages
the entire lifecycle of a Lambda layer - from verifying the source package to producing a built artifact,
and then updating dependent Lambda functions automatically.

Key features:
  - **Source Directory Validation:** Ensures that the provided package directory (which should contain a
    valid pyproject.toml and Python source files) exists and is properly structured.

  - **Change Detection:** Computes a hash of the package contents so that the layer rebuild is triggered
    only when changes are detected (or when forced).

  - **Customizable Buildspec:** Generates a buildspec for AWS CodeBuild to build the layer. The buildspec
    creates a zip archive with the exact AWS Lambda layer structure:
      python/lib/python<version>/site-packages/...

  - **Metadata Configuration:** The LambdaLayer constructor now accepts a `description` parameter, which is
    used to provide a human-readable description for the layer version. This description is propagated
    both to the Pulumi-managed Lambda layer version and the publish handler environment.

  - **Compatible Architectures:** The component explicitly sets `compatible_architectures` (e.g., ["x86_64", "arm64"])
    on the Lambda layer version resource so that it can run on the desired architectures.

  - **Publishing and Updates:** A separate publish handler (defined in publish_handler.py) is invoked via a
    Step Functions state machine, which publishes a new Lambda layer version from a built artifact stored in S3.
    The publish handler reads the layer name, description, and compatible architectures from its environment variables.

  - **Automated Updates of Dependent Lambda Functions:** Once a new layer version is published, the component's
    orchestration automatically updates all Lambda functions (filtered by environment tags) to use the newest
    layer version.

Usage Example:

    dynamo_layer = LambdaLayer(
         name="receipt-dynamo",
         package_dir="receipt_dynamo",
         python_version="3.12",
         description="Automatically built Lambda layer for receipt-dynamo",
    )

In this example:
  - The source for the layer is located in the "receipt_dynamo" directory (relative to the project root).
  - The layer is built for Python 3.12.
  - A custom description is provided.
  - The resulting layer version resource will include compatible runtimes, a description, and the specified
    compatible architectures.
  - The build process is managed by CodeBuild, and the built artifact is published and used to update any dependent
    Lambda functions automatically.

This component uses additional resources such as:
  - An S3 bucket for storing build artifacts.
  - A CodeBuild project to run the build according to a generated buildspec.
  - A Step Functions state machine to orchestrate the build and publish workflow.
  - IAM roles and policies to provide the necessary permissions.
  - A publish handler Lambda function (in publish_handler.py) to call lambda:PublishLayerVersion with the proper
    description and compatible architectures.

Ensure your environment (both local and AWS) meets the prerequisites for building Python layers, particularly
that the build environment uses an Amazon Linux image so that binary dependencies (e.g. from Pillow) are compatible
with AWS Lambda.

"""

import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import ComponentResource, Output
from utils import _find_project_root

# Constants


PROJECT_DIR = _find_project_root()
S3_BUCKET_NAME = "lambdalayerpulumi"
CODEBUILD_TIMEOUT = 300  # 5 minutes timeout

config = pulumi.Config("lambda-layer")


class LambdaLayer(ComponentResource):
    """
    A Pulumi component that builds and manages Lambda Layers using AWS CodeBuild.

    This component automates the process of building and deploying Lambda Layers
    in a cloud environment. It handles the complete lifecycle from source
    code to deployment.

    Attributes:
        name (str): The name of the Lambda Layer.
        package_dir (str): The directory containing the source code for the Lambda Layer.
        python_version (str): The version of Python to use for the Lambda Layer.
        opts (pulumi.ResourceOptions): Additional options for the Lambda Layer.
    """

    def __init__(
        self,
        name: str,
        package_dir: str,
        python_versions,
        description: str = None,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__(f"lambda-layer:{name}", name, {}, opts)

        self.name = name
        # Use a stack‑qualified name for the actual AWS Lambda layer
        self.layer_name = f"{name}-{pulumi.get_stack()}"
        self.package_dir = package_dir
        # Accept either a single version string or a list
        if isinstance(python_versions, str):
            self.python_versions = [python_versions]
        else:
            self.python_versions = list(python_versions)
        self.description = (
            description or f"Automatically built Lambda layer for {name}"
        )
        self.opts = opts

        # Validate package directory
        self._validate_package_dir()

        # Get the force-rebuild config
        self.force_rebuild = config.get_bool("force-rebuild") or False

        self._setup_layer_build()

    def _validate_package_dir(self):
        """Validate that the package directory exists and contains the necessary files."""
        # Get the absolute path to the package directory
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        # Check if directory exists
        if not os.path.exists(package_path):
            raise ValueError(
                f"Package directory {package_path} does not exist"
            )

        # Check for required files
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

        # Check for Python files
        python_files = glob.glob(
            os.path.join(package_path, "**/*.py"), recursive=True
        )
        if not python_files:
            raise ValueError(
                f"Package directory {package_path} contains no Python files"
            )

    def _calculate_package_hash(self):
        """Calculate a hash of the package contents to detect changes."""
        hash_obj = hashlib.sha256()
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        # Sort files to ensure consistent hashing
        files_to_hash = []
        for root, _, files in os.walk(package_path):
            for file in files:
                if file.endswith(".py") or file == "pyproject.toml":
                    files_to_hash.append(os.path.join(root, file))

        # Sort files for consistency
        for file_path in sorted(files_to_hash):
            with open(file_path, "rb") as f:
                hash_obj.update(f.read())
            # Also hash the relative path to detect renamed files
            rel_path = os.path.relpath(file_path, package_path)
            hash_obj.update(rel_path.encode())

        return hash_obj.hexdigest()

    def _setup_layer_build(self):
        """Set up the asynchronous build process."""

        tags = {
            "project": "receipt-parser",
            "component": f"lambda-layer-{self.name}",
            "environment": pulumi.get_stack(),
        }
        # Create an S3 bucket for source code and build artifacts
        build_bucket = aws.s3.Bucket(
            resource_name=f"lambda-layer-{self.name}-artifacts-{pulumi.get_stack()}",
            bucket=f"lambda-layer-{self.name}-artifacts-{pulumi.get_stack()}",
            force_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Calculate package hash
        package_hash = self._calculate_package_hash()

        # Get the absolute path to the package directory
        package_path = os.path.join(PROJECT_DIR, self.package_dir)

        # Create a local command to upload the package if it has changed
        upload_command = command.local.Command(
            f"{self.name}-upload-source",
            create=pulumi.Output.all(build_bucket.bucket, package_hash).apply(
                lambda args: self._create_and_run_upload_script(
                    args[0], package_path, package_hash
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for CodeBuild with more specific permissions
        codebuild_role = aws.iam.Role(
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
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create the bucket ARN and layer ARN variables to use in policies
        bucket_arn = build_bucket.arn
        layer_arn_pattern = Output.all().apply(
            lambda _: f"arn:aws:lambda:*:*:layer:{self.layer_name}:*"
        )

        # Attach more specific policies to the role
        codebuild_policy = aws.iam.RolePolicy(
            f"{self.name}-codebuild-policy",
            role=codebuild_role.id,
            policy=pulumi.Output.all(bucket_arn, layer_arn_pattern).apply(
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
                                "Resource": args[1],
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

        # Create the CodeBuild project
        codebuild_project = aws.codebuild.Project(
            resource_name=f"{self.name}-layer-build",
            name=f"{self.name}-layer-build-{pulumi.get_stack()}",
            service_role=codebuild_role.arn,
            source=aws.codebuild.ProjectSourceArgs(
                type="S3",
                location=pulumi.Output.concat(
                    build_bucket.bucket, f"/{self.name}/source.zip"
                ),
                buildspec=pulumi.Output.from_input(
                    self._get_buildspec()
                ).apply(lambda spec: json.dumps(spec)),
            ),
            source_version=None,
            artifacts=aws.codebuild.ProjectArtifactsArgs(
                type="S3",
                location=build_bucket.bucket,
                path=self.name,  # e.g., receipt-dynamo/
                name="layer.zip",  # Artifact file name
                packaging="ZIP",  # CodeBuild zips the artifacts
                namespace_type="NONE",
            ),
            environment=aws.codebuild.ProjectEnvironmentArgs(
                type="LINUX_CONTAINER",  # Changed from 'LINUX_CONTAINER'
                compute_type="BUILD_GENERAL1_SMALL",  # Appropriate compute type for Lambda
                image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="LAYER_NAME", value=self.layer_name
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PACKAGE_DIR",
                        value="source",
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PYTHON_VERSIONS",
                        value=",".join(self.python_versions),
                    ),
                ],
            ),
            # Set a cache configuration to speed up builds
            cache=aws.codebuild.ProjectCacheArgs(
                type="S3",
                location=pulumi.Output.concat(
                    build_bucket.bucket, f"/{self.name}/cache"
                ),
            ),
            # Remove unused dependencies
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[codebuild_role, codebuild_policy, upload_command],
            ),
        )

        # Check if the .zip file already exists
        initial_sync_build = command.local.Command(
            f"{self.name}-initial-sync-build",
            create=pulumi.Output.all(
                build_bucket.bucket, codebuild_project.name
            ).apply(
                lambda args: f"""
                BUCKET_NAME="{args[0]}"
                PROJECT_NAME="{args[1]}"

                if ! aws s3api head-object --bucket "$BUCKET_NAME" --key "{self.name}/layer.zip"; then
                    echo "Layer zip not found. Triggering initial build.";

                    MAX_RETRIES=3
                    RETRY_DELAY=10

                    attempt=0
                    BUILD_ID=""
                    until [ "$attempt" -ge $MAX_RETRIES ]
                    do
                    BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT_NAME" --query 'build.id' --output text) && break
                    attempt=$((attempt+1))
                    echo "Attempt $attempt failed. Retrying in $RETRY_DELAY seconds..."
                    sleep $RETRY_DELAY
                    done

                    if [ -z "$BUILD_ID" ]; then
                    echo "CodeBuild trigger failed after $MAX_RETRIES attempts"
                    exit 1
                    fi

                    STATUS='IN_PROGRESS'
                    until [[ "$STATUS" != "IN_PROGRESS" ]]; do
                        echo 'Waiting for initial CodeBuild...'
                        sleep 15
                        STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
                    done
                    if [[ "$STATUS" != "SUCCEEDED" ]]; then
                    echo 'Build failed'; exit 1;
                    fi
                    echo 'Initial build completed successfully.';
                else
                    echo "Layer zip exists. No initial build required.";
                fi
                """
            ),
            opts=pulumi.ResourceOptions(
                depends_on=[codebuild_project, upload_command],
                parent=self,
            ),
        )

        # Trigger the initial synchronous build explicitly
        self.layer_version = aws.lambda_.LayerVersion(
            f"{self.name}-lambda-layer",
            layer_name=self.layer_name,
            compatible_runtimes=[f"python{v}" for v in self.python_versions],
            compatible_architectures=["x86_64", "arm64"],
            description=self.description,
            s3_bucket=build_bucket.bucket,
            s3_key=f"{self.name}/layer.zip",
            opts=pulumi.ResourceOptions(
                depends_on=[initial_sync_build], parent=self
            ),
        )

        self.arn = self.layer_version.arn

        # Create IAM role for EventBridge to invoke CodeBuild
        self.eventbridge_role = aws.iam.Role(
            f"{self.name}-eventbridge-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create a local command to start the build if force-rebuild is enabled
        # Enhanced to check if a build is already running
        if self.force_rebuild:
            start_build = command.local.Command(
                f"{self.name}-start-build",
                create=pulumi.Output.all(codebuild_project.name).apply(
                    lambda args: f"""
                    # Check if there's already a build in progress
                    BUILD_STATUS=$(aws codebuild list-builds-for-project --project-name {args[0]} --query 'ids[0]' --output text)
                    if [ "$BUILD_STATUS" != "None" ]; then
                        # Get build status
                        CURRENT_STATUS=$(aws codebuild batch-get-builds --ids $BUILD_STATUS --query 'builds[0].buildStatus' --output text)
                        if [ "$CURRENT_STATUS" == "IN_PROGRESS" ]; then
                            echo "A build is already in progress. Skipping new build."
                            exit 0
                        fi
                    fi

                    # Start a new build
                    aws codebuild start-build --project-name {args[0]}
                    """
                ),
                opts=pulumi.ResourceOptions(
                    parent=self, depends_on=[upload_command, codebuild_project]
                ),
            )

        # Create SNS topic for notifications
        sns_topic = aws.sns.Topic(
            f"{self.name}-codebuild-failure-notifications",
            display_name=f"{self.name}-CodeBuildFailures",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Export the SNS topic ARN (useful for subscribing later)
        pulumi.export(f"{self.name}_sns_topic_arn", sns_topic.arn)

        # Create CloudWatch alarm for CodeBuild failed builds
        failed_builds_metric = aws.cloudwatch.MetricAlarm(
            f"{self.name}-failed-builds-alarm",
            name=f"{self.name}-failed-builds",
            comparison_operator="GreaterThanOrEqualToThreshold",
            evaluation_periods=1,
            metric_name="FailedBuilds",
            namespace="AWS/CodeBuild",
            period=300,
            statistic="Sum",
            threshold=1,
            alarm_description=f"Alarm when {self.name} CodeBuild project has failed builds",
            dimensions={
                "ProjectName": codebuild_project.name,
            },
            alarm_actions=[sns_topic.arn],
            opts=pulumi.ResourceOptions(parent=self),
        )

        # (EventBridge integration removed)

        # Set up Step Functions for orchestration
        self._setup_step_functions(codebuild_project, build_bucket)

    def _get_buildspec(self):
        """Generate the buildspec.yml content for CodeBuild."""
        primary = self.python_versions[0]
        versions_csv = ",".join(self.python_versions)
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

    def _create_and_run_upload_script(self, bucket, package_path, package_hash):
        """Create a temporary script file and execute it to avoid 'argument list too long' error."""
        import tempfile
        import os
        
        try:
            # Generate the script content
            script_content = self._generate_upload_script(bucket, package_path, package_hash)
            
            # Create a temporary file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                f.write(script_content)
                script_path = f.name
            
            # Make it executable and run it with guaranteed cleanup
            os.chmod(script_path, 0o755)
            # Use curly braces to ensure cleanup happens even if script fails
            return f"{{ bash {script_path}; rm -f {script_path}; }}"
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to create upload script: {e}") from e

    def _generate_upload_script(self, bucket, package_path, package_hash):
        """Generate a bash script that uses tempfile for secure temporary directories."""
        return f"""
        # Create a unique temporary directory
        TMP_DIR=$(mktemp -d)

        # Ensure cleanup on exit
        trap 'rm -rf "$TMP_DIR"' EXIT

        # Create source directory and copy package files
        mkdir -p "$TMP_DIR/source"
        cp -r {package_path}/* "$TMP_DIR/source/"

        # Create the zip file with the source directory
        cd "$TMP_DIR"
        zip -r "$TMP_DIR/source.zip" source
        cd - >/dev/null

        # Define retries and delay between retries
        MAX_RETRIES=3
        RETRY_DELAY=5

        attempt=0
        until [ "$attempt" -ge $MAX_RETRIES ]
        do
            aws s3 cp "$TMP_DIR/source.zip" "s3://{bucket}/{self.name}/source.zip" && break
            attempt=$((attempt+1))
            echo "Attempt $attempt failed. Retrying in $RETRY_DELAY seconds..."
            sleep $RETRY_DELAY
        done

        if [ "$attempt" -ge $MAX_RETRIES ]; then
            echo "Command failed after $MAX_RETRIES attempts"
            exit 1
        fi

        # Upload the hash
        echo {package_hash} | aws s3 cp - s3://{bucket}/{self.name}/hash.txt
        """

    def _setup_step_functions(self, codebuild_project, build_bucket):
        """Set up Step Functions workflow to orchestrate the Lambda Layer build process."""
        config = pulumi.Config()
        # Create update_lambda_functions directory and handler.py if not existing

        # Create IAM role for Step Functions
        step_functions_role = aws.iam.Role(
            f"{self.name}-step-functions-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach necessary IAM policies for Step Functions
        step_functions_policy = aws.iam.RolePolicy(
            f"{self.name}-step-functions-policy",
            role=step_functions_role.id,
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
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": "*",
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for the Lambda function
        update_lambda_function_role = aws.iam.Role(
            f"{self.name}-update-lambda-functions-role",
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach basic execution role for the Lambda function
        lambda_basic_execution = aws.iam.RolePolicyAttachment(
            f"{self.name}-lambda-basic-execution",
            role=update_lambda_function_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Add permissions to update Lambda functions
        update_lambda_functions_policy = aws.iam.RolePolicy(
            f"{self.name}-update-lambda-functions-policy",
            role=update_lambda_function_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "lambda:UpdateFunctionConfiguration",
                                "lambda:ListLayerVersions",
                                "lambda:GetLayerVersion",
                                "lambda:ListFunctions",
                                "lambda:ListTags",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        lambda_timeout = config.get_int("update_lambda_timeout") or 300
        lambda_memory = config.get_int("update_lambda_memory") or 512

        # Create the Lambda function to update other functions
        update_lambda_function = aws.lambda_.Function(
            f"{self.name}-update-lambda-functions",
            runtime="python3.12",
            architectures=["arm64"],
            role=update_lambda_function_role.arn,
            handler="handler.lambda_handler",
            code=pulumi.AssetArchive(
                {
                    ".": pulumi.FileArchive(
                        os.path.join(
                            PROJECT_DIR, "infra", "update_lambda_functions"
                        )
                    )
                }
            ),
            timeout=lambda_timeout,
            memory_size=lambda_memory,
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "STACK_NAME": pulumi.get_stack(),
                },
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for publish_layer_lambda
        publish_layer_function_role = aws.iam.Role(
            f"{self.name}-publish-layer-role",
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution permissions
        aws.iam.RolePolicyAttachment(
            f"{self.name}-publish-layer-basic-execution",
            role=publish_layer_function_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach permissions needed to publish Lambda layers and read from S3
        aws.iam.RolePolicy(
            f"{self.name}-publish-layer-policy",
            role=publish_layer_function_role.id,
            policy=pulumi.Output.all(
                build_bucket.bucket, self.layer_name
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
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
                                "Action": ["s3:GetObject"],
                                "Resource": f"arn:aws:s3:::{args[0]}/{args[1]}/layer.zip",
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create publish_layer_lambda function
        publish_layer_lambda = aws.lambda_.Function(
            f"{self.name}-publish-layer",
            runtime="python3.12",
            architectures=["arm64"],
            role=publish_layer_function_role.arn,
            handler="publish_handler.lambda_handler",
            code=pulumi.AssetArchive(
                {
                    ".": pulumi.FileArchive(
                        os.path.join(PROJECT_DIR, "infra", "publish_layer")
                    )
                }
            ),
            timeout=300,
            memory_size=512,
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "BUCKET_NAME": build_bucket.bucket,
                    "LAYER_NAME": self.layer_name,
                    "LAYER_DESCRIPTION": self.description,
                    "COMPATIBLE_ARCHITECTURES": "x86_64,arm64",
                },
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Update state_machine_definition with PublishNewLayer state
        state_machine_definition = pulumi.Output.all(
            codebuild_project_name=codebuild_project.name,
            publish_layer_lambda_name=publish_layer_lambda.name,
            update_lambda_function_name=update_lambda_function.name,
        ).apply(
            lambda args: json.dumps(
                {
                    "Comment": f"Build {self.name} Lambda Layer, Publish and Update Lambda Functions",
                    "StartAt": "TriggerCodeBuild",
                    "States": {
                        "TriggerCodeBuild": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:codebuild:startBuild",
                            "Parameters": {
                                "ProjectName": args["codebuild_project_name"]
                            },
                            "Next": "WaitForBuild",
                        },
                        "WaitForBuild": {
                            "Type": "Wait",
                            "Seconds": 60,
                            "Next": "CheckBuildStatus",
                        },
                        "CheckBuildStatus": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:codebuild:batchGetBuilds",
                            "Parameters": {
                                "Ids.$": "States.Array($.Build.Id)"
                            },
                            "Next": "BuildSucceeded?",
                        },
                        "BuildSucceeded?": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "Variable": "$.Builds[0].BuildStatus",
                                    "StringEquals": "SUCCEEDED",
                                    "Next": "PublishNewLayer",
                                },
                                {
                                    "Variable": "$.Builds[0].BuildStatus",
                                    "StringEquals": "FAILED",
                                    "Next": "BuildFailed",
                                },
                            ],
                            "Default": "WaitForBuild",
                        },
                        "PublishNewLayer": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args[
                                    "publish_layer_lambda_name"
                                ],
                                "Payload": {},
                            },
                            "Next": "UpdateLambdaFunctions",
                        },
                        "UpdateLambdaFunctions": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args[
                                    "update_lambda_function_name"
                                ],
                                "Payload": {
                                    "layer_arn.$": "$.Payload.LayerVersionArn"
                                },
                            },
                            "End": True,
                        },
                        "BuildFailed": {
                            "Type": "Fail",
                            "Cause": "CodeBuild failed.",
                            "Error": "CodeBuildFailure",
                        },
                    },
                }
            )
        )

        # Create the state machine
        state_machine = aws.sfn.StateMachine(
            f"{self.name}-build-state-machine",
            role_arn=step_functions_role.arn,
            definition=state_machine_definition,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # ------------------------------------------------------------------
        # S3 -> SQS notification (no CloudTrail needed in provider v6.78)
        # ------------------------------------------------------------------
        event_queue = aws.sqs.Queue(
            f"{self.name}-source-upload-queue",
            visibility_timeout_seconds=60,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Allow S3 bucket to send messages to the SQS queue
        queue_policy = aws.sqs.QueuePolicy(
            f"{self.name}-queue-policy",
            queue_url=event_queue.id,
            policy=pulumi.Output.all(event_queue.arn, build_bucket.arn).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "AllowS3SendMessage",
                                "Effect": "Allow",
                                "Principal": {"Service": "s3.amazonaws.com"},
                                "Action": "SQS:SendMessage",
                                "Resource": arns[0],
                                "Condition": {
                                    "ArnEquals": {"aws:SourceArn": arns[1]}
                                },
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Add bucket notification to send ObjectCreated events to SQS
        aws.s3.BucketNotification(
            f"{self.name}-sqs-notification",
            bucket=build_bucket.id,
            queues=[
                aws.s3.BucketNotificationQueueArgs(
                    queue_arn=event_queue.arn,
                    events=["s3:ObjectCreated:*"],
                    filter_prefix=f"{self.name}/",
                    filter_suffix="source.zip",
                )
            ],
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[event_queue, queue_policy]
            ),
        )

        # ------------------------------------------------------------------
        # Lambda that reads SQS messages and starts the Step Function
        # ------------------------------------------------------------------
        trigger_lambda_role = aws.iam.Role(
            f"{self.name}-trigger-role",
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{self.name}-trigger-basic-exec",
            role=trigger_lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Allow Lambda to start executions of the state machine
        aws.iam.RolePolicy(
            f"{self.name}-trigger-start-exec",
            role=trigger_lambda_role.id,
            policy=state_machine.arn.apply(
                lambda sm_arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["states:StartExecution"],
                                "Resource": sm_arn,
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Allow the trigger Lambda to receive and delete messages from the queue
        aws.iam.RolePolicy(
            f"{self.name}-trigger-sqs-access",
            role=trigger_lambda_role.id,
            policy=event_queue.arn.apply(
                lambda qarn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": qarn,
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        trigger_lambda = aws.lambda_.Function(
            f"{self.name}-sqs-trigger",
            runtime="python3.12",
            architectures=["arm64"],
            role=trigger_lambda_role.arn,
            handler="index.handler",
            code=pulumi.AssetArchive(
                {
                    "index.py": pulumi.StringAsset(
                        """
import os, json, boto3

sf = boto3.client("stepfunctions")
SM_ARN = os.environ["STATE_MACHINE_ARN"]

def handler(event, _):
    for record in event["Records"]:
        body = json.loads(record["body"])
        sf.start_execution(stateMachineArn=SM_ARN, input=json.dumps(body))
    return {"status": "started"}
"""
                    )
                }
            ),
            timeout=60,
            memory_size=128,
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={"STATE_MACHINE_ARN": state_machine.arn}
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # SQS event source mapping to the Lambda
        aws.lambda_.EventSourceMapping(
            f"{self.name}-sqs-event-source",
            event_source_arn=event_queue.arn,
            function_name=trigger_lambda.name,
            batch_size=1,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach policy allowing EventBridge to start CodeBuild project
        aws.iam.RolePolicy(
            f"{self.name}-eventbridge-policy",
            role=self.eventbridge_role.id,
            policy=pulumi.Output.all(
                codebuild_project.arn, state_machine.arn
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["codebuild:StartBuild"],
                                "Resource": arns[0],
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["states:StartExecution"],
                                "Resource": arns[1],
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Export the state machine ARN
        pulumi.export(f"{self.name}_state_machine_arn", state_machine.arn)

        # NOTE: EventBridge rule removed – S3 -> SQS -> Lambda now handles triggering.


# Define the layers to build
layers_to_build = [
    {
        "package_dir": "receipt_dynamo",
        "name": "receipt-dynamo",
        "description": "DynamoDB layer for receipt-dynamo",
        "python_versions": ["3.12"],
    },
    {
        "package_dir": "receipt_label",
        "name": "receipt-label",
        "description": "Label layer for receipt-label",
        "python_versions": ["3.12"],
    },
    {
        "package_dir": "receipt_upload",
        "name": "receipt-upload",
        "description": "Upload layer for receipt-upload",
        "python_versions": ["3.12"],
    },
    # Add more layers here as needed
]

# Create Lambda layers using CodeBuild
lambda_layers = {}

for layer_config in layers_to_build:
    # Create the Lambda Layer resource using the enhanced component
    lambda_layer = LambdaLayer(
        name=layer_config["name"],
        package_dir=layer_config["package_dir"],
        python_versions=layer_config["python_versions"],
        description=layer_config["description"],
    )

    lambda_layers[layer_config["name"]] = lambda_layer

# Access the built layers by name
dynamo_layer = lambda_layers["receipt-dynamo"]
label_layer = lambda_layers["receipt-label"]
upload_layer = lambda_layers["receipt-upload"]

# Export the layer ARNs for reference
pulumi.export("dynamo_layer_arn", dynamo_layer.arn)
pulumi.export("label_layer_arn", label_layer.arn)
pulumi.export("upload_layer_arn", upload_layer.arn)

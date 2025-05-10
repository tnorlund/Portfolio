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

  - **Automated Updates of Dependent Lambda Functions:** Once a new layer version is published, the component’s
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

import os
import pulumi
import pulumi_aws as aws
import pulumi_command as command
import json
import hashlib
import glob
import sys
import time
import tempfile
import shutil

from pulumi import ComponentResource, Output

# Constants
PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)  # Now points to the root directory
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
        python_version: str,
        description: str = None,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__(f"lambda-layer:{name}", name, {}, opts)

        self.name = name
        self.package_dir = package_dir
        self.python_version = python_version
        self.description = description or f"Automatically built Lambda layer for {name}"
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
            raise ValueError(f"Package directory {package_path} does not exist")

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
        python_files = glob.glob(os.path.join(package_path, "**/*.py"), recursive=True)
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
            f"{self.name}-build-artifacts",
            bucket=f"lambda-layer-{self.name}-artifacts",
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
                lambda args: self._generate_upload_script(
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
                            "Principal": {"Service": "codebuild.amazonaws.com"},
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
            lambda _: f"arn:aws:lambda:*:*:layer:{self.name}:*"
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
                                    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*"
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
                                "Action": ["lambda:UpdateFunctionConfiguration"],
                                "Resource": f"arn:aws:lambda:*:*:function:*",
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
                        name="LAYER_NAME", value=self.name
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PACKAGE_DIR", value="source"
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PYTHON_VERSION",
                        value=(
                            self.python_version[0]
                            if isinstance(self.python_version, list)
                            else self.python_version
                        ),
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
            create=pulumi.Output.all(build_bucket.bucket, codebuild_project.name).apply(
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
            layer_name=self.name,
            compatible_runtimes=[f"python{self.python_version[0]}"],
            compatible_architectures=["x86_64", "arm64"],
            description=self.description,
            s3_bucket=build_bucket.bucket,
            s3_key=f"{self.name}/layer.zip",
            opts=pulumi.ResourceOptions(depends_on=[initial_sync_build], parent=self),
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

        # Setup CloudTrail for logging PutObject events
        cloudtrail_bucket = aws.s3.Bucket(
            f"{self.name}-cloudtrail-logs",
            bucket=f"{self.name}-cloudtrail-logs",
            force_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        trail = aws.cloudtrail.Trail(
            f"{self.name}-trail",
            s3_bucket_name=cloudtrail_bucket.id,
            include_global_service_events=False,
            is_multi_region_trail=False,
            enable_logging=True,
            event_selectors=[
                aws.cloudtrail.TrailEventSelectorArgs(
                    read_write_type="WriteOnly",
                    include_management_events=True,
                    data_resources=[
                        aws.cloudtrail.TrailEventSelectorDataResourceArgs(
                            type="AWS::S3::Object",
                            values=[build_bucket.arn.apply(lambda arn: f"{arn}/")],
                        ),
                    ],
                ),
            ],
            opts=pulumi.ResourceOptions(parent=self),
        )

        cloudtrail_bucket_policy = aws.s3.BucketPolicy(
            f"{self.name}-cloudtrail-logs-policy",
            bucket=cloudtrail_bucket.id,
            policy=pulumi.Output.all(cloudtrail_bucket.bucket).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "AWSCloudTrailAclCheck",
                                "Effect": "Allow",
                                "Principal": {"Service": "cloudtrail.amazonaws.com"},
                                "Action": "s3:GetBucketAcl",
                                "Resource": f"arn:aws:s3:::{args[0]}",
                            },
                            {
                                "Sid": "AWSCloudTrailWrite",
                                "Effect": "Allow",
                                "Principal": {"Service": "cloudtrail.amazonaws.com"},
                                "Action": "s3:PutObject",
                                "Resource": f"arn:aws:s3:::{args[0]}/AWSLogs/{aws.get_caller_identity().account_id}/*",
                                "Condition": {
                                    "StringEquals": {
                                        "s3:x-amz-acl": "bucket-owner-full-control"
                                    }
                                },
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Set up Step Functions for orchestration
        self._setup_step_functions(codebuild_project, build_bucket)

    def _get_buildspec(self):
        """Generate the buildspec.yml content for CodeBuild."""
        primary_python_version = (
            self.python_version[0]
            if isinstance(self.python_version, list)
            else self.python_version
        )

        return {
            "version": 0.2,
            "phases": {
                "install": {
                    "runtime-versions": {"python": primary_python_version},
                    "commands": [
                        "echo Installing dependencies...",
                        "yum install -y libjpeg-devel zlib-devel",
                        "pip install --upgrade pip",
                        "pip install build",
                    ],
                },
                "build": {
                    "commands": [
                        "echo Cleaning build directory...",
                        "rm -rf build",
                        "mkdir -p build/python/lib/python${PYTHON_VERSION}/site-packages",
                        "echo Build directory created. Listing build/:",
                        "find build -maxdepth 2",
                        "echo Building the package wheel...",
                        "python -m build --wheel --outdir dist/",
                        "echo Wheel built. Listing contents of dist/:",
                        "ls -l dist/",
                        "echo Installing the built wheel into the correct directory...",
                        "pip install dist/*.whl -t build/python/lib/python${PYTHON_VERSION}/site-packages",
                        "echo Installation complete. Listing installed files:",
                        "find build/python -type f",
                        "chmod -R 755 build/python",
                        "echo Listing build directory:",
                        "ls -la build",
                    ],
                },
            },
            "artifacts": {
                "files": ["python/**/*"],
                "base-directory": "build",
            },
        }

    def _generate_upload_script(self, bucket, package_path, package_hash):
        """Generate a bash script that uses tempfile for secure temporary directories."""
        return f"""
        # Create a unique temporary directory
        TMP_DIR=$(mktemp -d)
        
        # Ensure cleanup on exit
        trap 'rm -rf "$TMP_DIR"' EXIT
        
        # Copy the package files
        cp -r {package_path}/* "$TMP_DIR/"
        
        # Create the zip file
        pushd "$TMP_DIR" > /dev/null
        zip -r "$TMP_DIR/source.zip" .
        popd > /dev/null
        
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
            runtime="python3.13",
            role=update_lambda_function_role.arn,
            handler="handler.lambda_handler",
            code=pulumi.AssetArchive(
                {
                    ".": pulumi.FileArchive(
                        os.path.join(PROJECT_DIR, "infra", "update_lambda_functions")
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
            policy=pulumi.Output.all(build_bucket.bucket, self.name).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:PublishLayerVersion"],
                                "Resource": f"arn:aws:lambda:{aws.config.region}:{aws.get_caller_identity().account_id}:layer:{args[1]}",
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
            runtime="python3.13",
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
                    "LAYER_NAME": self.name,
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
                            "Parameters": {"Ids.$": "States.Array($.Build.Id)"},
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
                                "FunctionName": args["publish_layer_lambda_name"],
                                "Payload": {},
                            },
                            "Next": "UpdateLambdaFunctions",
                        },
                        "UpdateLambdaFunctions": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args["update_lambda_function_name"],
                                "Payload": {"layer_arn.$": "$.Payload.LayerVersionArn"},
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

        # Attach policy allowing EventBridge to start CodeBuild project
        aws.iam.RolePolicy(
            f"{self.name}-eventbridge-policy",
            role=self.eventbridge_role.id,
            policy=pulumi.Output.all(codebuild_project.arn, state_machine.arn).apply(
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

        # Add EventBridge rule to trigger the Step Function on successful CodeBuild completion
        event_rule = aws.cloudwatch.EventRule(
            f"{self.name}-source-upload-trigger",
            description=f"Trigger Step Function for {self.name} on source upload",
            event_pattern=pulumi.Output.all(build_bucket.bucket).apply(
                lambda bucket: json.dumps(
                    {
                        "source": ["aws.s3"],
                        "detail-type": ["AWS API Call via CloudTrail"],
                        "detail": {
                            "eventSource": ["s3.amazonaws.com"],
                            "eventName": [
                                "PutObject",
                                "CompleteMultipartUpload",
                            ],
                            "requestParameters": {
                                "bucketName": bucket,
                                "key": [f"{self.name}/source.zip"],
                            },
                        },
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # EventBridge rule to trigger the Step Functions state machine after successful build
        aws.cloudwatch.EventTarget(
            f"{self.name}-state-machine-trigger",
            rule=event_rule.name,
            arn=state_machine.arn,
            role_arn=self.eventbridge_role.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )


# Define the layers to build
layers_to_build = [
    {
        "package_dir": "receipt_dynamo",
        "name": "receipt-dynamo",
        "description": "DynamoDB layer for receipt-dynamo",
        "python_version": ["3.12"],
    },
    {
        "package_dir": "receipt_label",
        "name": "receipt-label",
        "description": "Label layer for receipt-label",
        "python_version": ["3.12"],
    },
    # Add more layers here as needed
]

# Create Lambda layers using CodeBuild
lambda_layers = {}

for layer_config in layers_to_build:
    print(f"Creating Lambda Layer using CodeBuild: {layer_config['name']}")

    # Create the Lambda Layer resource using the enhanced component
    lambda_layer = LambdaLayer(
        name=layer_config["name"],
        package_dir=layer_config["package_dir"],
        python_version=layer_config["python_version"],
        description=layer_config["description"],
    )

    lambda_layers[layer_config["name"]] = lambda_layer
    print(f"Lambda Layer resource created for: {layer_config['name']}")

# Access the built layers by name
dynamo_layer = lambda_layers["receipt-dynamo"]
label_layer = lambda_layers["receipt-label"]

# Export the layer ARNs for reference
pulumi.export("dynamo_layer_arn", dynamo_layer.arn)
pulumi.export("label_layer_arn", label_layer.arn)

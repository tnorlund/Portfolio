"""Pulumi component for building and deploying ML packages using AWS CodeBuild."""

import os
import json
import hashlib
import tempfile
import pulumi
import pulumi_command as command
import pulumi_aws as aws
from typing import Dict, List, Optional, Any


class MLPackageBuilder(pulumi.ComponentResource):
    """A Pulumi component that builds and manages ML packages using AWS CodeBuild.

    This component is responsible for:
    1. Checking if packages need to be rebuilt
    2. Building packages with the right environment in AWS CodeBuild
    3. Deploying packages to EFS via CodeBuild
    4. Tracking build state across Pulumi runs
    """

    def __init__(
        self,
        name: str,
        packages: List[str],
        efs_storage_id: Optional[pulumi.Input[str]] = None,
        python_version: str = "3.9",
        cuda_version: str = "11.7",
        force_rebuild: bool = False,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        """Initialize the ML package builder.

        Args:
            name: The unique name for this component
            packages: List of package directories to build
            efs_storage_id: EFS volume ID for deployment (optional)
            python_version: Python version to use for building
            cuda_version: CUDA version to use for building
            force_rebuild: Force rebuilding packages
            opts: Resource options for this component
        """
        super().__init__("custom:ml:PackageBuilder", name, {}, opts)

        # Save input parameters
        self.packages = packages
        self.efs_storage_id = efs_storage_id
        self.python_version = python_version
        self.cuda_version = cuda_version
        self.force_rebuild = force_rebuild

        # Get stack name
        self.stack = pulumi.get_stack()

        # Get project root directory
        self.project_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )

        # Create an S3 bucket for package artifacts and build state
        self.artifact_bucket = aws.s3.Bucket(
            f"{name}-artifacts",
            acl="private",
            force_destroy=True,
            tags={
                "Name": f"{name}-artifacts",
                "Environment": self.stack,
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create IAM role for CodeBuild
        codebuild_role = aws.iam.Role(
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
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach policies to the CodeBuild role
        codebuild_policy = aws.iam.Policy(
            f"{name}-codebuild-policy",
            policy=pulumi.Output.all(
                bucket_arn=self.artifact_bucket.arn, efs_id=efs_storage_id
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # S3 permissions
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args["bucket_arn"],
                                    f"{args['bucket_arn']}/*",
                                ],
                            },
                            # CloudWatch Logs permissions
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogGroup",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": ["*"],
                            },
                            # EFS permissions (if provided)
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "elasticfilesystem:DescribeFileSystems",
                                    "elasticfilesystem:DescribeMountTargets",
                                    "elasticfilesystem:ClientMount",
                                    "elasticfilesystem:ClientWrite",
                                ],
                                "Resource": (
                                    [
                                        f"arn:aws:elasticfilesystem:*:*:file-system/{args['efs_id']}"
                                    ]
                                    if args["efs_id"]
                                    else ["*"]
                                ),
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        codebuild_policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-codebuild-policy-attachment",
            role=codebuild_role.name,
            policy_arn=codebuild_policy.arn,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Process each package
        built_packages = []
        for package in packages:
            # Upload package source to S3 first
            package_dir = os.path.join(self.project_dir, package)
            if not os.path.isdir(package_dir):
                raise ValueError(
                    f"Package directory '{package_dir}' does not exist"
                )

            # Create a command to zip and upload the package source to S3
            upload_command = command.local.Command(
                f"{name}-{package}-upload",
                create=(
                    f"cd {self.project_dir} && "
                    f"zip -r {package}.zip {package}/* && "
                    f"aws s3 cp {package}.zip s3://${{bucket_name}}/source/{package}.zip && "
                    f"rm {package}.zip"
                ),
                environment={
                    "bucket_name": self.artifact_bucket.bucket,
                },
                opts=pulumi.ResourceOptions(parent=self),
            )

            # Create a CodeBuild project for each package
            buildspec = pulumi.Output.all(
                bucket_name=self.artifact_bucket.bucket,
                efs_id=efs_storage_id,
                package=package,
                stack=self.stack,
                python_version=python_version,
                cuda_version=cuda_version,
                force_rebuild=force_rebuild,
            ).apply(self._create_buildspec)

            codebuild_project = aws.codebuild.Project(
                f"{name}-{package}",
                artifacts=aws.codebuild.ProjectArtifactsArgs(
                    type="S3",
                    location=self.artifact_bucket.bucket,
                    path=f"{package}/output",
                    packaging="ZIP",
                    name=f"{package}.zip",
                ),
                environment=aws.codebuild.ProjectEnvironmentArgs(
                    type="LINUX_GPU_CONTAINER",  # Use GPU environment
                    image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                    compute_type="BUILD_GENERAL1_LARGE",
                    privileged_mode=True,  # Required for Docker
                    environment_variables=[
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="PACKAGE_NAME",
                            value=package,
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="PYTHON_VERSION",
                            value=python_version,
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="CUDA_VERSION",
                            value=cuda_version,
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="STACK",
                            value=self.stack,
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="FORCE_REBUILD",
                            value=str(force_rebuild).lower(),
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="S3_BUCKET",
                            value=self.artifact_bucket.bucket,
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="EFS_ID",
                            value=efs_storage_id or "",
                        ),
                    ],
                ),
                service_role=codebuild_role.arn,
                source=aws.codebuild.ProjectSourceArgs(
                    type="NO_SOURCE",  # No external source, using buildspec directly
                    buildspec=buildspec,
                ),
                build_timeout=60,  # 60 minutes timeout
                cache=aws.codebuild.ProjectCacheArgs(
                    type="S3",
                    location=pulumi.Output.concat(
                        self.artifact_bucket.bucket, "/cache/", package
                    ),
                ),
                description=f"Build ML package {package} with CUDA support",
                logs_config=aws.codebuild.ProjectLogsConfigArgs(
                    cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                        group_name=f"/aws/codebuild/{name}-{package}",
                        stream_name="build-log",
                    ),
                ),
                tags={
                    "Name": f"{name}-{package}",
                    "Environment": self.stack,
                    "ManagedBy": "Pulumi",
                },
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[codebuild_policy_attachment],
                ),
            )

            built_packages.append(codebuild_project)

        # Register outputs
        self.register_outputs(
            {
                "packages": packages,
                "python_version": python_version,
                "cuda_version": cuda_version,
                "force_rebuild": force_rebuild,
                "artifact_bucket": self.artifact_bucket.bucket,
            }
        )

    def _create_buildspec(self, args):
        """Create a buildspec for AWS CodeBuild."""
        buildspec = {
            "version": "0.2",
            "phases": {
                "install": {
                    "runtime-versions": {"python": args["python_version"]},
                    "commands": [
                        "echo Installing dependencies...",
                        "apt-get update",
                        "apt-get install -y build-essential curl unzip git",
                        # Install CUDA Toolkit
                        f"wget https://developer.download.nvidia.com/compute/cuda/{args['cuda_version']}/local_installers/cuda-repo-ubuntu2204-{args['cuda_version']}-local_11.5.0-1_amd64.deb",
                        f"dpkg -i cuda-repo-ubuntu2204-{args['cuda_version']}-local_11.5.0-1_amd64.deb",
                        "cp /var/cuda-repo-ubuntu2204-11.5.0-local/cuda-*-keyring.gpg /usr/share/keyrings/",
                        "apt-get update",
                        f"apt-get -y install cuda-toolkit-{args['cuda_version'].replace('.', '-')}",
                        # Install Python dependencies
                        "pip install --upgrade pip",
                        "pip install boto3 awscli",
                    ],
                },
                "pre_build": {
                    "commands": [
                        "echo Checking if rebuild is needed...",
                        "mkdir -p /tmp/source/{args['package']}",
                        "mkdir -p /tmp/cache",
                        "mkdir -p /tmp/output",
                        # Download package source from S3
                        f"aws s3 cp s3://{args['bucket_name']}/source/{args['package']}.zip /tmp/{args['package']}.zip",
                        f"unzip /tmp/{args['package']}.zip -d /tmp/source/",
                        f"rm /tmp/{args['package']}.zip",
                        # Download previous build state if exists
                        f"aws s3 cp s3://{args['bucket_name']}/state/{args['package']}.json /tmp/cache/state.json || echo 'No previous build state'",
                    ]
                },
                "build": {
                    "commands": [
                        "echo Building ML package...",
                        f"cd /tmp/source/{args['package']}",
                        "pip install -r requirements.txt",
                        "pip install -e .",
                        # Install the package to the output directory
                        f"pip install -e . --target /tmp/output/python",
                        f"cp requirements.txt /tmp/output/",
                        # Calculate hash and update build state
                        'HASH=$(find . -type f \\( -name "*.py" -o -name "*.txt" -o -name "*.yml" -o -name "*.yaml" -o -name "*.json" \\) | sort | xargs cat | sha256sum | cut -d \' \' -f 1)',
                        "BUILD_TIME=$(date +%s)",
                        # Create JSON state file using a different approach
                        f"cat > /tmp/cache/state.json << EOF\n{{\"package_name\": \"{args['package']}\", \"build_timestamp\": $(date +%s), \"build_date\": \"$(date -Iseconds)\", \"python_version\": \"{args['python_version']}\", \"cuda_version\": \"{args['cuda_version']}\", \"source_hash\": \"$HASH\"}}\nEOF",
                    ]
                },
                "post_build": {
                    "commands": [
                        "echo Uploading build artifacts...",
                        # Upload build state
                        f"aws s3 cp /tmp/cache/state.json s3://{args['bucket_name']}/state/{args['package']}.json",
                        # Upload build output
                        f"aws s3 cp /tmp/output s3://{args['bucket_name']}/output/{args['package']} --recursive",
                        # Handle EFS deployment if EFS ID is provided
                        f"if [ ! -z '{args['efs_id']}' ]; then "
                        + "  echo Deploying to EFS... && "
                        + f"  mkdir -p /mnt/efs && "
                        + f"  mount -t efs {args['efs_id']}:/ /mnt/efs && "
                        + f"  mkdir -p /mnt/efs/packages/{args['package']} && "
                        + f"  cp -r /tmp/output/* /mnt/efs/packages/{args['package']}/ && "
                        + "  echo 'Successfully deployed to EFS'; "
                        + "else "
                        + "  echo 'Skipping EFS deployment (no EFS ID provided)'; "
                        + "fi",
                    ]
                },
            },
            "artifacts": {
                "files": ["**/*"],
                "base-directory": "/tmp/output",
                "discard-paths": "no",
            },
            "cache": {"paths": ["/tmp/cache/**/*"]},
        }

        return json.dumps(buildspec)

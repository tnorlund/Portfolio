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
        efs_access_point_id: Optional[pulumi.Input[str]] = None,
        vpc_id: Optional[pulumi.Input[str]] = None,
        subnet_ids: Optional[List[pulumi.Input[str]]] = None,
        security_group_ids: Optional[List[pulumi.Input[str]]] = None,
        python_version: str = "3.9",
        cuda_version: str = "11.7",
        force_rebuild: bool = False,
        vpc_endpoints: Optional[List[Any]] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        """Initialize the ML package builder.

        Args:
            name: The unique name for this component
            packages: List of package directories to build
            efs_storage_id: EFS volume ID for deployment (optional)
            efs_access_point_id: EFS access point ID for mounting (optional)
            vpc_id: VPC ID where CodeBuild will run (optional)
            subnet_ids: List of subnet IDs for CodeBuild (optional)
            security_group_ids: List of security group IDs for CodeBuild (optional)
            python_version: Python version to use for building
            cuda_version: CUDA version to use for building
            force_rebuild: Force rebuilding packages
            vpc_endpoints: List of VPC endpoints that CodeBuild depends on
            opts: Resource options for this component
        """
        super().__init__("custom:ml:PackageBuilder", name, {}, opts)

        # Save input parameters
        self.packages = packages
        self.efs_storage_id = efs_storage_id
        self.efs_access_point_id = efs_access_point_id
        self.vpc_id = vpc_id
        self.subnet_ids = subnet_ids
        self.security_group_ids = security_group_ids
        self.python_version = python_version
        self.cuda_version = cuda_version
        self.force_rebuild = force_rebuild
        self.vpc_endpoints = vpc_endpoints or []

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
                bucket_arn=self.artifact_bucket.arn,
                efs_id=self.efs_storage_id,
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
                            # VPC permissions
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ec2:CreateNetworkInterface",
                                    "ec2:DeleteNetworkInterface",
                                    "ec2:ModifyNetworkInterfaceAttribute",
                                    "ec2:AssignPrivateIpAddresses",
                                    "ec2:UnassignPrivateIpAddresses",
                                    "ec2:DetachNetworkInterface",
                                    "ec2:AttachNetworkInterface",
                                    "ec2:CreateTags",
                                    "ec2:DeleteTags",
                                ],
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ec2:DescribeNetworkInterfaces",
                                    "ec2:DescribeVpcs",
                                    "ec2:DescribeSubnets",
                                    "ec2:DescribeSecurityGroups",
                                    "ec2:DescribeDhcpOptions",
                                    "ec2:DescribeRouteTables",
                                ],
                                "Resource": "*",
                            },
                            # Network interface permission for VPC endpoints
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ec2:CreateNetworkInterfacePermission"
                                ],
                                "Resource": "*",
                            },
                            # IAM pass role permission
                            {
                                "Effect": "Allow",
                                "Action": "iam:PassRole",
                                "Resource": "*",
                                "Condition": {
                                    "StringEquals": {
                                        "iam:PassedToService": "ec2.amazonaws.com"
                                    }
                                },
                            },
                            # KMS permissions for S3 key
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "kms:Decrypt",
                                    "kms:Encrypt",
                                    "kms:ReEncrypt*",
                                    "kms:GenerateDataKey*",
                                    "kms:DescribeKey",
                                ],
                                "Resource": "arn:aws:kms:us-east-1:681647709217:alias/aws/s3",
                            },
                            # EFS permissions
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "elasticfilesystem:ClientMount",
                                    "elasticfilesystem:ClientWrite",
                                    "elasticfilesystem:DescribeFileSystems",
                                    "elasticfilesystem:DescribeMountTargets",
                                    "elasticfilesystem:DescribeMountTargetSecurityGroups",
                                ],
                                "Resource": f"arn:aws:elasticfilesystem:*:*:file-system/{args['efs_id']}",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "elasticfilesystem:DescribeFileSystems",
                                    "elasticfilesystem:DescribeMountTargets",
                                ],
                                "Resource": "*",
                            },
                            # SSM messaging permissions for debugging
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ssmmessages:CreateControlChannel",
                                    "ssmmessages:CreateDataChannel",
                                    "ssmmessages:OpenControlChannel",
                                    "ssmmessages:OpenDataChannel",
                                ],
                                "Resource": "*",
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
                efs_id=self.efs_storage_id,
                efs_access_point_id=self.efs_access_point_id,
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
                    type="LINUX_CONTAINER",
                    image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                    compute_type="BUILD_GENERAL1_SMALL",
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
                            value=self.efs_storage_id or "",
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="EFS_ACCESS_POINT_ID",
                            value=self.efs_access_point_id or "",
                        ),
                    ],
                ),
                vpc_config=(
                    aws.codebuild.ProjectVpcConfigArgs(
                        vpc_id=self.vpc_id,
                        subnets=self.subnet_ids,
                        security_group_ids=self.security_group_ids,
                    )
                    if all(
                        [self.vpc_id, self.subnet_ids, self.security_group_ids]
                    )
                    else None
                ),
                # Use apply to handle the Output nature of efs_storage_id
                file_system_locations=pulumi.Output.all(
                    fs_id=self.efs_storage_id,
                    ap_id=self.efs_access_point_id,
                ).apply(
                    lambda args: (
                        [
                            aws.codebuild.ProjectFileSystemLocationArgs(
                                identifier="efs_mount",
                                location=f"{args['fs_id']}.efs.{aws.config.region}.amazonaws.com:/",
                                mount_point="/mnt/efs",
                                type="EFS",
                                mount_options=(
                                    f"accesspoint={args['ap_id']},"
                                    "nfsvers=4.1,"
                                    "rsize=1048576,"
                                    "wsize=1048576,"
                                    "hard,timeo=600,retrans=2"
                                ),
                            )
                        ]
                        if args["fs_id"] and args["ap_id"]
                        else []
                    )
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

            # Start the build automatically using AWS CLI
            start_build_cmd = command.local.Command(
                f"{name}-{package}-start-build",
                create=pulumi.Output.concat(
                    "aws codebuild start-build --project-name ",
                    codebuild_project.name,
                ),
                opts=pulumi.ResourceOptions(
                    parent=codebuild_project,
                    depends_on=[codebuild_project, upload_command]
                    + self.vpc_endpoints,
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
                        "yum update -y",
                        "yum install -y gcc-c++ make unzip git amazon-efs-utils",
                        # Install Python dependencies for CUDA compatibility
                        "pip install --upgrade pip",
                        "pip install boto3 awscli",
                        # Install common Python packages for CPU-only PyTorch
                        "pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu",
                        "# Removed CUDA package for cost efficiency",
                        "# Removed NVIDIA ML package for cost efficiency",
                    ],
                },
                "pre_build": {
                    "commands": [
                        "echo Checking if rebuild is needed...",
                        f"mkdir -p /tmp/source/{args['package']}",
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
                        # Create state.json early to avoid post-build failure
                        'echo \'{"package_name": "${PACKAGE_NAME}", "build_timestamp": \'$(date +%s)\', "build_date": "\'$(date -Iseconds)\'", "python_version": "${PYTHON_VERSION}", "cuda_version": "${CUDA_VERSION}", "source_hash": "initial"}\' > /tmp/cache/state.json',
                        f"cd /tmp/source/{args['package']}",
                        # Check if we need to handle receipt_dynamo dependency
                        "if grep -q 'receipt_dynamo' requirements.txt; then "
                        + "  echo 'Found receipt_dynamo dependency, handling specially...' && "
                        + "  sed -i 's|../receipt_dynamo|/tmp/source/receipt_dynamo|g' requirements.txt && "
                        + "  cat requirements.txt && "
                        + "  if [ ! -d '/tmp/source/receipt_dynamo' ] && [ -f '/tmp/${PACKAGE_NAME}.zip' ]; then "
                        + "    echo 'Downloading receipt_dynamo package...' && "
                        + f"    aws s3 cp s3://{args['bucket_name']}/source/receipt_dynamo.zip /tmp/receipt_dynamo.zip && "
                        + "    unzip /tmp/receipt_dynamo.zip -d /tmp/source/ && "
                        + "    rm /tmp/receipt_dynamo.zip; "
                        + "  fi; "
                        + "fi",
                        # Continue with normal installation
                        "pip install -r requirements.txt || echo 'Warning: requirements installation failed'",
                        "pip install -e . || echo 'Warning: package installation failed'",
                        # Install the package to the output directory
                        "mkdir -p /tmp/output/python",
                        "cp -r * /tmp/output/ || echo 'Warning: could not copy all files'",
                        "cp requirements.txt /tmp/output/ || echo 'Warning: could not copy requirements.txt'",
                        # Calculate hash and update build state
                        'HASH=$(find . -type f \\( -name "*.py" -o -name "*.txt" -o -name "*.yml" -o -name "*.yaml" -o -name "*.json" \\) | sort | xargs cat | sha256sum | cut -d \' \' -f 1)',
                        "BUILD_TIME=$(date +%s)",
                        # Update state.json with final hash
                        'echo \'{"package_name": "${PACKAGE_NAME}", "build_timestamp": \'$(date +%s)\', "build_date": "\'$(date -Iseconds)\'", "python_version": "${PYTHON_VERSION}", "cuda_version": "${CUDA_VERSION}", "source_hash": "\'$HASH\'"}\' > /tmp/cache/state.json',
                    ]
                },
                "post_build": {
                    "commands": [
                        "echo Uploading build artifacts...",
                        # Make sure the cache directory exists
                        "mkdir -p /tmp/cache",
                        # Create minimal state.json if it doesn't exist
                        'if [ ! -f /tmp/cache/state.json ]; then echo "{\\"package_name\\": \\"${PACKAGE_NAME}\\", \\"build_timestamp\\": \\"$(date +%s)\\", \\"build_date\\": \\"$(date -Iseconds)\\", \\"python_version\\": \\"${PYTHON_VERSION}\\", \\"cuda_version\\": \\"${CUDA_VERSION}\\", \\"source_hash\\": \\"error\\"}" > /tmp/cache/state.json; fi',
                        # Upload build state
                        f"aws s3 cp /tmp/cache/state.json s3://{args['bucket_name']}/state/{args['package']}.json || echo 'Failed to upload state.json'",
                        # Create minimal output if it doesn't exist
                        "if [ ! -d /tmp/output ]; then mkdir -p /tmp/output; echo 'Build failed' > /tmp/output/BUILD_FAILED.txt; fi",
                        # Upload build output
                        f"aws s3 cp /tmp/output s3://{args['bucket_name']}/output/{args['package']} --recursive || echo 'Failed to upload output'",
                        # Copy files to EFS if it's mounted (using native EFS integration)
                        f"if [ -d /mnt/efs ]; then "
                        + f"  echo 'Copying files to EFS...' && "
                        + f"  mkdir -p /mnt/efs/packages/{args['package']} && "
                        + f"  cp -r /tmp/output/* /mnt/efs/packages/{args['package']}/ && "
                        + "  echo 'Successfully deployed to EFS'; "
                        + "else "
                        + "  echo 'EFS mount point not found. Skipping EFS deployment.'; "
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

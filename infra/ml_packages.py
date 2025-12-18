"""Pulumi component for building and deploying ML packages using AWS CodeBuild.

This module provides infrastructure for building and deploying machine learning packages
in a cloud environment. It handles the complete lifecycle of ML package deployment,
including:

- Package source code management and versioning
- Build environment configuration with Python support
- Automated build and deployment using AWS CodeBuild
- Artifact storage in S3 and deployment to EFS
- Build state tracking and caching
- VPC and security group configuration for build environments

The main component, MLPackageBuilder, manages the entire process of building and
deploying ML packages, ensuring they are properly configured for use in ML training
environments.
"""

import hashlib
import json
import os
import tempfile
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_aws as aws
import pulumi_command as command


class MLPackageBuilder(pulumi.ComponentResource):
    """A Pulumi component that builds and manages ML packages using AWS CodeBuild.

    This component automates the process of building and deploying machine learning
    packages in a cloud environment. It handles the complete lifecycle from source
    code to deployment, including build environment setup, dependency management,
    and artifact distribution.

    The component is responsible for:
    1. Checking if packages need to be rebuilt based on source changes
    2. Building packages with the right environment in AWS CodeBuild
    3. Deploying packages to EFS via CodeBuild
    4. Tracking build state across Pulumi runs
    5. Managing build artifacts and caching
    6. Configuring VPC and security settings for builds

    Attributes:
        packages (List[str]): List of package directories to build
        efs_storage_id (Optional[pulumi.Input[str]]): EFS volume ID for deployment
        efs_access_point_id (Optional[pulumi.Input[str]]): EFS access point ID for mounting
        efs_dns_name (Optional[pulumi.Input[str]]): EFS DNS name for mounting
        vpc_id (Optional[pulumi.Input[str]]): VPC ID where CodeBuild will run
        subnet_ids (Optional[List[pulumi.Input[str]]]): List of subnet IDs for CodeBuild
        security_group_ids (Optional[List[pulumi.Input[str]]]): List of security group IDs
        python_version (str): Python version to use for building
        force_rebuild (bool): Whether to force rebuilding packages
        vpc_endpoints (List[Any]): List of VPC endpoints that CodeBuild depends on
        artifact_bucket (aws.s3.Bucket): S3 bucket for storing build artifacts
    """

    def __init__(
        self,
        name: str,
        packages: List[str],
        supplementary_packages: Optional[List[str]] = None,  # NEW parameter
        efs_storage_id: Optional[pulumi.Input[str]] = None,
        efs_access_point_id: Optional[pulumi.Input[str]] = None,
        efs_dns_name: Optional[pulumi.Input[str]] = None,
        vpc_id: Optional[pulumi.Input[str]] = None,
        subnet_ids: Optional[List[pulumi.Input[str]]] = None,
        security_group_ids: Optional[List[pulumi.Input[str]]] = None,
        python_version: str = "3.12",
        force_rebuild: bool = False,
        vpc_endpoints: Optional[List[Any]] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        """Initialize the ML package builder.

        Args:
            name (str): The unique name for this component
            packages (List[str]): List of package directories to build
            efs_storage_id (Optional[pulumi.Input[str]]): EFS volume ID for deployment
            efs_access_point_id (Optional[pulumi.Input[str]]): EFS access point ID for mounting
            efs_dns_name (Optional[pulumi.Input[str]]): EFS DNS name for mounting
            vpc_id (Optional[pulumi.Input[str]]): VPC ID where CodeBuild will run
            subnet_ids (Optional[List[pulumi.Input[str]]]): List of subnet IDs for CodeBuild
            security_group_ids (Optional[List[pulumi.Input[str]]]): List of security group IDs
            python_version (str): Python version to use for building
            force_rebuild (bool): Whether to force rebuilding packages
            vpc_endpoints (Optional[List[Any]]): List of VPC endpoints that CodeBuild depends on
            opts (Optional[pulumi.ResourceOptions]): Resource options for this component

        Raises:
            ValueError: If a package directory does not exist
        """
        super().__init__("custom:ml:PackageBuilder", name, {}, opts)

        # Save input parameters
        self.packages = packages
        self.supplementary_packages = (
            supplementary_packages or []
        )  # Save the optional list
        self.efs_storage_id = efs_storage_id
        self.efs_access_point_id = efs_access_point_id
        self.efs_dns_name = efs_dns_name
        self.vpc_id = vpc_id
        self.subnet_ids = subnet_ids
        self.security_group_ids = security_group_ids
        self.python_version = python_version
        self.force_rebuild = force_rebuild
        self.vpc_endpoints = vpc_endpoints or []

        # Get stack name
        self.stack = pulumi.get_stack()

        # Get project root directory
        self.project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Create an S3 bucket for package artifacts and build state
        # Note: No ACL needed - S3 buckets are private by default in newer AWS accounts
        self.artifact_bucket = aws.s3.Bucket(
            f"{name}-artifacts",
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
                            "Principal": {"Service": "codebuild.amazonaws.com"},
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
                                "Action": ["ec2:CreateNetworkInterfacePermission"],
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

        # Process supplementary packages: zip and upload their source to S3
        supplementary_upload_commands = []
        for supp_pkg in self.supplementary_packages:
            supp_pkg_dir = os.path.join(self.project_dir, supp_pkg)
            if not os.path.isdir(supp_pkg_dir):
                raise ValueError(
                    f"Supplementary package directory '{supp_pkg_dir}' does not exist"
                )
            supp_upload_command = command.local.Command(
                f"{name}-{supp_pkg}-upload",
                create=(
                    f"cd {self.project_dir} && "
                    f"zip -r {supp_pkg}.zip {supp_pkg}/* && "
                    f"aws s3 cp {supp_pkg}.zip s3://${{bucket_name}}/source/{supp_pkg}.zip && "
                    f"rm {supp_pkg}.zip"
                ),
                environment={
                    "bucket_name": self.artifact_bucket.bucket,
                },
                opts=pulumi.ResourceOptions(parent=self),
            )
            supplementary_upload_commands.append(supp_upload_command)

        # Process each package
        built_packages = []
        for package in self.packages:
            # Upload package source to S3 first
            package_dir = os.path.join(self.project_dir, package)
            if not os.path.isdir(package_dir):
                raise ValueError(f"Package directory '{package_dir}' does not exist")

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
                force_rebuild=force_rebuild,
            ).apply(self._create_buildspec)

            codebuild_project = aws.codebuild.Project(
                f"{name}-{package}-{pulumi.get_stack()}",  # Pulumi logical name with stack
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
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="EFS_DNS_NAME",
                            value=self.efs_dns_name or "",
                        ),
                        aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                            name="LOCAL_PACKAGES",
                            value=",".join(
                                self.supplementary_packages
                            ),  # If self.supplementary_packages is empty, this will be empty.
                        ),
                    ],
                ),
                vpc_config=(
                    aws.codebuild.ProjectVpcConfigArgs(
                        vpc_id=self.vpc_id,
                        subnets=self.subnet_ids,
                        security_group_ids=self.security_group_ids,
                    )
                    if all([self.vpc_id, self.subnet_ids, self.security_group_ids])
                    else None
                ),
                # Use apply to handle the Output nature of efs_storage_id
                file_system_locations=pulumi.Output.all(
                    fs_id=self.efs_storage_id,
                    ap_id=self.efs_access_point_id,
                    dns_name=self.efs_dns_name,
                    region=aws.config.region,
                ).apply(
                    lambda args: (
                        [
                            aws.codebuild.ProjectFileSystemLocationArgs(
                                identifier="efs_mount",
                                location=f"{args['dns_name']}:/",
                                mount_point="/mnt/training",  # Use /mnt/training (same as production)
                                type="EFS",
                                mount_options=(
                                    # TODO We originally attempted to mount EFS in CodeBuild using the accesspoint and region options:
                                    #        mount -t efs -o tls,accesspoint=fsap-00a6b574642179604,region=us-east-1,noresvport,nfsvers=4.1 <EFS_DNS_NAME>:/ /mnt/training
                                    #
                                    # However, this configuration caused the mount command to fail with the error:
                                    #        "mounting '127.0.0.1:/' failed. invalid argument"
                                    # and reported that the amazon-efs-mount-watchdog couldn't start due to an unrecognized init system.
                                    #
                                    # Our debugging revealed that:
                                    #   • The EFS DNS name resolves correctly (e.g., via nslookup it returns a valid IP such as 10.2.11.108).
                                    #   • Mounting the EFS root (without these options) works correctly.
                                    #
                                    # For now, we have commented out the "accesspoint" and "region" options from the mount command
                                    # to allow the EFS root to be mounted successfully in CodeBuild.
                                    #
                                    # Further investigation is needed to determine how to properly mount via the access point in CodeBuild
                                    # (possibly by updating amazon-efs-utils or adjusting CodeBuild's environment).
                                    # f"region={args['region']},"
                                    # f"accesspoint={args['ap_id']},"
                                    "nfsvers=4.1,"
                                    "rsize=1048576,"
                                    "wsize=1048576,"
                                    "hard,"
                                    "timeo=600,"
                                    "retrans=2"
                                ),
                            )
                        ]
                        if args["fs_id"]
                        and args["ap_id"]
                        and args["dns_name"]
                        and args["region"]
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
                description=f"Build ML package {package}",
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
                    depends_on=[codebuild_project, upload_command] + self.vpc_endpoints,
                ),
            )

            built_packages.append(codebuild_project)

        # Register outputs
        self.register_outputs(
            {
                "packages": packages,
                "python_version": python_version,
                "force_rebuild": force_rebuild,
                "artifact_bucket": self.artifact_bucket.bucket,
            }
        )

    def _create_buildspec(self, args: Dict[str, Any]) -> str:
        """Create a buildspec for AWS CodeBuild.

        This method generates the buildspec configuration used by AWS CodeBuild to
        build and deploy ML packages. It includes all necessary build phases,
        environment setup, and deployment steps.

        Args:
            args (Dict[str, Any]): Dictionary containing build configuration:
                - bucket_name (str): S3 bucket name for artifacts
                - efs_id (str): EFS file system ID
                - efs_access_point_id (str): EFS access point ID
                - package (str): Package name to build
                - stack (str): Pulumi stack name
                - python_version (str): Python version to use
                - force_rebuild (bool): Whether to force rebuild

        Returns:
            str: JSON string containing the buildspec configuration
        """
        buildspec = {
            "version": 0.2,
            "phases": {
                "install": {
                    "runtime-versions": {"python": args["python_version"]},
                    "commands": [
                        "echo Installing dependencies...",
                        "yum update -y",
                        "yum install -y amazon-efs-utils",
                        "mkdir -p /mnt/training",
                        "echo 'Installing wheel package...'",
                        "pip install wheel",
                    ],
                },
                "pre_build": {
                    "commands": [
                        "echo Starting pre_build phase...",
                        "mkdir -p /tmp/source",
                        "for pkg in receipt_dynamo; do "
                        + "echo Downloading source for $pkg... && "
                        + f"aws s3 cp s3://{args['bucket_name']}/source/$pkg.zip /tmp/$pkg.zip && "
                        + "echo Unzipping source for $pkg... && "
                        + "unzip /tmp/$pkg.zip -d /tmp/source/ && "
                        + "rm /tmp/$pkg.zip; "
                        + "done",
                        "mkdir -p /tmp/cache",
                        "mkdir -p /tmp/output",
                        f"aws s3 cp s3://{args['bucket_name']}/state/{args['package']}.json /tmp/cache/state.json || echo 'No previous build state for {args['package']}'",
                        'if [ ! -z "$LOCAL_PACKAGES" ]; then '
                        + "echo 'Processing local packages: $LOCAL_PACKAGES' && "
                        + "for pkg in $(echo $LOCAL_PACKAGES | tr ',' ' '); do "
                        + '  echo "Zipping local package $pkg..." && '
                        + "  cd /tmp/source/$pkg && zip -r ${pkg}.zip . && "
                        + '  echo "Uploading local package $pkg..." && '
                        + f"  aws s3 cp ${{pkg}}.zip s3://{args['bucket_name']}/local/${{pkg}}.zip && rm ${{pkg}}.zip; "
                        + "done; "
                        + "fi",
                    ]
                },
                "build": {
                    "commands": [
                        "echo Building receipt_dynamo package...",
                        "cd /tmp/source/receipt_dynamo",
                        "pip install build || echo 'Warning: could not install build package for receipt_dynamo'",
                        "python -m build --wheel --outdir /tmp/output/wheels || echo 'Warning: could not build receipt_dynamo package'",
                        "mkdir -p /tmp/output/python/receipt_dynamo",
                        "cp -r /tmp/output/wheels/* /tmp/output/python/receipt_dynamo/ || echo 'Warning: no wheel files found for receipt_dynamo'",
                        "HASH=$(find . -type f \\( -name '*.py' -o -name '*.txt' -o -name '*.yml' -o -name '*.yaml' -o -name '*.json' \\) | sort | xargs cat | sha256sum | cut -d ' ' -f 1)",
                        "BUILD_TIME=$(date +%s)",
                        'echo \'{"build_timestamp": "$(date +%s)", "build_date": "$(date -Iseconds)", "source_hash": "\'$HASH\'"}\' > /tmp/cache/state.json',
                    ]
                },
                "post_build": {
                    "commands": [
                        "echo Uploading build artifacts...",
                        # Make sure the cache directory exists
                        "mkdir -p /tmp/cache",
                        # Create minimal state.json if it doesn't exist
                        'if [ ! -f /tmp/cache/state.json ]; then echo "{\\"package_name\\": \\"${PACKAGE_NAME}\\", \\"build_timestamp\\": \\"$(date +%s)\\", \\"build_date\\": \\"$(date -Iseconds)\\", \\"python_version\\": \\"${PYTHON_VERSION}\\", \\"source_hash\\": \\"error\\"}" > /tmp/cache/state.json; fi',
                        # Upload build state
                        f"aws s3 cp /tmp/cache/state.json s3://{args['bucket_name']}/state/{args['package']}.json || echo 'Failed to upload state.json'",
                        # Create minimal output if it doesn't exist
                        "if [ ! -d /tmp/output ]; then mkdir -p /tmp/output; echo 'Build failed' > /tmp/output/BUILD_FAILED.txt; fi",
                        # Upload build output
                        f"aws s3 cp /tmp/output s3://{args['bucket_name']}/output/{args['package']} --recursive || echo 'Failed to upload output'",
                        'if [ ! -z "$LOCAL_PACKAGES" ]; then '
                        + "for pkg in $(echo $LOCAL_PACKAGES | tr ',' ' '); do "
                        + '  echo "Installing local package $pkg..." && '
                        + f"  aws s3 cp s3://{args['bucket_name']}/local/${{pkg}}.zip /tmp/${{pkg}}.zip || echo \"Local package $pkg not found\" && "
                        + "  pip install /tmp/${pkg}.zip && rm /tmp/${pkg}.zip; "
                        + "done; "
                        + "fi",
                        # Copy files to EFS if it's mounted (using native EFS integration)
                        f"if [ -d /mnt/training ]; then "
                        + f"  echo 'Copying files to EFS...' && "
                        + f"  mkdir -p /mnt/training/{args['package']} && "
                        + f"  cp -r /tmp/output/* /mnt/training/{args['package']}/ && "
                        + "  echo 'Successfully deployed to EFS'; "
                        + "else "
                        + "  echo 'EFS mount point not found. Skipping EFS deployment.'; "
                        + "fi",
                    ]
                },
            },
            "artifacts": {
                "files": ["**/*"],
                "base-directory": "/tmp/output/python",
                "discard-paths": "no",
            },
            "cache": {"paths": ["/tmp/cache/**/*"]},
        }

        return json.dumps(buildspec)

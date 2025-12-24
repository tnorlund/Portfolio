"""
ami_builder.py

Pulumi component for building custom LayoutLM training AMIs.

Uses content-based hashing to detect when the AMI needs to be rebuilt:
- Hash of ami_setup.sh script
- Hash of Python dependency versions
- Base AMI ID

When the hash changes, triggers a CodeBuild job that:
1. Launches an EC2 instance from the base Deep Learning AMI
2. Runs the setup script via SSM
3. Creates an AMI from the configured instance
4. Tags the AMI with the content hash
5. Stores the AMI ID in SSM Parameter Store
"""

import json
import os
import textwrap
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions
import pulumi_command as command

from infra.shared.build_utils import compute_hash


# Python dependencies that affect the AMI - update version to trigger rebuild
AMI_PYTHON_DEPS = """
transformers>=4.40.0
datasets>=2.19.0
boto3>=1.34.0
pillow
tqdm
filelock
pydantic>=2.10.6
accelerate>=0.21.0
seqeval
backports.tarfile
pyarrow==16.1.0
sentencepiece==0.1.99
"""

# AMI builder version - increment to force rebuild
AMI_BUILDER_VERSION = "1"


class LayoutLMAMIBuilder(ComponentResource):
    """Builds and manages custom AMIs for LayoutLM training."""

    def __init__(
        self,
        name: str,
        *,
        instance_type: str = "g5.xlarge",
        key_name: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:ml:LayoutLMAMIBuilder", name, None, opts)

        stack = pulumi.get_stack()
        region = aws.config.region or "us-east-1"

        # Get the directory containing this file
        this_dir = Path(__file__).parent

        # Compute content hash for the AMI
        setup_script_path = this_dir / "ami_setup.sh"
        self.content_hash = compute_hash(
            paths=[str(setup_script_path)],
            extra_strings={
                "python_deps": AMI_PYTHON_DEPS,
                "builder_version": AMI_BUILDER_VERSION,
                "instance_type": instance_type,
            },
        )[:16]  # Use first 16 chars for readability

        # Log the hash for debugging
        pulumi.log.info(f"LayoutLM AMI content hash: {self.content_hash}")

        # SSM Parameter to store the current AMI ID
        self.ami_param_name = f"/layoutlm-training/{stack}/ami-id"
        self.hash_param_name = f"/layoutlm-training/{stack}/ami-hash"

        # Try to find existing AMI with matching hash
        # We'll use SSM to track the current AMI since get_ami can't filter by our custom state

        # S3 bucket for build artifacts (setup script)
        self.bucket = aws.s3.Bucket(
            f"{name}-ami-build",
            force_destroy=True,
            tags={"Component": name, "Purpose": "ami-build"},
            opts=ResourceOptions(parent=self),
        )

        # Upload setup script to S3
        self.setup_script_object = aws.s3.BucketObject(
            f"{name}-setup-script",
            bucket=self.bucket.id,
            key="ami_setup.sh",
            source=pulumi.FileAsset(str(setup_script_path)),
            content_type="text/x-shellscript",
            opts=ResourceOptions(parent=self),
        )

        # IAM role for CodeBuild
        self.codebuild_role = aws.iam.Role(
            f"{name}-codebuild-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codebuild.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        # IAM policy for CodeBuild - needs EC2, SSM, S3, CloudWatch permissions
        self.codebuild_policy = aws.iam.RolePolicy(
            f"{name}-codebuild-policy",
            role=self.codebuild_role.id,
            policy=Output.all(self.bucket.arn).apply(
                lambda args: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ec2:RunInstances",
                                "ec2:TerminateInstances",
                                "ec2:DescribeInstances",
                                "ec2:DescribeInstanceStatus",
                                "ec2:CreateImage",
                                "ec2:DescribeImages",
                                "ec2:DeregisterImage",
                                "ec2:CreateTags",
                                "ec2:DescribeTags",
                            ],
                            "Resource": "*",
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ssm:SendCommand",
                                "ssm:GetCommandInvocation",
                                "ssm:PutParameter",
                                "ssm:GetParameter",
                            ],
                            "Resource": "*",
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:ListBucket",
                            ],
                            "Resource": [
                                args[0],
                                f"{args[0]}/*",
                            ],
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": "*",
                        },
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
                    ],
                })
            ),
            opts=ResourceOptions(parent=self.codebuild_role),
        )

        # IAM role for the EC2 instance during AMI build (needs SSM agent)
        self.instance_role = aws.iam.Role(
            f"{name}-instance-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-instance-ssm",
            role=self.instance_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            opts=ResourceOptions(parent=self.instance_role),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-instance-s3",
            role=self.instance_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
            opts=ResourceOptions(parent=self.instance_role),
        )

        self.instance_profile = aws.iam.InstanceProfile(
            f"{name}-instance-profile",
            role=self.instance_role.name,
            opts=ResourceOptions(parent=self.instance_role),
        )

        # Get default VPC and subnets
        default_vpc = aws.ec2.get_vpc(default=True)
        subnets = aws.ec2.get_subnets(
            filters=[
                aws.ec2.GetSubnetsFilterArgs(
                    name="vpc-id", values=[default_vpc.id]
                )
            ]
        )

        # Security group for the build instance
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-build-sg",
            description="Security group for AMI build instance",
            vpc_id=default_vpc.id,
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                    description="Allow all outbound",
                )
            ],
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # Get the base Deep Learning AMI
        self.base_ami = aws.ec2.get_ami(
            most_recent=True,
            owners=["amazon"],
            filters=[
                {"name": "name", "values": ["Deep Learning * AMI GPU PyTorch*"]},
                {"name": "architecture", "values": ["x86_64"]},
            ],
        )

        # CloudWatch Log Group for CodeBuild
        self.log_group = aws.cloudwatch.LogGroup(
            f"{name}-build-logs",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # CodeBuild project for building AMIs
        buildspec = self._generate_buildspec(
            instance_type=instance_type,
            key_name=key_name,
            subnet_id=subnets.ids[0] if subnets.ids else None,
        )

        self.codebuild_project = aws.codebuild.Project(
            f"{name}-ami-builder",
            description="Builds custom AMI for LayoutLM training",
            service_role=self.codebuild_role.arn,
            build_timeout=60,  # 60 minutes max
            environment=aws.codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                type="LINUX_CONTAINER",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="BASE_AMI_ID",
                        value=self.base_ami.id,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="CONTENT_HASH",
                        value=self.content_hash,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AMI_PARAM_NAME",
                        value=self.ami_param_name,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="HASH_PARAM_NAME",
                        value=self.hash_param_name,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="STACK_NAME",
                        value=stack,
                    ),
                ],
            ),
            source=aws.codebuild.ProjectSourceArgs(
                type="NO_SOURCE",
                buildspec=buildspec,
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
            logs_config=aws.codebuild.ProjectLogsConfigArgs(
                cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                    group_name=self.log_group.name,
                    stream_name="ami-build",
                ),
            ),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # Command to trigger AMI build when hash changes
        # This checks if the current AMI hash matches, and if not, triggers a build
        self.build_trigger = command.local.Command(
            f"{name}-build-trigger",
            create=Output.all(
                self.codebuild_project.name,
                self.bucket.bucket,
                self.instance_profile.name,
                self.security_group.id,
            ).apply(
                lambda args: self._generate_trigger_script(
                    project_name=args[0],
                    bucket=args[1],
                    instance_profile=args[2],
                    security_group=args[3],
                )
            ),
            update=Output.all(
                self.codebuild_project.name,
                self.bucket.bucket,
                self.instance_profile.name,
                self.security_group.id,
            ).apply(
                lambda args: self._generate_trigger_script(
                    project_name=args[0],
                    bucket=args[1],
                    instance_profile=args[2],
                    security_group=args[3],
                )
            ),
            triggers=[self.content_hash],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.codebuild_project,
                    self.setup_script_object,
                    self.codebuild_policy,
                ],
            ),
        )

        # Output the AMI parameter name so the training component can look it up
        self.register_outputs({
            "ami_param_name": self.ami_param_name,
            "hash_param_name": self.hash_param_name,
            "content_hash": self.content_hash,
            "bucket_name": self.bucket.bucket,
            "codebuild_project": self.codebuild_project.name,
        })

    def _generate_buildspec(
        self,
        instance_type: str,
        key_name: Optional[str],
        subnet_id: Optional[str],
    ) -> str:
        """Generate the CodeBuild buildspec for AMI creation."""
        key_name_arg = f"--key-name {key_name}" if key_name else ""
        subnet_arg = f"--subnet-id {subnet_id}" if subnet_id else ""

        return textwrap.dedent(f"""
            version: 0.2
            phases:
              build:
                commands:
                  - echo "=== Starting AMI Build ==="
                  - echo "Base AMI: $BASE_AMI_ID"
                  - echo "Content Hash: $CONTENT_HASH"
                  - echo "Instance Profile: $INSTANCE_PROFILE"
                  - echo "Security Group: $SECURITY_GROUP"
                  - echo "S3 Bucket: $S3_BUCKET"

                  # Check if AMI with this hash already exists
                  - |
                    CURRENT_HASH=$(aws ssm get-parameter --name "$HASH_PARAM_NAME" --query 'Parameter.Value' --output text 2>/dev/null || echo "none")
                    echo "Current hash in SSM: $CURRENT_HASH"
                    if [ "$CURRENT_HASH" = "$CONTENT_HASH" ]; then
                      echo "AMI with hash $CONTENT_HASH already exists, skipping build"
                      EXISTING_AMI=$(aws ssm get-parameter --name "$AMI_PARAM_NAME" --query 'Parameter.Value' --output text)
                      echo "Existing AMI ID: $EXISTING_AMI"
                      exit 0
                    fi

                  # Launch EC2 instance
                  - echo "Launching EC2 instance..."
                  - |
                    INSTANCE_ID=$(aws ec2 run-instances \\
                      --image-id $BASE_AMI_ID \\
                      --instance-type {instance_type} \\
                      --iam-instance-profile Name=$INSTANCE_PROFILE \\
                      --security-group-ids $SECURITY_GROUP \\
                      {key_name_arg} \\
                      {subnet_arg} \\
                      --tag-specifications "ResourceType=instance,Tags=[{{Key=Name,Value=layoutlm-ami-builder}},{{Key=Purpose,Value=ami-build}}]" \\
                      --query 'Instances[0].InstanceId' \\
                      --output text)
                    echo "Instance ID: $INSTANCE_ID"

                  # Wait for instance to be running and SSM ready
                  - echo "Waiting for instance to be running..."
                  - aws ec2 wait instance-running --instance-ids $INSTANCE_ID
                  - echo "Waiting for instance status checks..."
                  - aws ec2 wait instance-status-ok --instance-ids $INSTANCE_ID
                  - echo "Waiting for SSM agent..."
                  - |
                    for i in $(seq 1 30); do
                      SSM_STATUS=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE_ID" --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "None")
                      if [ "$SSM_STATUS" = "Online" ]; then
                        echo "SSM agent is online"
                        break
                      fi
                      echo "Waiting for SSM agent... ($i/30)"
                      sleep 10
                    done

                  # Download and run setup script via SSM
                  - echo "Running setup script via SSM..."
                  - |
                    COMMAND_ID=$(aws ssm send-command \\
                      --instance-ids $INSTANCE_ID \\
                      --document-name "AWS-RunShellScript" \\
                      --parameters "commands=[
                        'aws s3 cp s3://$S3_BUCKET/ami_setup.sh /tmp/ami_setup.sh',
                        'chmod +x /tmp/ami_setup.sh',
                        'sudo /tmp/ami_setup.sh'
                      ]" \\
                      --timeout-seconds 1800 \\
                      --query 'Command.CommandId' \\
                      --output text)
                    echo "SSM Command ID: $COMMAND_ID"

                  # Wait for command to complete
                  - echo "Waiting for setup script to complete..."
                  - |
                    for i in $(seq 1 90); do
                      STATUS=$(aws ssm get-command-invocation \\
                        --command-id $COMMAND_ID \\
                        --instance-id $INSTANCE_ID \\
                        --query 'Status' \\
                        --output text 2>/dev/null || echo "Pending")
                      echo "Command status: $STATUS ($i/90)"
                      if [ "$STATUS" = "Success" ]; then
                        echo "Setup completed successfully!"
                        break
                      elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Cancelled" ] || [ "$STATUS" = "TimedOut" ]; then
                        echo "Setup failed with status: $STATUS"
                        aws ssm get-command-invocation --command-id $COMMAND_ID --instance-id $INSTANCE_ID
                        aws ec2 terminate-instances --instance-ids $INSTANCE_ID
                        exit 1
                      fi
                      sleep 20
                    done

                  # Stop the instance before creating AMI (cleaner state)
                  - echo "Stopping instance before creating AMI..."
                  - aws ec2 stop-instances --instance-ids $INSTANCE_ID
                  - aws ec2 wait instance-stopped --instance-ids $INSTANCE_ID

                  # Create AMI
                  - echo "Creating AMI..."
                  - |
                    AMI_NAME="layoutlm-training-$STACK_NAME-$CONTENT_HASH"
                    AMI_ID=$(aws ec2 create-image \\
                      --instance-id $INSTANCE_ID \\
                      --name "$AMI_NAME" \\
                      --description "LayoutLM training AMI with pre-installed dependencies" \\
                      --tag-specifications "ResourceType=image,Tags=[
                        {{Key=Name,Value=$AMI_NAME}},
                        {{Key=ContentHash,Value=$CONTENT_HASH}},
                        {{Key=ManagedBy,Value=pulumi-layoutlm}},
                        {{Key=Stack,Value=$STACK_NAME}}
                      ]" \\
                      --query 'ImageId' \\
                      --output text)
                    echo "AMI ID: $AMI_ID"

                  # Wait for AMI to be available
                  - echo "Waiting for AMI to be available..."
                  - aws ec2 wait image-available --image-ids $AMI_ID
                  - echo "AMI is ready!"

                  # Terminate the build instance
                  - echo "Terminating build instance..."
                  - aws ec2 terminate-instances --instance-ids $INSTANCE_ID

                  # Store AMI ID and hash in SSM
                  - echo "Storing AMI ID in SSM..."
                  - aws ssm put-parameter --name "$AMI_PARAM_NAME" --value "$AMI_ID" --type String --overwrite
                  - aws ssm put-parameter --name "$HASH_PARAM_NAME" --value "$CONTENT_HASH" --type String --overwrite

                  - echo "=== AMI Build Complete ==="
                  - echo "AMI ID: $AMI_ID"
        """).strip()

    def _generate_trigger_script(
        self,
        project_name: str,
        bucket: str,
        instance_profile: str,
        security_group: str,
    ) -> str:
        """Generate script to trigger AMI build if needed."""
        return textwrap.dedent(f"""
            #!/bin/bash
            set -e

            echo "Checking if AMI build is needed..."

            # Check current hash in SSM
            CURRENT_HASH=$(aws ssm get-parameter --name "{self.hash_param_name}" --query 'Parameter.Value' --output text 2>/dev/null || echo "none")

            if [ "$CURRENT_HASH" = "{self.content_hash}" ]; then
                echo "AMI with hash {self.content_hash} already exists, skipping build"
                AMI_ID=$(aws ssm get-parameter --name "{self.ami_param_name}" --query 'Parameter.Value' --output text)
                echo "Current AMI ID: $AMI_ID"
                exit 0
            fi

            echo "Hash mismatch: current=$CURRENT_HASH, expected={self.content_hash}"
            echo "Triggering AMI build..."

            # Start CodeBuild
            BUILD_ID=$(aws codebuild start-build \\
                --project-name "{project_name}" \\
                --environment-variables-override \\
                    "name=S3_BUCKET,value={bucket}" \\
                    "name=INSTANCE_PROFILE,value={instance_profile}" \\
                    "name=SECURITY_GROUP,value={security_group}" \\
                --query 'build.id' \\
                --output text)

            echo "Started CodeBuild: $BUILD_ID"
            echo "AMI will be available after build completes (~15-20 minutes)"
            echo "Monitor at: https://console.aws.amazon.com/codesuite/codebuild/projects/{project_name}"
        """).strip()

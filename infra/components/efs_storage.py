"""Pulumi component for creating an EFS file system for shared data across instances."""

from typing import List, Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions


class EFSStorage(ComponentResource):
    """Pulumi component for creating an EFS file system for shared data across instances."""

    def __init__(
        self,
        name: str,
        vpc_id: str,
        subnet_ids: pulumi.Output[List[str]] | List[str],
        security_group_ids: List[str],
        instance_role_name: str,
        performance_mode: str = "generalPurpose",
        throughput_mode: str = "bursting",
        encrypted: bool = True,
        lifecycle_policies: Optional[List[dict]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize EFSStorage.

        Args:
            name: Base name for created resources
            vpc_id: VPC ID where EFS will be deployed
            subnet_ids: Output containing list of subnet IDs for mount targets, or plain list
            security_group_ids: List of security group IDs for the EFS mount targets
            instance_role_name: Name of the IAM role used by EC2 instances
            performance_mode: EFS performance mode (generalPurpose or maxIO)
            throughput_mode: EFS throughput mode (bursting or provisioned)
            encrypted: Whether to encrypt the file system
            lifecycle_policies: Optional lifecycle policies for data retention
            opts: Optional resource options
        """
        super().__init__("efs-storage", name, {}, opts)
        # Create security group for EFS access
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-efs-sg",
            description=f"Allow EFS access for {name}",
            vpc_id=vpc_id,
            ingress=[
                # Allow NFS traffic from the provided security groups
                aws.ec2.SecurityGroupIngressArgs(
                    from_port=2049,
                    to_port=2049,
                    protocol="tcp",
                    security_groups=security_group_ids,
                ),
            ],
            egress=[
                # Allow all outbound traffic
                aws.ec2.SecurityGroupEgressArgs(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                ),
            ],
            opts=opts,
        )

        # Create EFS file system
        self.file_system = aws.efs.FileSystem(
            f"{name}-efs",
            performance_mode=performance_mode,
            throughput_mode=throughput_mode,
            encrypted=encrypted,
            lifecycle_policies=(
                [
                    aws.efs.FileSystemLifecyclePolicyArgs(
                        transition_to_ia=lifecycle_policies[0].get(
                            "transition_to_ia", "AFTER_30_DAYS"
                        ),
                    )
                ]
                if lifecycle_policies
                else None
            ),
            tags={
                "Name": f"{name}-shared-storage",
            },
            opts=opts,
        )

        # Convert subnet_ids to Output if it's a plain list
        subnet_ids_output = (
            subnet_ids
            if isinstance(subnet_ids, pulumi.Output)
            else pulumi.Output.from_input(subnet_ids)
        )

        # Create mount targets in each subnet using apply
        def create_mount_targets(resolved_subnet_ids):
            mount_targets = []
            for i, subnet_id in enumerate(resolved_subnet_ids):
                mt = aws.efs.MountTarget(
                    f"{name}-efs-mount-target-{i}",
                    file_system_id=self.file_system.id,
                    subnet_id=subnet_id,
                    security_groups=[self.security_group.id],
                    opts=ResourceOptions(
                        parent=self.file_system
                    ),  # Ensure parent is set correctly
                )
                mount_targets.append(mt)
            return mount_targets

        # Apply the function to the subnet_ids Output
        # Note: This Output won't be directly iterable,
        # but we can pass it to other resources that accept Output[List[MountTarget]] if needed.
        self.mount_targets_output = subnet_ids_output.apply(
            create_mount_targets
        )

        # Create an access point for shared training data
        self.training_access_point = aws.efs.AccessPoint(
            f"{name}-efs-ap-training",
            file_system_id=self.file_system.id,
            posix_user=aws.efs.AccessPointPosixUserArgs(
                gid=1000,
                uid=1000,
            ),
            root_directory=aws.efs.AccessPointRootDirectoryArgs(
                path="/training",
                creation_info=aws.efs.AccessPointRootDirectoryCreationInfoArgs(
                    owner_gid=1000,
                    owner_uid=1000,
                    permissions="755",
                ),
            ),
            opts=opts,
        )

        # Create an access point for checkpoints
        self.checkpoints_access_point = aws.efs.AccessPoint(
            f"{name}-efs-ap-checkpoints",
            file_system_id=self.file_system.id,
            posix_user=aws.efs.AccessPointPosixUserArgs(
                gid=1000,
                uid=1000,
            ),
            root_directory=aws.efs.AccessPointRootDirectoryArgs(
                path="/checkpoints",
                creation_info=aws.efs.AccessPointRootDirectoryCreationInfoArgs(
                    owner_gid=1000,
                    owner_uid=1000,
                    permissions="755",
                ),
            ),
            opts=opts,
        )

        # Create IAM policy for EFS access
        self.efs_policy = aws.iam.Policy(
            f"{name}-efs-policy",
            description="Allows EC2 instances to access EFS file system",
            policy=pulumi.Output.all(
                file_system_arn=self.file_system.arn,
                training_ap_arn=self.training_access_point.arn,
                checkpoints_ap_arn=self.checkpoints_access_point.arn,
            ).apply(
                lambda args: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "elasticfilesystem:ClientMount",
                                "elasticfilesystem:ClientWrite",
                                "elasticfilesystem:ClientRootAccess"
                            ],
                            "Resource": "{args['file_system_arn']}",
                            "Condition": {{
                                "StringEquals": {{
                                    "elasticfilesystem:AccessPointArn": [
                                        "{args['training_ap_arn']}",
                                        "{args['checkpoints_ap_arn']}"
                                    ]
                                }}
                            }}
                        }},
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "elasticfilesystem:DescribeFileSystems",
                                "elasticfilesystem:DescribeAccessPoints",
                                "elasticfilesystem:DescribeMountTargets"
                            ],
                            "Resource": "*"
                        }}
                    ]
                }}"""
            ),
            opts=opts,
        )

        # Attach policy to the instance role
        self.policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-efs-policy-attachment",
            role=instance_role_name,
            policy_arn=self.efs_policy.arn,
            opts=opts,
        )

        # Export the file system ID and DNS name
        self.file_system_id = self.file_system.id
        self.file_system_dns_name = self.file_system.dns_name
        self.training_access_point_id = self.training_access_point.id
        self.checkpoints_access_point_id = self.checkpoints_access_point.id

        # Register component outputs
        self.register_outputs(
            {
                "file_system_id": self.file_system_id,
                "file_system_dns_name": self.file_system_dns_name,
                "training_access_point_id": self.training_access_point_id,
                "checkpoints_access_point_id": self.checkpoints_access_point_id,
            }
        )

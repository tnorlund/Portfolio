"""EFS component for ChromaDB compaction Lambdas.

Creates an encrypted EFS file system, mount targets in provided subnets,
security group allowing NFS from the provided Lambda security group, and a
dedicated Access Point at "/chroma".
"""

from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions


class ChromaEfs(ComponentResource):
    """Provision EFS for Chroma with an access point at /chroma."""

    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        subnet_ids: pulumi.Input[list[str]],
        lambda_security_group_id: pulumi.Input[str],
        additional_client_security_group_ids: (
            pulumi.Input[list[str]] | None
        ) = None,
        # Optional explicit secondary subnet id; when provided, we create a second mount target
        secondary_subnet_id: pulumi.Input[str] | None = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("chromadb:efs:ChromaEfs", name, None, opts)

        # Security group for EFS allowing NFS from Lambda SG
        # Build list of client SGs allowed to mount EFS (Lambda SG + optional others like ECS SG)
        client_sgs = [lambda_security_group_id]
        if additional_client_security_group_ids is not None:
            # Flatten into a simple list; Pulumi will resolve Inputs
            client_sgs = client_sgs + list(additional_client_security_group_ids)  # type: ignore[arg-type]

        self.efs_sg = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description="EFS SG for Chroma (allow NFS from Lambda SG)",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=2049,
                    to_port=2049,
                    security_groups=client_sgs,
                    description="Allow NFS from Lambda SG",
                )
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={
                "Name": f"{name}-sg",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # EFS filesystem
        self.file_system = aws.efs.FileSystem(
            f"{name}-fs",
            encrypted=True,
            performance_mode="generalPurpose",
            throughput_mode="bursting",
            tags={
                "Name": f"{name}-fs",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create mount targets in the first two subnets for AZ coverage
        # Primary mount target (subnet index 0)
        self.primary_mount_target = aws.efs.MountTarget(
            f"{name}-mt-0",
            file_system_id=self.file_system.id,
            subnet_id=subnet_ids.apply(lambda ids: ids[0]),
            security_groups=[self.efs_sg.id],
            opts=ResourceOptions(parent=self.file_system),
        )

        # Secondary mount target (optional)
        self.secondary_mount_target = (
            aws.efs.MountTarget(
                f"{name}-mt-1",
                file_system_id=self.file_system.id,
                subnet_id=secondary_subnet_id,
                security_groups=[self.efs_sg.id],
                opts=ResourceOptions(parent=self.file_system),
            )
            if secondary_subnet_id is not None
            else None
        )

        # Access point at /chroma
        self.access_point = aws.efs.AccessPoint(
            f"{name}-ap",
            file_system_id=self.file_system.id,
            posix_user=aws.efs.AccessPointPosixUserArgs(uid=1000, gid=1000),
            root_directory=aws.efs.AccessPointRootDirectoryArgs(
                path="/chroma",
                creation_info=aws.efs.AccessPointRootDirectoryCreationInfoArgs(
                    owner_uid=1000,
                    owner_gid=1000,
                    permissions="755",
                ),
            ),
            opts=ResourceOptions(parent=self.file_system),
        )

        # Outputs
        self.access_point_arn = self.access_point.arn
        self.access_point_id = self.access_point.id
        self.file_system_id = self.file_system.id
        self.security_group_id = self.efs_sg.id

        self.register_outputs(
            {
                "access_point_arn": self.access_point_arn,
                "access_point_id": self.access_point_id,
                "file_system_id": self.file_system_id,
                "security_group_id": self.security_group_id,
                # Expose mount target IDs for troubleshooting
                "primary_mount_target_id": self.primary_mount_target.id,
                "secondary_mount_target_id": (
                    self.secondary_mount_target.id
                    if self.secondary_mount_target is not None
                    else None
                ),
            }
        )

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
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("chromadb:efs:ChromaEfs", name, None, opts)

        # Security group for EFS allowing NFS from Lambda SG
        self.efs_sg = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description="EFS SG for Chroma (allow NFS from Lambda SG)",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=2049,
                    to_port=2049,
                    security_groups=[lambda_security_group_id],
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

        # EFS filesystem with Elastic throughput for faster ChromaDB snapshot copies
        # Elastic mode auto-scales to 1000+ MiB/s and charges per GB transferred
        # Much faster than bursting mode (50 MiB/s) for our ~3GB copies
        self.file_system = aws.efs.FileSystem(
            f"{name}-fs",
            encrypted=True,
            performance_mode="generalPurpose",
            throughput_mode="elastic",
            tags={
                "Name": f"{name}-fs",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Mount targets: one per provided subnet (we filter in __main__.py to have only unique AZs)
        # Note: EFS only allows one mount target per AZ, so caller must pass unique AZ subnets
        def _mk_mt(ids: list[str]):
            targets = []
            for i, sid in enumerate(ids):
                mt = aws.efs.MountTarget(
                    f"{name}-mt-{i}",
                    file_system_id=self.file_system.id,
                    subnet_id=sid,
                    security_groups=[self.efs_sg.id],
                    opts=ResourceOptions(parent=self.file_system),
                )
                targets.append(mt)
            return targets

        self.mount_targets = subnet_ids.apply(_mk_mt)

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
        self.file_system_id = self.file_system.id
        self.security_group_id = self.efs_sg.id

        self.register_outputs(
            {
                "access_point_arn": self.access_point_arn,
                "file_system_id": self.file_system_id,
                "security_group_id": self.security_group_id,
            }
        )

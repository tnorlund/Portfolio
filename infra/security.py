"""Shared VPC security groups for Lambda workloads."""

from typing import Optional

import pulumi
import pulumi_aws as aws


class ChromaSecurity(pulumi.ComponentResource):
    """Create Lambda egress and interface-endpoint security groups.

    The historical ECS-hosted Chroma service and its orchestration roles were
    removed after Chroma Cloud became the query target.  This component keeps
    only the security groups still shared by the container Lambdas.
    """

    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:security:ChromaSecurity", name, {}, opts)

        self.sg_lambda = aws.ec2.SecurityGroup(
            f"{name}-sg-lambda",
            vpc_id=vpc_id,
            description="Lambda egress-only security group",
            ingress=[],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={
                "Name": f"{name}-sg-lambda",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.sg_vpce = aws.ec2.SecurityGroup(
            f"{name}-sg-vpce",
            vpc_id=vpc_id,
            # Preserve the existing immutable AWS description to avoid
            # replacing the endpoint security group during this cleanup.
            description="Interface endpoint SG: allow 443 from Lambda and Chroma",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=443,
                    to_port=443,
                    description="Allow HTTPS from Lambda SG",
                    security_groups=[self.sg_lambda.id],
                )
            ],
            egress=[],
            tags={
                "Name": f"{name}-sg-vpce",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.sg_lambda_id = self.sg_lambda.id
        self.sg_vpce_id = self.sg_vpce.id

        self.register_outputs(
            {
                "sg_lambda_id": self.sg_lambda_id,
                "sg_vpce_id": self.sg_vpce_id,
            }
        )

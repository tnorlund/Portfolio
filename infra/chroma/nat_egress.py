from __future__ import annotations

import pulumi
import pulumi_aws as aws
from pulumi import Output, ResourceOptions


class NatEgress(pulumi.ComponentResource):
    """
    Creates a minimal NAT instance for VPC egress and two private subnets with a route table
    that sends 0.0.0.0/0 through the NAT instance. Intended for scale-to-zero by starting/stopping
    the instance around jobs.
    """

    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        public_subnet_id: pulumi.Input[str],
        cidr_block_a: str = "10.0.101.0/24",
        cidr_block_b: str = "10.0.102.0/24",
        opts: ResourceOptions | None = None,
    ) -> None:
        super().__init__("custom:chroma:NatEgress", name, None, opts)

        # Private subnets
        self.private_subnet_a = aws.ec2.Subnet(
            f"{name}-priv-a",
            vpc_id=vpc_id,
            cidr_block=cidr_block_a,
            map_public_ip_on_launch=False,
            opts=ResourceOptions(parent=self),
        )
        self.private_subnet_b = aws.ec2.Subnet(
            f"{name}-priv-b",
            vpc_id=vpc_id,
            cidr_block=cidr_block_b,
            map_public_ip_on_launch=False,
            opts=ResourceOptions(parent=self),
        )

        # Route table for private subnets
        self.private_rt = aws.ec2.RouteTable(
            f"{name}-priv-rt",
            vpc_id=vpc_id,
            opts=ResourceOptions(parent=self),
        )

        aws.ec2.RouteTableAssociation(
            f"{name}-priv-rt-assoc-a",
            route_table_id=self.private_rt.id,
            subnet_id=self.private_subnet_a.id,
            opts=ResourceOptions(parent=self),
        )
        aws.ec2.RouteTableAssociation(
            f"{name}-priv-rt-assoc-b",
            route_table_id=self.private_rt.id,
            subnet_id=self.private_subnet_b.id,
            opts=ResourceOptions(parent=self),
        )

        # Security group for NAT instance (egress all)
        self.nat_sg = aws.ec2.SecurityGroup(
            f"{name}-nat-sg",
            vpc_id=vpc_id,
            description="NAT instance SG",
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=[cidr_block_a, cidr_block_b],
                )
            ],
            opts=ResourceOptions(parent=self),
        )

        # Latest Amazon Linux 2 ARM64 AMI
        ami = aws.ec2.get_ami_output(
            owners=["137112412989"],
            most_recent=True,
            filters=[
                aws.ec2.GetAmiFilterArgs(
                    name="name", values=["amzn2-ami-hvm-*-arm64-gp2"]
                ),
                aws.ec2.GetAmiFilterArgs(name="state", values=["available"]),
            ],
        )

        # NAT instance in public subnet with EIP
        self.nat_instance = aws.ec2.Instance(
            f"{name}-nat",
            ami=ami.id,
            instance_type="t4g.nano",
            subnet_id=public_subnet_id,
            vpc_security_group_ids=[self.nat_sg.id],
            associate_public_ip_address=True,
            source_dest_check=False,
            user_data_replace_on_change=True,
            user_data="""#!/bin/bash
set -euxo pipefail

# Enable IPv4 forwarding now and persist across reboots
if ! grep -q '^net.ipv4.ip_forward' /etc/sysctl.conf; then
  echo 'net.ipv4.ip_forward = 1' >> /etc/sysctl.conf
fi
sysctl -w net.ipv4.ip_forward=1

# Determine egress interface (default to eth0)
IFACE=$(ip route show default | awk '/default/ {print $5}' || echo eth0)

# Ensure iptables is available and rules persist
if command -v yum >/dev/null 2>&1; then
  yum install -y -q iptables-services || true
fi

# Configure NAT (idempotent)
iptables -t nat -C POSTROUTING -o "$IFACE" -j MASQUERADE || iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE
iptables -C FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT || iptables -A FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -C FORWARD -j ACCEPT || iptables -A FORWARD -j ACCEPT

# Persist iptables rules (best-effort across distros)
(command -v service >/dev/null 2>&1 && service iptables save) || iptables-save > /etc/sysconfig/iptables || true
# Ensure iptables service enabled on boot if available
(command -v systemctl >/dev/null 2>&1 && systemctl enable --now iptables) || true
""",
            opts=ResourceOptions(parent=self),
        )

        self.eip = aws.ec2.Eip(
            f"{name}-eip",
            instance=self.nat_instance.id,
            domain="vpc",
            opts=ResourceOptions(parent=self),
        )

        # Default route in private RT via NAT instance
        aws.ec2.Route(
            f"{name}-priv-default",
            route_table_id=self.private_rt.id,
            destination_cidr_block="0.0.0.0/0",
            network_interface_id=self.nat_instance.primary_network_interface_id,
            opts=ResourceOptions(parent=self, depends_on=[self.nat_instance]),
        )

        self.private_subnet_ids = Output.all(
            self.private_subnet_a.id, self.private_subnet_b.id
        ).apply(lambda ids: ids)
        self.nat_instance_id = self.nat_instance.id

        self.register_outputs(
            {
                "private_subnet_ids": self.private_subnet_ids,
                "nat_instance_id": self.nat_instance_id,
                "private_route_table_id": self.private_rt.id,
            }
        )

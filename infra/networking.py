from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


class VpcForCodeBuild(ComponentResource):
    """Creates a VPC suitable for CodeBuild with EFS access.

    Includes:
    - VPC
    - Public and Private Subnets across 4 AZs
    - Internet Gateway
    - NAT Gateways (one in each public subnet)
    - Route Tables and Associations
    - Security Groups for CodeBuild and EFS
    """

    def __init__(self, name: str, opts: Optional[pulumi.ResourceOptions] = None):
        super().__init__("custom:network:VpcForCodeBuild", name, {}, opts)

        # Create VPC
        self.vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags={
                "Name": f"{name}-vpc",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Store VPC ID as an attribute
        self.vpc_id = self.vpc.id

        # Create a new Internet Gateway
        self.igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=self.vpc.id,
            tags={
                "Name": f"{name}-igw",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create public subnets
        self.public_subnets = []
        for i, az in enumerate(["us-east-1a", "us-east-1b", "us-east-1c"]):
            subnet = aws.ec2.Subnet(
                f"{name}-public-subnet-{i+1}",
                vpc_id=self.vpc.id,
                cidr_block=f"10.0.{i+1}.0/24",
                availability_zone=az,
                map_public_ip_on_launch=True,
                tags={
                    "Name": f"{name}-public-subnet-{i+1}",
                    "Environment": pulumi.get_stack(),
                    "ManagedBy": "Pulumi",
                },
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.public_subnets.append(subnet)

        # Store public subnet IDs as an attribute
        self.public_subnet_ids = Output.all(
            *[subnet.id for subnet in self.public_subnets]
        )

        # Create Elastic IP for NAT Gateway
        self.nat_eip = aws.ec2.Eip(
            f"{name}-nat-eip",
            domain="vpc",
            tags={
                "Name": f"{name}-nat-eip",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create NAT Gateway in the first public subnet
        self.nat_gateway = aws.ec2.NatGateway(
            f"{name}-nat-gw",
            allocation_id=self.nat_eip.id,
            subnet_id=self.public_subnets[0].id,  # Use first public subnet
            tags={
                "Name": f"{name}-nat-gw",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create private subnets
        self.private_subnets = []
        for i, az in enumerate(["us-east-1a", "us-east-1b", "us-east-1c"]):
            subnet = aws.ec2.Subnet(
                f"{name}-private-subnet-{i+1}",
                vpc_id=self.vpc.id,
                cidr_block=f"10.0.{i+4}.0/24",
                availability_zone=az,
                tags={
                    "Name": f"{name}-private-subnet-{i+1}",
                    "Environment": pulumi.get_stack(),
                    "ManagedBy": "Pulumi",
                },
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.private_subnets.append(subnet)

        # Store private subnet IDs as an attribute
        self.private_subnet_ids = Output.all(
            *[subnet.id for subnet in self.private_subnets]
        )

        # Create public route table
        self.public_route_table = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=self.vpc.id,
            tags={
                "Name": f"{name}-public-rt",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create public route
        self.public_route = aws.ec2.Route(
            f"{name}-public-route",
            route_table_id=self.public_route_table.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=self.igw.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Associate public subnets with public route table
        self.public_route_table_associations = []
        for i, subnet in enumerate(self.public_subnets):
            association = aws.ec2.RouteTableAssociation(
                f"{name}-public-rt-assoc-{i+1}",
                subnet_id=subnet.id,
                route_table_id=self.public_route_table.id,
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.public_route_table_associations.append(association)

        # Create private route table
        self.private_route_table = aws.ec2.RouteTable(
            f"{name}-private-rt",
            vpc_id=self.vpc.id,
            tags={
                "Name": f"{name}-private-rt",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Add route to NAT Gateway for outbound Internet access
        self.private_route = aws.ec2.Route(
            f"{name}-private-route",
            route_table_id=self.private_route_table.id,
            destination_cidr_block="0.0.0.0/0",
            nat_gateway_id=self.nat_gateway.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Associate private subnets with private route table
        self.private_route_table_associations = []
        for i, subnet in enumerate(self.private_subnets):
            association = aws.ec2.RouteTableAssociation(
                f"{name}-private-rt-assoc-{i+1}",
                subnet_id=subnet.id,
                route_table_id=self.private_route_table.id,
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.private_route_table_associations.append(association)

        # Create security group
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=self.vpc.id,
            description="Security group for ML training instances",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=22,
                    to_port=22,
                    cidr_blocks=["0.0.0.0/0"],
                    description="Allow SSH access",
                ),
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=2049,
                    to_port=2049,
                    cidr_blocks=["10.0.0.0/16"],
                    description="Allow NFS access for EFS",
                ),
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                ),
            ],
            tags={
                "Name": f"{name}-sg",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Store security group ID as an attribute
        self.security_group_id = self.security_group.id

        # Register outputs
        self.register_outputs(
            {
                "vpc_id": self.vpc_id,
                "public_subnet_ids": self.public_subnet_ids,
                "private_subnet_ids": self.private_subnet_ids,
                "security_group_id": self.security_group_id,
            }
        )


class PublicVpc(ComponentResource):
    """Minimal public-only VPC (no NAT) per foundation task.

    - VPC 10.0.0.0/16 with DNS support and hostnames
    - 2 public subnets across different AZs
    - Internet Gateway
    - Public route table with 0.0.0.0/0 via IGW
    """

    def __init__(self, name: str, opts: Optional[pulumi.ResourceOptions] = None):
        super().__init__("custom:network:PublicVpc", name, {}, opts)

        # Create VPC
        self.vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags={
                "Name": f"{name}-vpc",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Internet Gateway
        self.igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=self.vpc.id,
            tags={
                "Name": f"{name}-igw",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Determine two AZs
        azs = aws.get_availability_zones(state="available").names[:2]

        # Two public subnets across different AZs
        self.public_subnets = []
        for i, az in enumerate(azs):
            subnet = aws.ec2.Subnet(
                f"{name}-public-{i+1}",
                vpc_id=self.vpc.id,
                cidr_block=f"10.0.{i}.0/24",
                availability_zone=az,
                map_public_ip_on_launch=True,
                tags={
                    "Name": f"{name}-public-{i+1}",
                    "Environment": pulumi.get_stack(),
                    "ManagedBy": "Pulumi",
                },
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.public_subnets.append(subnet)

        # Public route table
        self.public_rt = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=self.vpc.id,
            tags={
                "Name": f"{name}-public-rt",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Default route to IGW
        self.public_route = aws.ec2.Route(
            f"{name}-public-0-0-0-0",
            route_table_id=self.public_rt.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=self.igw.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Associate subnets with public RT
        self.public_assocs = []
        for i, subnet in enumerate(self.public_subnets):
            assoc = aws.ec2.RouteTableAssociation(
                f"{name}-public-rt-assoc-{i+1}",
                subnet_id=subnet.id,
                route_table_id=self.public_rt.id,
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.public_assocs.append(assoc)

        # Expose attributes for easy consumption
        self.vpc_id = self.vpc.id
        self.public_subnet_ids = Output.all(*[s.id for s in self.public_subnets])
        self.internet_gateway_id = self.igw.id
        self.public_route_table_id = self.public_rt.id

        # Outputs
        self.register_outputs(
            {
                "vpc_id": self.vpc_id,
                "public_subnet_ids": self.public_subnet_ids,
                "internet_gateway_id": self.internet_gateway_id,
                "public_route_table_id": self.public_route_table_id,
            }
        )

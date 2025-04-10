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

    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__("custom:network:VpcForCodeBuild", name, {}, opts)

        # Get Availability Zones
        azs = aws.get_availability_zones(state="available")

        # VPC
        vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block="10.2.0.0/16",
            enable_dns_support=True,
            enable_dns_hostnames=True,
            tags={"Name": f"{name}-vpc"},
            opts=ResourceOptions(parent=self),
        )

        # Internet Gateway
        igw = aws.ec2.InternetGateway(
            f"{name}-igw-{pulumi.get_stack()}",
            tags={"Name": f"{name}-igw-{pulumi.get_stack()}"},
            opts=ResourceOptions(parent=vpc),
        )

        # Attach Internet Gateway to VPC
        igw_attachment = aws.ec2.InternetGatewayAttachment(
            f"{name}-igw-attachment-{pulumi.get_stack()}",
            vpc_id=vpc.id,
            internet_gateway_id=igw.id,
            opts=ResourceOptions(parent=igw, depends_on=[igw, vpc]),
        )

        # Create subnets in all available AZs
        public_subnets = []
        private_subnets = []
        nat_gateways = []
        nat_eips = []

        # Create NAT Gateway EIPs first
        for i in range(len(azs.names)):
            nat_eip = aws.ec2.Eip(
                f"{name}-nat-eip-{i}",
                domain="vpc",
                opts=ResourceOptions(parent=self, depends_on=[igw]),
            )
            nat_eips.append(nat_eip)

        # Create public and private subnets in each AZ
        for i, az in enumerate(azs.names):
            # Public subnet
            public_subnet = aws.ec2.Subnet(
                f"{name}-public-subnet-{i}",
                vpc_id=vpc.id,
                cidr_block=f"10.2.{i}.0/24",
                availability_zone=az,
                map_public_ip_on_launch=True,
                tags={"Name": f"{name}-public-subnet-{i}"},
                opts=ResourceOptions(parent=vpc),
            )
            public_subnets.append(public_subnet)

            # Private subnet
            private_subnet = aws.ec2.Subnet(
                f"{name}-private-subnet-{i}",
                vpc_id=vpc.id,
                cidr_block=f"10.2.{i+10}.0/24",
                availability_zone=az,
                map_public_ip_on_launch=False,
                tags={"Name": f"{name}-private-subnet-{i}"},
                opts=ResourceOptions(parent=vpc),
            )
            private_subnets.append(private_subnet)

            # Create NAT Gateway in public subnet
            nat_gw = aws.ec2.NatGateway(
                f"{name}-nat-gw-{i}",
                allocation_id=nat_eips[i].id,
                subnet_id=public_subnet.id,
                tags={"Name": f"{name}-nat-gw-{i}"},
                opts=ResourceOptions(
                    parent=self, depends_on=[public_subnet, nat_eips[i]]
                ),
            )
            nat_gateways.append(nat_gw)

        # Public Route Table
        public_rt = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=vpc.id,
            tags={"Name": f"{name}-public-rt"},
            opts=ResourceOptions(parent=vpc),
        )

        # Create default route for public subnets
        public_route = aws.ec2.Route(
            f"{name}-public-route",
            route_table_id=public_rt.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=igw.id,
            opts=ResourceOptions(parent=public_rt),
        )

        # Associate public subnets with public route table
        for i, subnet in enumerate(public_subnets):
            aws.ec2.RouteTableAssociation(
                f"{name}-public-rta-{i}",
                subnet_id=subnet.id,
                route_table_id=public_rt.id,
                opts=ResourceOptions(parent=public_rt),
            )

        # Create private route tables and associate with private subnets
        private_route_tables = []
        for i, (private_subnet, nat_gw) in enumerate(
            zip(private_subnets, nat_gateways)
        ):
            private_rt = aws.ec2.RouteTable(
                f"{name}-private-rt-{i}",
                vpc_id=vpc.id,
                tags={"Name": f"{name}-private-rt-{i}"},
                opts=ResourceOptions(parent=vpc),
            )

            # Create default route for private subnet
            private_route = aws.ec2.Route(
                f"{name}-private-route-{i}",
                route_table_id=private_rt.id,
                destination_cidr_block="0.0.0.0/0",
                nat_gateway_id=nat_gw.id,
                opts=ResourceOptions(parent=private_rt, depends_on=[nat_gw]),
            )

            # Associate private subnet with its route table
            aws.ec2.RouteTableAssociation(
                f"{name}-private-rta-{i}",
                subnet_id=private_subnet.id,
                route_table_id=private_rt.id,
                opts=ResourceOptions(parent=private_rt),
            )

            private_route_tables.append(private_rt)

        # Create DynamoDB VPC endpoint
        dynamodb_endpoint = aws.ec2.VpcEndpoint(
            f"{name}-dynamodb-endpoint",
            vpc_id=vpc.id,
            service_name=f"com.amazonaws.{aws.config.region}.dynamodb",
            vpc_endpoint_type="Gateway",
            route_table_ids=[rt.id for rt in private_route_tables],
            tags={"Name": f"{name}-dynamodb-endpoint"},
            opts=ResourceOptions(parent=vpc),
        )

        # Simple NoIngressSecurityGroup
        no_ingress_sg = aws.ec2.SecurityGroup(
            f"{name}-no-ingress-sg",
            description="Security group with no ingress rule",
            vpc_id=vpc.id,
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={"Name": f"{name}-no-ingress-sg"},
            opts=ResourceOptions(parent=vpc),
        )

        # Store outputs
        self.vpc_id = vpc.id
        self.public_subnet_ids = Output.all(
            *[subnet.id for subnet in public_subnets]
        )
        self.private_subnet_ids = Output.all(
            *[subnet.id for subnet in private_subnets]
        )
        self.security_group_id = no_ingress_sg.id

        self.register_outputs(
            {
                "vpc_id": self.vpc_id,
                "public_subnet_ids": self.public_subnet_ids,
                "private_subnet_ids": self.private_subnet_ids,
                "security_group_id": self.security_group_id,
            }
        )

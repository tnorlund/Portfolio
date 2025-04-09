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

        # Public Subnets (using first two AZs)
        public_subnet1 = aws.ec2.Subnet(
            f"{name}-public-subnet-1",
            vpc_id=vpc.id,
            cidr_block="10.2.1.0/24",
            availability_zone=azs.names[0],
            map_public_ip_on_launch=True,
            tags={"Name": f"{name}-public-subnet-1"},
            opts=ResourceOptions(parent=vpc),
        )

        public_subnet2 = aws.ec2.Subnet(
            f"{name}-public-subnet-2",
            vpc_id=vpc.id,
            cidr_block="10.2.2.0/24",
            availability_zone=azs.names[1],
            map_public_ip_on_launch=True,
            tags={"Name": f"{name}-public-subnet-2"},
            opts=ResourceOptions(parent=vpc),
        )

        # Private Subnets (using next two AZs)
        private_subnet1 = aws.ec2.Subnet(
            f"{name}-private-subnet-1",
            vpc_id=vpc.id,
            cidr_block="10.2.11.0/24",
            availability_zone=azs.names[2],
            map_public_ip_on_launch=False,
            tags={"Name": f"{name}-private-subnet-1"},
            opts=ResourceOptions(parent=vpc),
        )

        private_subnet2 = aws.ec2.Subnet(
            f"{name}-private-subnet-2",
            vpc_id=vpc.id,
            cidr_block="10.2.12.0/24",
            availability_zone=azs.names[3],
            map_public_ip_on_launch=False,
            tags={"Name": f"{name}-private-subnet-2"},
            opts=ResourceOptions(parent=vpc),
        )

        # NAT Gateway EIPs and NAT Gateways (one in each public subnet)
        nat_eip1 = aws.ec2.Eip(
            f"{name}-nat-eip-1",
            domain="vpc",
            opts=ResourceOptions(parent=self, depends_on=[igw]),
        )

        nat_eip2 = aws.ec2.Eip(
            f"{name}-nat-eip-2",
            domain="vpc",
            opts=ResourceOptions(parent=self, depends_on=[igw]),
        )

        nat_gw1 = aws.ec2.NatGateway(
            f"{name}-nat-gw-1",
            allocation_id=nat_eip1.id,
            subnet_id=public_subnet1.id,
            tags={"Name": f"{name}-nat-gw-1"},
            opts=ResourceOptions(
                parent=self, depends_on=[public_subnet1, nat_eip1]
            ),
        )

        nat_gw2 = aws.ec2.NatGateway(
            f"{name}-nat-gw-2",
            allocation_id=nat_eip2.id,
            subnet_id=public_subnet2.id,
            tags={"Name": f"{name}-nat-gw-2"},
            opts=ResourceOptions(
                parent=self, depends_on=[public_subnet2, nat_eip2]
            ),
        )

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

        aws.ec2.RouteTableAssociation(
            f"{name}-public-rta-1",
            subnet_id=public_subnet1.id,
            route_table_id=public_rt.id,
            opts=ResourceOptions(parent=public_rt),
        )

        aws.ec2.RouteTableAssociation(
            f"{name}-public-rta-2",
            subnet_id=public_subnet2.id,
            route_table_id=public_rt.id,
            opts=ResourceOptions(parent=public_rt),
        )

        # Private Route Tables (one for each private subnet)
        private_rt1 = aws.ec2.RouteTable(
            f"{name}-private-rt-1",
            vpc_id=vpc.id,
            tags={"Name": f"{name}-private-rt-1"},
            opts=ResourceOptions(parent=vpc),
        )

        # Create default route for first private subnet
        private_route1 = aws.ec2.Route(
            f"{name}-private-route-1",
            route_table_id=private_rt1.id,
            destination_cidr_block="0.0.0.0/0",
            nat_gateway_id=nat_gw1.id,
            opts=ResourceOptions(parent=private_rt1, depends_on=[nat_gw1]),
        )

        private_rt2 = aws.ec2.RouteTable(
            f"{name}-private-rt-2",
            vpc_id=vpc.id,
            tags={"Name": f"{name}-private-rt-2"},
            opts=ResourceOptions(parent=vpc),
        )

        # Create default route for second private subnet
        private_route2 = aws.ec2.Route(
            f"{name}-private-route-2",
            route_table_id=private_rt2.id,
            destination_cidr_block="0.0.0.0/0",
            nat_gateway_id=nat_gw2.id,
            opts=ResourceOptions(parent=private_rt2, depends_on=[nat_gw2]),
        )

        # Associate private subnets with their route tables
        aws.ec2.RouteTableAssociation(
            f"{name}-private-rta-1",
            subnet_id=private_subnet1.id,
            route_table_id=private_rt1.id,
            opts=ResourceOptions(parent=private_rt1),
        )

        aws.ec2.RouteTableAssociation(
            f"{name}-private-rta-2",
            subnet_id=private_subnet2.id,
            route_table_id=private_rt2.id,
            opts=ResourceOptions(parent=private_rt2),
        )

        # Create DynamoDB VPC endpoint
        dynamodb_endpoint = aws.ec2.VpcEndpoint(
            f"{name}-dynamodb-endpoint",
            vpc_id=vpc.id,
            service_name=f"com.amazonaws.{aws.config.region}.dynamodb",
            vpc_endpoint_type="Gateway",
            route_table_ids=[private_rt1.id, private_rt2.id],
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
            public_subnet1.id, public_subnet2.id
        )
        self.private_subnet_ids = Output.all(
            private_subnet1.id, private_subnet2.id
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

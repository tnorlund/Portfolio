"""AWS Spot Instance Training Infrastructure using Pulumi"""

import json
import base64
import pulumi
import pulumi_aws as aws

# Configuration
project_name = "aws-spot-training"
aws_region = aws.get_region().name

# Reference the portfolio/prod stack to get the DynamoDB table ARN.
portfolio_stack = pulumi.StackReference("tnorlund/portfolio/prod")
dynamodb_table_arn = portfolio_stack.get_output("dynamodb_table_arn")

# Get the latest Deep Learning AMI
dl_ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(
            name="name", values=["Deep Learning AMI GPU PyTorch*"]
        ),
        aws.ec2.GetAmiFilterArgs(name="architecture", values=["x86_64"]),
        aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
    ],
)

# Get default VPC and its subnets
default_vpc = aws.ec2.get_vpc(default=True)
default_subnets = aws.ec2.get_subnets(
    filters=[aws.ec2.GetSubnetsFilterArgs(name="vpc-id", values=[default_vpc.id])]
)

# Create an S3 bucket for logs, checkpoints, and artifacts
bucket = aws.s3.Bucket(
    f"{project_name}-bucket",
    bucket=f"{project_name}-{pulumi.get_stack()}",
    versioning=aws.s3.BucketVersioningArgs(enabled=True),
    server_side_encryption_configuration=aws.s3.BucketServerSideEncryptionConfigurationArgs(
        rule=aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256"
            )
        )
    ),
    lifecycle_rules=[
        aws.s3.BucketLifecycleRuleArgs(
            enabled=True,
            transitions=[
                aws.s3.BucketLifecycleRuleTransitionArgs(
                    days=30, storage_class="STANDARD_IA"
                ),
                aws.s3.BucketLifecycleRuleTransitionArgs(
                    days=90, storage_class="GLACIER"
                ),
            ],
        )
    ],
)

# Create a security group for our instances.
security_group = aws.ec2.SecurityGroup(
    f"{project_name}-sg",
    description="Security group for training instances",
    vpc_id=default_vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=22,
            to_port=22,
            cidr_blocks=["0.0.0.0/0"],
            description="SSH access",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound traffic",
        )
    ],
)

# Create an IAM role for EC2 instances.
instance_role = aws.iam.Role(
    f"{project_name}-instance-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Effect": "Allow",
                    "Sid": "",
                }
            ],
        }
    ),
)

# Attach an S3 access policy to the role.
s3_access_policy = aws.iam.RolePolicy(
    f"{project_name}-s3-access",
    role=instance_role.id,
    policy=pulumi.Output.all(bucket_name=bucket.id).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                        "Resource": [
                            f"arn:aws:s3:::{args['bucket_name']}",
                            f"arn:aws:s3:::{args['bucket_name']}/*",
                        ],
                    }
                ],
            }
        )
    ),
)

# Attach a DynamoDB access policy to the role.
# This policy allows:
# - DescribeTable (for the client's __init__ call),
# - Query (for listReceiptDetails),
# on both the table and its "GSI2" index.
dynamodb_access_policy = aws.iam.RolePolicy(
    f"{project_name}-dynamodb-access",
    role=instance_role.id,
    policy=dynamodb_table_arn.apply(
        lambda table_arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeTable",
                            "dynamodb:Query",
                        ],
                        "Resource": [table_arn, f"{table_arn}/index/GSI2"],
                    }
                ],
            }
        )
    ),
)

# Create an instance profile.
instance_profile = aws.iam.InstanceProfile(
    f"{project_name}-instance-profile", role=instance_role.name
)

# Create a launch template.
launch_template = aws.ec2.LaunchTemplate(
    f"{project_name}-launch-template",
    name=f"{project_name}-template",
    image_id=dl_ami.id,
    instance_type="p3.2xlarge",
    vpc_security_group_ids=[security_group.id],
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        name=instance_profile.name
    ),
    user_data=pulumi.Output.all(bucket_name=bucket.id).apply(
        lambda args: base64.b64encode(
            f"""#!/bin/bash
pip install wandb
export BUCKET_NAME={args['bucket_name']}
# Add your training script startup commands here
""".encode()
        ).decode()
    ),
)

# Create an Auto Scaling Group.
asg = aws.autoscaling.Group(
    f"{project_name}-asg",
    vpc_zone_identifiers=default_subnets.ids,
    desired_capacity=0,
    max_size=10,
    min_size=0,
    mixed_instances_policy=aws.autoscaling.GroupMixedInstancesPolicyArgs(
        launch_template=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateArgs(
            launch_template_specification=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateLaunchTemplateSpecificationArgs(
                launch_template_id=launch_template.id, version="$Latest"
            ),
            overrides=[
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="p3.2xlarge"
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="p3.8xlarge"
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="g4dn.xlarge"
                ),
            ],
        ),
        instances_distribution=aws.autoscaling.GroupMixedInstancesPolicyInstancesDistributionArgs(
            on_demand_percentage_above_base_capacity=0,
            spot_allocation_strategy="capacity-optimized",
        ),
    ),
)

# Export important values.
pulumi.export("bucket_name", bucket.id)
pulumi.export("asg_name", asg.name)
pulumi.export("security_group_id", security_group.id)
pulumi.export("vpc_id", default_vpc.id)
pulumi.export("subnet_ids", default_subnets.ids)
pulumi.export("ami_id", dl_ami.id)
pulumi.export("ami_name", dl_ami.name)
pulumi.export("dynamodb_table_arn", dynamodb_table_arn)

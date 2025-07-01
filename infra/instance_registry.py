"""Pulumi component for instance registry using DynamoDB."""

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions


class InstanceRegistry:
    """Pulumi component for creating a DynamoDB-based instance registry system."""

    def __init__(
        self,
        name: str,
        instance_role_name: str,
        dynamodb_table_name: str,
        ttl_hours: int = 1,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize InstanceRegistry.

        Args:
            name: Base name for created resources
            instance_role_name: Name of the IAM role used by EC2 instances
            dynamodb_table_name: Name of the existing DynamoDB table to use
            ttl_hours: Time-to-live in hours for instance entries (for auto-cleanup)
            opts: Optional resource options
        """
        # Store ttl_hours as an instance variable
        self.ttl_hours = ttl_hours
        self.table_name = dynamodb_table_name

        # Create IAM policy for DynamoDB access
        self.dynamodb_policy = aws.iam.Policy(
            f"{name}-instance-registry-policy",
            description="Allows EC2 instances to register themselves in DynamoDB",
            policy=pulumi.Output.all(
                table_name=dynamodb_table_name,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:PutItem",
                                    "dynamodb:GetItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                ],
                                "Resource": f"arn:aws:dynamodb:*:*:table/{args['table_name']}",
                            }
                        ],
                    }
                )
            ),
            opts=opts,
        )

        # Create IAM policy for EC2 instance introspection
        self.ec2_introspection_policy = aws.iam.Policy(
            f"{name}-ec2-introspection-policy",
            description="Allows EC2 instances to query information about themselves and other instances",
            policy="""
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ec2:DescribeInstances",
                            "ec2:DescribeTags",
                            "ec2:DescribeInstanceTypes",
                            "autoscaling:DescribeAutoScalingGroups",
                            "autoscaling:DescribeAutoScalingInstances"
                        ],
                        "Resource": "*"
                    }
                ]
            }
            """,
            opts=opts,
        )

        # Attach policies to the instance role
        self.dynamo_policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-instance-registry-attachment",
            role=instance_role_name,
            policy_arn=self.dynamodb_policy.arn,
            opts=opts,
        )

        self.ec2_policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-ec2-introspection-attachment",
            role=instance_role_name,
            policy_arn=self.ec2_introspection_policy.arn,
            opts=opts,
        )

    def create_registration_script(self, leader_election_enabled: bool = True) -> str:
        """Generate a shell script for instance self-registration.

        Args:
            leader_election_enabled: Whether to include leader election logic

        Returns:
            Shell script for instance registration
        """
        script = f"""#!/bin/bash
# Instance self-registration script

# Get instance metadata
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type)
AZ=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone)
REGION=$(echo $AZ | sed 's/[a-z]$//')
IP_ADDRESS=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
IS_SPOT=$(curl -s http://169.254.169.254/latest/meta-data/instance-life-cycle | grep -q "spot" && echo "true" || echo "false")

# Detect GPUs
if command -v nvidia-smi &>/dev/null; then
    GPU_COUNT=$(nvidia-smi --query-gpu=count --format=csv,noheader | grep -v "No devices were found" | wc -l || echo "0")
else
    GPU_COUNT=0
fi

# Calculate TTL (current time + {3600 * self.ttl_hours} seconds)
TTL=$(($(date +%s) + {3600 * self.ttl_hours}))

# Register instance in DynamoDB using the main table schema
aws dynamodb put-item \\
    --region $REGION \\
    --table-name {self.table_name} \\
    --item '{{"PK":{{"S":"INSTANCE#$INSTANCE_ID"}}, \\
             "SK":{{"S":"INSTANCE"}}, \\
             "GSI1PK":{{"S":"STATUS#running"}}, \\
             "GSI1SK":{{"S":"INSTANCE#$INSTANCE_ID"}}, \\
             "TYPE":{{"S":"INSTANCE"}}, \\
             "instance_type":{{"S":"$INSTANCE_TYPE"}}, \\
             "gpu_count":{{"N":"$GPU_COUNT"}}, \\
             "status":{{"S":"running"}}, \\
             "launched_at":{{"S":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}}, \\
             "ip_address":{{"S":"$IP_ADDRESS"}}, \\
             "availability_zone":{{"S":"$AZ"}}, \\
             "is_spot":{{"BOOL":$IS_SPOT}}, \\
             "health_status":{{"S":"healthy"}}, \\
             "TimeToLive":{{"N":"$TTL"}}}}'

"""

        # Add leader election logic if enabled
        if leader_election_enabled:
            script += """
# Leader election logic
LEADER_KEY='{"PK":{"S":"INSTANCE#LEADER"},"SK":{"S":"LEADER"}}'
LEADER_UPDATE='{"PK":{"S":"INSTANCE#LEADER"},"SK":{"S":"LEADER"},"instance_id":{"S":"$INSTANCE_ID"},"elected_at":{"S":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}}'

# Try to become leader
aws dynamodb put-item \\
    --region $REGION \\
    --table-name {self.table_name} \\
    --item "$LEADER_UPDATE" \\
    --condition-expression "attribute_not_exists(PK) OR instance_id = :id" \\
    --expression-attribute-values '{{":id":{{"S":"$INSTANCE_ID"}}}}' \\
    && echo "Elected as leader" \\
    || echo "Failed to become leader"

# Update leader status in instance record
aws dynamodb update-item \\
    --region $REGION \\
    --table-name {self.table_name} \\
    --key '{{"PK":{{"S":"INSTANCE#$INSTANCE_ID"}},"SK":{{"S":"INSTANCE"}}}}' \\
    --update-expression "SET is_leader = :leader" \\
    --expression-attribute-values '{{":leader":{{"BOOL":true}}}}' \\
    --condition-expression "attribute_exists(PK)" \\
    || echo "Failed to update leader status"
"""

        return script


# Remove these lines that were causing issues
# Get stack-specific configuration
# stack = pulumi.get_stack()
# config = pulumi.Config()

# Create the KeyPair resource
# key_pair = aws.ec2.KeyPair("ml-training-key-pair",
#     key_name=f"ml-training-{stack}",
#     # In CI/CD, this would be provided via a secure environment variable
#     # or configuration that's specific to each environment (dev/prod)
#     public_key=config.require("ssh_public_key"),
#     tags={
#         "Name": f"ML-Training-KeyPair-{stack}",
#         "Environment": stack,
#         "ManagedBy": "Pulumi",
#     }
# )

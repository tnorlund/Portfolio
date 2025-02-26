"""Pulumi component for instance registry using DynamoDB."""

import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions
from typing import Optional
import json


class InstanceRegistry:
    """Pulumi component for creating a DynamoDB-based instance registry system."""

    def __init__(
        self,
        name: str,
        instance_role_name: str,
        ttl_hours: int = 1,
        read_capacity: int = 5,
        write_capacity: int = 5,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize InstanceRegistry.

        Args:
            name: Base name for created resources
            instance_role_name: Name of the IAM role used by EC2 instances
            ttl_hours: Time-to-live in hours for instance entries (for auto-cleanup)
            read_capacity: DynamoDB read capacity units
            write_capacity: DynamoDB write capacity units
            opts: Optional resource options
        """
        # Store ttl_hours as an instance variable
        self.ttl_hours = ttl_hours

        # Create DynamoDB table for instance registry
        self.table = aws.dynamodb.Table(
            f"{name}-instance-registry",
            attributes=[
                aws.dynamodb.TableAttributeArgs(
                    name="instance_id",
                    type="S",
                ),
                aws.dynamodb.TableAttributeArgs(
                    name="status",
                    type="S",
                ),
            ],
            billing_mode="PROVISIONED",
            hash_key="instance_id",
            global_secondary_indexes=[
                aws.dynamodb.TableGlobalSecondaryIndexArgs(
                    name="StatusIndex",
                    hash_key="status",
                    projection_type="ALL",
                    read_capacity=read_capacity,
                    write_capacity=write_capacity,
                ),
            ],
            read_capacity=read_capacity,
            write_capacity=write_capacity,
            ttl=aws.dynamodb.TableTtlArgs(
                attribute_name="ttl",
                enabled=True,
            ),
            tags={
                "Name": f"{name}-instance-registry",
                "Purpose": "ML Training Instance Registry",
            },
            opts=opts,
        )

        # Create IAM policy for DynamoDB access
        self.dynamo_policy = aws.iam.Policy(
            f"{name}-instance-registry-policy",
            description="Allows EC2 instances to access the instance registry DynamoDB table",
            policy=pulumi.Output.all(table_arn=self.table.arn).apply(
                lambda args: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:PutItem",
                                "dynamodb:GetItem",
                                "dynamodb:UpdateItem",
                                "dynamodb:DeleteItem",
                                "dynamodb:Query",
                                "dynamodb:Scan"
                            ],
                            "Resource": "{args['table_arn']}"
                        }},
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:Query"
                            ],
                            "Resource": "{args['table_arn']}/index/StatusIndex"
                        }}
                    ]
                }}"""
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
            policy_arn=self.dynamo_policy.arn,
            opts=opts,
        )

        self.ec2_policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-ec2-introspection-attachment",
            role=instance_role_name,
            policy_arn=self.ec2_introspection_policy.arn,
            opts=opts,
        )

        # Export the table name and ARN
        self.table_name = self.table.name
        self.table_arn = self.table.arn

        pulumi.export(f"{name}_instance_registry_table_name", self.table_name)
        pulumi.export(f"{name}_instance_registry_table_arn", self.table_arn)

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

# Detect GPUs
if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | tr '\\n' '|' | sed 's/|$//')
    GPU_COUNT=$(nvidia-smi --query-gpu=count --format=csv,noheader)
else
    GPU_INFO="none"
    GPU_COUNT=0
fi

# Calculate TTL (current time + {3600 * self.ttl_hours} seconds)
TTL=$(($(date +%s) + {3600 * self.ttl_hours}))

# Register instance in DynamoDB
aws dynamodb put-item \\
    --region $REGION \\
    --table-name $INSTANCE_REGISTRY_TABLE \\
    --item '{{"instance_id":{{"S":"'$INSTANCE_ID'"}}, \\
             "status":{{"S":"running"}}, \\
             "instance_type":{{"S":"'$INSTANCE_TYPE'"}}, \\
             "availability_zone":{{"S":"'$AZ'"}}, \\
             "registration_time":{{"N":"'$(date +%s)'"}}, \\
             "ttl":{{"N":"'$TTL'"}}, \\
             "gpu_info":{{"S":"'$GPU_INFO'"}}, \\
             "gpu_count":{{"N":"'$GPU_COUNT'"}}, \\
             "is_leader":{{"BOOL":false}}}}'

"""

        # Add leader election logic if enabled
        if leader_election_enabled:
            script += """
# Leader election function
elect_leader() {
    # Check if there's already a leader
    LEADER_COUNT=$(aws dynamodb scan \\
        --table-name $INSTANCE_REGISTRY_TABLE \\
        --filter-expression "is_leader = :true" \\
        --expression-attribute-values '{":true":{"BOOL":true}}' \\
        --select COUNT \\
        --query "Count" \\
        --output text)
    
    # If no leader, try to become one
    if [ "$LEADER_COUNT" -eq "0" ]; then
        # Use conditional update to handle race conditions
        aws dynamodb update-item \\
            --table-name $INSTANCE_REGISTRY_TABLE \\
            --key '{"instance_id":{"S":"'$INSTANCE_ID'"}}' \\
            --update-expression "SET is_leader = :true" \\
            --condition-expression "attribute_exists(instance_id)" \\
            --expression-attribute-values '{":true":{"BOOL":true}}' \\
            --return-values ALL_NEW \\
            &>/dev/null
        
        if [ $? -eq 0 ]; then
            echo "This instance is now the leader"
            # Set up leader-specific resources here
        else
            echo "Leader election failed, another instance may have become leader first"
        fi
    else
        echo "A leader already exists"
    fi
}

# Try to elect a leader
elect_leader

# Set up a heartbeat to maintain registration and potentially take over leadership
(
    while true; do
        # Update TTL
        TTL=$(($(date +%s) + 3600))
        aws dynamodb update-item \\
            --table-name $INSTANCE_REGISTRY_TABLE \\
            --key '{"instance_id":{"S":"'$INSTANCE_ID'"}}' \\
            --update-expression "SET ttl = :ttl, last_heartbeat = :now" \\
            --expression-attribute-values '{":ttl":{"N":"'$TTL'"}, ":now":{"N":"'$(date +%s)'"}}' \\
            &>/dev/null
        
        # Check if leader is needed
        LEADER_COUNT=$(aws dynamodb scan \\
            --table-name $INSTANCE_REGISTRY_TABLE \\
            --filter-expression "is_leader = :true" \\
            --expression-attribute-values '{":true":{"BOOL":true}}' \\
            --select COUNT \\
            --query "Count" \\
            --output text)
        
        if [ "$LEADER_COUNT" -eq "0" ]; then
            elect_leader
        fi
        
        sleep 60
    done
) &
"""

        # Add cleanup on instance shutdown
        script += """
# Deregister on shutdown
cleanup() {
    echo "Deregistering instance from registry..."
    aws dynamodb delete-item \\
        --table-name $INSTANCE_REGISTRY_TABLE \\
        --key '{"instance_id":{"S":"'$INSTANCE_ID'"}}'
    exit 0
}

# Register the cleanup function on termination signals
trap cleanup SIGTERM SIGINT
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

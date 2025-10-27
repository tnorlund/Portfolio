"""
Infrastructure for container-based merchant validation Lambda.

This component creates a Lambda function that:
- Uses direct EFS access to ChromaDB (read-only)
- Validates merchant data using ChromaDB similarity + Google Places
- Creates ReceiptMetadata in DynamoDB
- Triggers NDJSON embedding process automatically
"""

from typing import Optional
import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output


class MerchantValidationContainer(ComponentResource):
    """
    Container-based Lambda for merchant validation with EFS and NDJSON trigger.
    """

    def __init__(
        self,
        name: str,
        *,
        # Container image
        image_uri: pulumi.Input[str],
        # EFS configuration
        efs_access_point_arn: pulumi.Input[str],
        # VPC configuration
        vpc_id: pulumi.Input[str],
        subnet_ids: pulumi.Input[list[str]],
        lambda_security_group_id: pulumi.Input[str],
        # DynamoDB
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        # S3
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        # SQS
        embed_ndjson_queue_url: pulumi.Input[str],
        embed_ndjson_queue_arn: pulumi.Input[str],
        # API Keys (secrets)
        google_places_api_key: pulumi.Input[str],
        openai_api_key: pulumi.Input[str],
        # Optional
        memory_size: int = 2048,
        timeout: int = 900,
        ephemeral_storage_size: int = 10240,
        reserved_concurrent_executions: Optional[int] = 5,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            "custom:merchant-validation:Container",
            name,
            None,
            opts,
        )

        # Create IAM role for Lambda
        self.lambda_role = aws.iam.Role(
            f"{name}-role",
            assume_role_policy="""{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }""",
            tags={
                "Name": f"{name}-role",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-basic-execution",
            role=self.lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Attach VPC execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-vpc-execution",
            role=self.lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Create inline policy for DynamoDB, S3, SQS, and EFS access
        self.lambda_policy = aws.iam.RolePolicy(
            f"{name}-policy",
            role=self.lambda_role.id,
            policy=pulumi.Output.all(
                dynamodb_table_arn,
                chromadb_bucket_arn,
                chromadb_bucket_name,
                embed_ndjson_queue_arn,
            ).apply(
                lambda args: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:GetItem",
                                "dynamodb:PutItem",
                                "dynamodb:UpdateItem",
                                "dynamodb:Query",
                                "dynamodb:Scan"
                            ],
                            "Resource": [
                                "{args[0]}",
                                "{args[0]}/index/*"
                            ]
                        }},
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:ListBucket"
                            ],
                            "Resource": [
                                "{args[1]}",
                                "{args[1]}/receipts/*"
                            ]
                        }},
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "sqs:SendMessage",
                                "sqs:GetQueueUrl",
                                "sqs:GetQueueAttributes"
                            ],
                            "Resource": "{args[3]}"
                        }},
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "elasticfilesystem:ClientMount",
                                "elasticfilesystem:ClientWrite",
                                "elasticfilesystem:DescribeMountTargets"
                            ],
                            "Resource": "*"
                        }},
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer"
                            ],
                            "Resource": "*"
                        }}
                    ]
                }}"""
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Create Lambda function
        self.lambda_function = aws.lambda_.Function(
            f"{name}-function",
            package_type="Image",
            image_uri=image_uri,
            role=self.lambda_role.arn,
            timeout=timeout,
            memory_size=memory_size,
            ephemeral_storage=aws.lambda_.FunctionEphemeralStorageArgs(
                size=ephemeral_storage_size,
            ),
            vpc_config=aws.lambda_.FunctionVpcConfigArgs(
                subnet_ids=subnet_ids,
                security_group_ids=[lambda_security_group_id],
            ),
            file_system_configs=[
                aws.lambda_.FunctionFileSystemConfigArgs(
                    arn=efs_access_point_arn,
                    local_mount_path="/mnt/chroma",
                )
            ],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    # DynamoDB
                    "DYNAMO_TABLE_NAME": dynamodb_table_name,
                    # ChromaDB
                    "CHROMA_ROOT": "/mnt/chroma",
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    # SQS
                    "EMBED_NDJSON_QUEUE_URL": embed_ndjson_queue_url,
                    # API Keys
                    "GOOGLE_PLACES_API_KEY": google_places_api_key,
                    "OPENAI_API_KEY": openai_api_key,
                }
            ),
            reserved_concurrent_executions=reserved_concurrent_executions,
            tags={
                "Name": f"{name}-function",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
                "Component": "MerchantValidation",
            },
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.lambda_policy],
            ),
        )

        # Export outputs
        self.function_arn = self.lambda_function.arn
        self.function_name = self.lambda_function.name
        self.role_arn = self.lambda_role.arn

        self.register_outputs(
            {
                "function_arn": self.function_arn,
                "function_name": self.function_name,
                "role_arn": self.role_arn,
            }
        )


def create_merchant_validation_container(
    name: str,
    *,
    image_uri: pulumi.Input[str],
    efs_access_point_arn: pulumi.Input[str],
    vpc_id: pulumi.Input[str],
    subnet_ids: pulumi.Input[list[str]],
    lambda_security_group_id: pulumi.Input[str],
    dynamodb_table_name: pulumi.Input[str],
    dynamodb_table_arn: pulumi.Input[str],
    chromadb_bucket_name: pulumi.Input[str],
    chromadb_bucket_arn: pulumi.Input[str],
    embed_ndjson_queue_url: pulumi.Input[str],
    embed_ndjson_queue_arn: pulumi.Input[str],
    google_places_api_key: pulumi.Input[str],
    openai_api_key: pulumi.Input[str],
    memory_size: int = 2048,
    timeout: int = 900,
    ephemeral_storage_size: int = 10240,
    reserved_concurrent_executions: Optional[int] = 5,
) -> MerchantValidationContainer:
    """
    Factory function to create a merchant validation container Lambda.

    Args:
        name: Resource name prefix
        image_uri: Docker image URI from ECR
        efs_access_point_arn: EFS access point ARN for ChromaDB
        vpc_id: VPC ID
        subnet_ids: List of subnet IDs (private subnets with NAT)
        lambda_security_group_id: Security group ID for Lambda
        dynamodb_table_name: DynamoDB table name
        dynamodb_table_arn: DynamoDB table ARN
        chromadb_bucket_name: S3 bucket name for ChromaDB
        chromadb_bucket_arn: S3 bucket ARN for ChromaDB
        embed_ndjson_queue_url: SQS queue URL for embedding jobs
        embed_ndjson_queue_arn: SQS queue ARN for embedding jobs
        google_places_api_key: Google Places API key
        openai_api_key: OpenAI API key
        memory_size: Lambda memory in MB (default: 2048)
        timeout: Lambda timeout in seconds (default: 900)
        ephemeral_storage_size: Ephemeral storage in MB (default: 10240)
        reserved_concurrent_executions: Reserved concurrency (default: 5)

    Returns:
        MerchantValidationContainer instance
    """
    return MerchantValidationContainer(
        name,
        image_uri=image_uri,
        efs_access_point_arn=efs_access_point_arn,
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        lambda_security_group_id=lambda_security_group_id,
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        chromadb_bucket_name=chromadb_bucket_name,
        chromadb_bucket_arn=chromadb_bucket_arn,
        embed_ndjson_queue_url=embed_ndjson_queue_url,
        embed_ndjson_queue_arn=embed_ndjson_queue_arn,
        google_places_api_key=google_places_api_key,
        openai_api_key=openai_api_key,
        memory_size=memory_size,
        timeout=timeout,
        ephemeral_storage_size=ephemeral_storage_size,
        reserved_concurrent_executions=reserved_concurrent_executions,
    )


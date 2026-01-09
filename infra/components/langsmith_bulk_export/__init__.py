"""
LangSmith Bulk Export Infrastructure.

This component creates:
- S3 bucket for Parquet exports from LangSmith
- IAM Role for LangSmith cross-account access
- Setup Lambda to register destination with LangSmith API
- Export trigger Lambda for manual bulk exports
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileArchive,
    Output,
    ResourceOptions,
)

# Get stack configuration
stack = pulumi.get_stack()

# Reference the lambdas directory
LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "lambdas")


class LangSmithBulkExport(ComponentResource):
    """Infrastructure for LangSmith bulk export to S3."""

    def __init__(
        self,
        name: str,
        *,
        project_name: str,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:langsmith-bulk-export:{name}",
            name,
            None,
            opts,
        )

        self.project_name = project_name

        # ============================================================
        # S3 Export Bucket
        # ============================================================
        self.export_bucket = aws.s3.Bucket(
            f"{name}-export-bucket",
            force_destroy=True,
            tags={
                "Name": f"{name}-export-bucket",
                "Purpose": "LangSmithBulkExport",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketOwnershipControls(
            f"{name}-export-bucket-ownership",
            bucket=self.export_bucket.id,
            rule=aws.s3.BucketOwnershipControlsRuleArgs(
                object_ownership="BucketOwnerEnforced"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Block all public access - cross-account IAM access works via IAM policies
        aws.s3.BucketPublicAccessBlock(
            f"{name}-export-bucket-public-access",
            bucket=self.export_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # IAM User for LangSmith Access (LangSmith uses access keys)
        # ============================================================
        self.langsmith_user = aws.iam.User(
            f"{name}-langsmith-user",
            tags={
                "Name": f"{name}-langsmith-user",
                "Purpose": "LangSmith bulk export S3 access",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Policy allowing LangSmith to write to the export bucket
        # LangSmith validates access by testing various S3 operations
        aws.iam.UserPolicy(
            f"{name}-langsmith-s3-policy",
            user=self.langsmith_user.name,
            policy=self.export_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:GetObject",
                                    "s3:DeleteObject",
                                    "s3:HeadObject",
                                    "s3:GetObjectAcl",
                                    "s3:PutObjectAcl",
                                    "s3:AbortMultipartUpload",
                                    "s3:ListMultipartUploadParts",
                                ],
                                "Resource": f"{arn}/*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:ListBucket",
                                    "s3:HeadBucket",
                                    "s3:GetBucketLocation",
                                    "s3:GetBucketAcl",
                                    "s3:GetBucketVersioning",
                                    "s3:ListBucketMultipartUploads",
                                ],
                                "Resource": arn,
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create access keys for the IAM user
        self.langsmith_access_key = aws.iam.AccessKey(
            f"{name}-langsmith-access-key",
            user=self.langsmith_user.name,
            opts=ResourceOptions(parent=self),
        )

        # Store credentials in Secrets Manager
        self.langsmith_credentials_secret = aws.secretsmanager.Secret(
            f"{name}-langsmith-credentials",
            description=f"LangSmith S3 credentials for {project_name}",
            tags={
                "Name": f"{name}-langsmith-credentials",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Store the actual credentials
        aws.secretsmanager.SecretVersion(
            f"{name}-langsmith-credentials-version",
            secret_id=self.langsmith_credentials_secret.id,
            secret_string=Output.all(
                self.langsmith_access_key.id, self.langsmith_access_key.secret
            ).apply(
                lambda args: json.dumps(
                    {
                        "access_key_id": args[0],
                        "secret_access_key": args[1],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # IAM Role for Lambda Functions
        # ============================================================
        self.lambda_role = aws.iam.Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Name": f"{name}-lambda-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution
        aws.iam.RolePolicyAttachment(
            f"{name}-basic-execution",
            role=self.lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # SSM Parameter Store access (for storing/retrieving destination_id)
        aws.iam.RolePolicy(
            f"{name}-ssm-policy",
            role=self.lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ssm:GetParameter",
                                "ssm:PutParameter",
                                "ssm:DeleteParameter",
                            ],
                            "Resource": f"arn:aws:ssm:*:*:parameter/langsmith/{stack}/*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Secrets Manager access (for reading S3 credentials)
        aws.iam.RolePolicy(
            f"{name}-secrets-policy",
            role=self.lambda_role.id,
            policy=self.langsmith_credentials_secret.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "secretsmanager:GetSecretValue",
                                ],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Read from export bucket (for cache generators and setup)
        aws.iam.RolePolicy(
            f"{name}-export-bucket-read-policy",
            role=self.lambda_role.id,
            policy=self.export_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:ListBucket",
                                    "s3:GetBucketLocation",
                                ],
                                "Resource": [arn, f"{arn}/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Get LangSmith Service API key and optional tenant ID from Pulumi config
        # Service API keys have permissions for bulk export operations
        # Tenant ID is optional - if not set, uses default workspace
        config = pulumi.Config("portfolio")
        langsmith_api_key = config.require_secret("LANGSMITH_SERVICE_API_KEY")
        langsmith_tenant_id = config.get("LANGSMITH_TENANT_ID") or ""

        # ============================================================
        # Setup Lambda (One-time destination registration)
        # ============================================================
        self.setup_lambda = aws.lambda_.Function(
            f"{name}-setup-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="setup.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "LANGCHAIN_API_KEY": langsmith_api_key,
                    "LANGSMITH_TENANT_ID": langsmith_tenant_id,
                    "EXPORT_BUCKET": self.export_bucket.id,
                    "S3_CREDENTIALS_SECRET_ARN": self.langsmith_credentials_secret.arn,
                    "LANGSMITH_PROJECT": project_name,
                    "STACK": stack,
                }
            ),
            memory_size=256,
            timeout=60,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Log group for setup Lambda
        aws.cloudwatch.LogGroup(
            f"{name}-setup-lambda-logs",
            name=self.setup_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Export Trigger Lambda (Manual invocation)
        # ============================================================
        self.trigger_lambda = aws.lambda_.Function(
            f"{name}-trigger-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="trigger_export.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "LANGCHAIN_API_KEY": langsmith_api_key,
                    "LANGSMITH_TENANT_ID": langsmith_tenant_id,
                    "LANGSMITH_PROJECT": project_name,
                    "STACK": stack,
                }
            ),
            memory_size=256,
            timeout=60,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Log group for trigger Lambda
        aws.cloudwatch.LogGroup(
            f"{name}-trigger-lambda-logs",
            name=self.trigger_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Exports
        # ============================================================
        self.register_outputs(
            {
                "export_bucket_name": self.export_bucket.id,
                "export_bucket_arn": self.export_bucket.arn,
                "langsmith_user_arn": self.langsmith_user.arn,
                "credentials_secret_arn": self.langsmith_credentials_secret.arn,
                "setup_lambda_arn": self.setup_lambda.arn,
                "setup_lambda_name": self.setup_lambda.name,
                "trigger_lambda_arn": self.trigger_lambda.arn,
                "trigger_lambda_name": self.trigger_lambda.name,
            }
        )


def create_langsmith_bulk_export(
    project_name: str,
    opts: Optional[ResourceOptions] = None,
) -> LangSmithBulkExport:
    """Factory function to create LangSmith bulk export infrastructure."""
    return LangSmithBulkExport(
        f"langsmith-export-{stack}",
        project_name=project_name,
        opts=opts,
    )

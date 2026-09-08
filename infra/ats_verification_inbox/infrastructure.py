"""Isolated ATS verification inbox on the existing SES receiving plane.

The component deliberately adds a rule to the receipt inbox's active rule set
instead of creating or activating another set: SES permits only one active
receipt rule set per account and region.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Input, ResourceOptions

LAMBDA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lambdas"
)


class AtsVerificationInbox(ComponentResource):
    """SES -> S3 -> code extractor -> DynamoDB, plus a read-only MCP Lambda."""

    def __init__(
        self,
        name: str,
        *,
        domain: str,
        rule_set_name: Input[str],
        recipient_localpart: str = "ats",
        raw_retention_days: int = 1,
        tags: Optional[dict[str, str]] = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__(
            "portfolio:infra:AtsVerificationInbox", name, None, opts
        )
        stack = pulumi.get_stack()
        child = ResourceOptions(parent=self)
        region = aws.get_region().region
        account_id = aws.get_caller_identity().account_id
        tags = {
            "Environment": stack,
            "Component": "ats-verification-inbox",
            "DataClassification": "authentication-secret",
            **(tags or {}),
        }

        self.address = f"{recipient_localpart}@{domain}"
        rule_name = f"{name}-store-{stack}"
        rule_arn = pulumi.Output.format(
            "arn:aws:ses:{}:{}:receipt-rule-set/{}:receipt-rule/{}",
            region,
            account_id,
            rule_set_name,
            rule_name,
        )

        self.table = aws.dynamodb.Table(
            f"{name}-codes",
            attributes=[
                aws.dynamodb.TableAttributeArgs(name="provider", type="S"),
                aws.dynamodb.TableAttributeArgs(
                    name="received_at_id", type="S"
                ),
            ],
            hash_key="provider",
            range_key="received_at_id",
            billing_mode="PAY_PER_REQUEST",
            ttl=aws.dynamodb.TableTtlArgs(
                attribute_name="expires_at", enabled=True
            ),
            # Deliberately omit point-in-time recovery: these are ephemeral
            # authentication secrets, so recoverability beyond TTL is a bug.
            server_side_encryption=(
                aws.dynamodb.TableServerSideEncryptionArgs(enabled=True)
            ),
            tags=tags,
            opts=child,
        )

        self.bucket = aws.s3.Bucket(
            f"{name}-mail",
            bucket=f"{name}-mail-{stack}-{account_id}",
            # Development-only ephemeral authentication data should be erased
            # when the feature is explicitly removed. Retain the normal S3
            # deletion guard if this component is ever enabled in production.
            force_destroy=stack != "prod",
            tags=tags,
            opts=child,
        )
        aws.s3.BucketPublicAccessBlock(
            f"{name}-mail-pab",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=child,
        )
        aws.s3.BucketServerSideEncryptionConfiguration(
            f"{name}-mail-sse",
            bucket=self.bucket.id,
            rules=[
                {
                    "apply_server_side_encryption_by_default": {
                        "sse_algorithm": "AES256"
                    }
                }
            ],
            opts=child,
        )
        versioning = aws.s3.BucketVersioning(
            f"{name}-mail-versioning",
            bucket=self.bucket.id,
            versioning_configuration={"status": "Enabled"},
            opts=child,
        )
        aws.s3.BucketLifecycleConfiguration(
            f"{name}-mail-lifecycle",
            bucket=self.bucket.id,
            rules=[
                {
                    "id": "expire-raw-email",
                    "status": "Enabled",
                    "filter": {"prefix": "raw/"},
                    "expiration": {"days": raw_retention_days},
                    "noncurrent_version_expiration": {
                        "noncurrent_days": raw_retention_days
                    },
                }
            ],
            opts=ResourceOptions(parent=self, depends_on=[versioning]),
        )
        bucket_policy = aws.s3.BucketPolicy(
            f"{name}-mail-ses-policy",
            bucket=self.bucket.id,
            policy=pulumi.Output.all(
                self.bucket.arn, rule_arn, account_id
            ).apply(
                lambda values: pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "DenyInsecureTransport",
                                "Effect": "Deny",
                                "Principal": "*",
                                "Action": "s3:*",
                                "Resource": [
                                    values[0],
                                    f"{values[0]}/*",
                                ],
                                "Condition": {
                                    "Bool": {"aws:SecureTransport": "false"}
                                },
                            },
                            {
                                "Sid": "AllowExactSESRule",
                                "Effect": "Allow",
                                "Principal": {"Service": "ses.amazonaws.com"},
                                "Action": "s3:PutObject",
                                "Resource": f"{values[0]}/raw/*",
                                "Condition": {
                                    "StringEquals": {
                                        "aws:SourceAccount": values[2],
                                        "aws:SourceArn": values[1],
                                    }
                                },
                            },
                        ],
                    }
                )
            ),
            opts=child,
        )

        ingest_role = aws.iam.Role(
            f"{name}-ingest-role",
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
            tags=tags,
            opts=child,
        )
        ingest_logs = aws.iam.RolePolicyAttachment(
            f"{name}-ingest-logs",
            role=ingest_role.name,
            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
            opts=child,
        )
        self.dlq = aws.sqs.Queue(
            f"{name}-ingest-dlq",
            message_retention_seconds=1209600,
            sqs_managed_sse_enabled=True,
            tags=tags,
            opts=child,
        )
        ingest_policy = aws.iam.RolePolicy(
            f"{name}-ingest-data",
            role=ingest_role.id,
            policy=pulumi.Output.all(
                self.bucket.arn, self.table.arn, self.dlq.arn
            ).apply(
                lambda values: pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                ],
                                "Resource": f"{values[0]}/raw/*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": "dynamodb:PutItem",
                                "Resource": values[1],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": values[2],
                            },
                        ],
                    }
                )
            ),
            opts=child,
        )
        self.ingest_lambda = aws.lambda_.Function(
            f"{name}-ingest",
            runtime="python3.13",
            handler="ingest.lambda_handler",
            role=ingest_role.arn,
            timeout=30,
            memory_size=192,
            reserved_concurrent_executions=3,
            code=pulumi.AssetArchive(
                {
                    "ingest.py": pulumi.FileAsset(
                        os.path.join(LAMBDA_DIR, "ingest.py")
                    )
                }
            ),
            environment={"variables": {"TABLE_NAME": self.table.name}},
            tags=tags,
            opts=ResourceOptions(
                parent=self, depends_on=[ingest_policy, ingest_logs]
            ),
        )
        async_config = aws.lambda_.FunctionEventInvokeConfig(
            f"{name}-ingest-async",
            function_name=self.ingest_lambda.name,
            maximum_retry_attempts=2,
            maximum_event_age_in_seconds=3600,
            destination_config={"on_failure": {"destination": self.dlq.arn}},
            opts=child,
        )
        aws.cloudwatch.MetricAlarm(
            f"{name}-ingest-dlq-depth",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ApproximateNumberOfMessagesVisible",
            namespace="AWS/SQS",
            period=300,
            statistic="Maximum",
            threshold=0,
            alarm_description=(
                "ATS verification ingest failures require inspection before "
                "the 14-day DLQ retention expires."
            ),
            dimensions={"QueueName": self.dlq.name},
            treat_missing_data="notBreaching",
            tags=tags,
            opts=child,
        )
        invoke_permission = aws.lambda_.Permission(
            f"{name}-ingest-s3-invoke",
            action="lambda:InvokeFunction",
            function=self.ingest_lambda.name,
            principal="s3.amazonaws.com",
            source_arn=self.bucket.arn,
            source_account=account_id,
            opts=child,
        )
        notification = aws.s3.BucketNotification(
            f"{name}-mail-notify",
            bucket=self.bucket.id,
            lambda_functions=[
                {
                    "lambda_function_arn": self.ingest_lambda.arn,
                    "events": ["s3:ObjectCreated:*"],
                    "filter_prefix": "raw/",
                }
            ],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.ingest_lambda,
                    invoke_permission,
                    async_config,
                ],
            ),
        )

        self.receipt_rule = aws.ses.ReceiptRule(
            f"{name}-store-rule",
            name=rule_name,
            rule_set_name=rule_set_name,
            recipients=[self.address],
            enabled=True,
            scan_enabled=True,
            tls_policy="Require",
            s3_actions=[
                {
                    "bucket_name": self.bucket.bucket,
                    "object_key_prefix": "raw/",
                    "position": 1,
                }
            ],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    versioning,
                    bucket_policy,
                    notification,
                ],
            ),
        )

        mcp_role = aws.iam.Role(
            f"{name}-mcp-role",
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
            tags=tags,
            opts=child,
        )
        mcp_logs = aws.iam.RolePolicyAttachment(
            f"{name}-mcp-logs",
            role=mcp_role.name,
            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
            opts=child,
        )
        mcp_policy = aws.iam.RolePolicy(
            f"{name}-mcp-read",
            role=mcp_role.id,
            policy=self.table.arn.apply(
                lambda table_arn: pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "dynamodb:Query",
                                "Resource": table_arn,
                            }
                        ],
                    }
                )
            ),
            opts=child,
        )
        self.mcp_lambda = aws.lambda_.Function(
            f"{name}-mcp",
            runtime="python3.13",
            handler="mcp.lambda_handler",
            role=mcp_role.arn,
            timeout=10,
            memory_size=128,
            reserved_concurrent_executions=2,
            code=pulumi.AssetArchive(
                {
                    "mcp.py": pulumi.FileAsset(
                        os.path.join(LAMBDA_DIR, "mcp.py")
                    )
                }
            ),
            environment={
                "variables": {
                    "TABLE_NAME": self.table.name,
                    "ALLOWED_ORIGINS": (
                        "https://www.cursor.com,https://cursor.com,"
                        "https://claude.ai,https://claude.com"
                    ),
                }
            },
            tags=tags,
            opts=ResourceOptions(
                parent=self, depends_on=[mcp_policy, mcp_logs]
            ),
        )

        self.register_outputs(
            {
                "address": self.address,
                "bucket": self.bucket.bucket,
                "table": self.table.name,
                "ingest_lambda_arn": self.ingest_lambda.arn,
                "mcp_lambda_arn": self.mcp_lambda.arn,
                "dlq_url": self.dlq.url,
            }
        )

"""Scheduled Exporter Lambda: mirror EFS snapshots to S3.

Mounts EFS at /mnt/chroma and every run:
- For each collection in env COLLECTIONS (comma-separated), reads latest-pointer.txt
- Uploads the versioned snapshot dir to s3://BUCKET/<collection>/snapshot/timestamped/<version_id>/
- Updates latest-pointer.txt in S3
"""

from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions


class EfsExporter(ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        bucket_name: pulumi.Input[str],
        vpc_subnet_ids,
        lambda_security_group_id: pulumi.Input[str],
        efs_access_point_arn: pulumi.Input[str],
        schedule_expression: str = "cron(0/10 * * * ? *)",
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("chromadb:exporter:EfsExporter", name, None, opts)

        # IAM role
        role = aws.iam.Role(
            f"{name}-role",
            assume_role_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
            opts=ResourceOptions(parent=self),
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-basic",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-vpc",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=ResourceOptions(parent=self),
        )
        # S3 write access limited to target bucket
        aws.iam.RolePolicy(
            f"{name}-s3",
            role=role.id,
            policy=bucket_name.apply(
                lambda b: '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:PutObject","s3:PutObjectAcl","s3:ListBucket"],"Resource":["arn:aws:s3:::%s","arn:aws:s3:::%s/*"]}]}'
                % (b, b)
            ),
            opts=ResourceOptions(parent=self),
        )

        # Lambda code packaged from local file
        code = pulumi.AssetArchive(
            {
                "exporter.py": pulumi.FileAsset(
                    str(
                        Path(__file__).parent.parent
                        / "lambdas"
                        / "exporter.py"
                    )
                )
            }
        )

        vpc_cfg = aws.lambda_.FunctionVpcConfigArgs(
            subnet_ids=vpc_subnet_ids,
            security_group_ids=[lambda_security_group_id],
        )

        func = aws.lambda_.Function(
            f"{name}-fn",
            runtime="python3.12",
            handler="exporter.handler",
            role=role.arn,
            code=code,
            timeout=900,
            memory_size=512,
            vpc_config=vpc_cfg,
            file_system_config=aws.lambda_.FunctionFileSystemConfigArgs(
                arn=efs_access_point_arn,
                local_mount_path="/mnt/chroma",
            ),
            environment={
                "variables": {
                    "S3_BUCKET": bucket_name,
                    "COLLECTIONS": "lines,words",
                }
            },
            opts=ResourceOptions(parent=self),
        )

        # Schedule
        rule = aws.cloudwatch.EventRule(
            f"{name}-schedule",
            schedule_expression=schedule_expression,
            opts=ResourceOptions(parent=self),
        )
        target = aws.cloudwatch.EventTarget(
            f"{name}-target",
            rule=rule.name,
            arn=func.arn,
            opts=ResourceOptions(parent=self),
        )
        aws.lambda_.Permission(
            f"{name}-perm",
            action="lambda:InvokeFunction",
            function=func.name,
            principal="events.amazonaws.com",
            source_arn=rule.arn,
            opts=ResourceOptions(parent=self),
        )

        self.function_arn = func.arn
        self.rule_arn = rule.arn
        self.register_outputs(
            {"function_arn": self.function_arn, "rule_arn": self.rule_arn}
        )

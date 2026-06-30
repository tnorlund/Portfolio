"""
Web Analytics query layer (Glue + Athena over CloudFront access logs).

Makes the raw CloudFront access logs queryable without any new data pipeline:

- Glue Catalog database + external table over the existing CloudFront log
  bucket (gzip TSV, 2 header lines skipped).
- Athena workgroup with a dedicated results bucket (results expire on a
  lifecycle rule).
- A managed IAM policy granting read-only Athena/Glue/S3 access, intended to be
  attached to the MCP server's Lambda role so the ``analytics_*`` MCP tools can
  run queries.

All beacon-parsing / bot+WARP classification / timezone bucketing lives in the
MCP tool SQL (see ``receipt_mcp_server`` ``analytics_*`` tools), not in a Glue
view, so the logic stays version-controlled and reviewable.

CloudFront standard log field order is fixed by AWS; the column list below
matches it exactly.
"""

import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Input, Output, ResourceOptions

_HANDLER_DIR = str(Path(__file__).resolve().parent / "transform_lambda")

# Short aliases for verbose pulumi_aws Args classes (keeps lines <= 79 cols).
_SerDeInfo = aws.glue.CatalogTableStorageDescriptorSerDeInfoArgs
_LifecycleExpiry = aws.s3.BucketLifecycleConfigurationRuleExpirationArgs
_WgResultConfig = aws.athena.WorkgroupConfigurationResultConfigurationArgs

# CloudFront standard access-log columns, in order.
_CLOUDFRONT_COLUMNS = [
    ("date", "date"),
    ("time", "string"),
    ("location", "string"),
    ("bytes", "string"),
    ("request_ip", "string"),
    ("method", "string"),
    ("host", "string"),
    ("uri", "string"),
    ("status", "int"),
    ("referrer", "string"),
    ("user_agent", "string"),
    ("query_string", "string"),
    ("cookie", "string"),
    ("result_type", "string"),
    ("request_id", "string"),
    ("host_header", "string"),
    ("request_protocol", "string"),
    ("request_bytes", "string"),
    ("time_taken", "string"),
    ("xforwarded_for", "string"),
    ("ssl_protocol", "string"),
    ("ssl_cipher", "string"),
    ("response_result_type", "string"),
    ("http_version", "string"),
    ("fle_status", "string"),
    ("fle_encrypted_fields", "string"),
    ("c_port", "string"),
    ("time_to_first_byte", "string"),
    ("x_edge_detailed_result_type", "string"),
    ("sc_content_type", "string"),
    ("sc_content_len", "string"),
    ("sc_range_start", "string"),
    ("sc_range_end", "string"),
]


class WebAnalytics(ComponentResource):
    """Glue + Athena query layer over CloudFront access logs."""

    def __init__(
        self,
        name: str,
        *,
        cloudfront_logs_bucket: Input[str],
        log_prefix: str = "cloudfront/prod/",
        database_name: str = "portfolio_analytics",
        table_name: str = "cloudfront_logs_prod",
        results_expiration_days: int = 30,
        ga_service_account_key: Optional[Input[str]] = None,
        ga_property_id: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__("portfolio:analytics:WebAnalytics", name, None, opts)
        child = ResourceOptions(parent=self)

        bucket_name = Output.from_input(cloudfront_logs_bucket)
        logs_location = bucket_name.apply(
            lambda b: f"s3://{b}/{log_prefix}"
        )

        # --- Athena results bucket (short-lived query output) ---
        self.results_bucket = aws.s3.Bucket(
            f"{name}-results",
            opts=child,
        )
        aws.s3.BucketPublicAccessBlock(
            f"{name}-results-pab",
            bucket=self.results_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.results_bucket),
        )
        aws.s3.BucketLifecycleConfiguration(
            f"{name}-results-lifecycle",
            bucket=self.results_bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="expire-query-results",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix=""
                    ),
                    expiration=_LifecycleExpiry(
                        days=results_expiration_days
                    ),
                )
            ],
            opts=ResourceOptions(parent=self.results_bucket),
        )

        # --- Glue catalog database + external table ---
        self.database = aws.glue.CatalogDatabase(
            f"{name}-db",
            name=database_name,
            opts=child,
        )

        self.table = aws.glue.CatalogTable(
            f"{name}-cloudfront-table",
            name=table_name,
            database_name=self.database.name,
            table_type="EXTERNAL_TABLE",
            parameters={
                "EXTERNAL": "TRUE",
                "skip.header.line.count": "2",
                "classification": "csv",
            },
            storage_descriptor=aws.glue.CatalogTableStorageDescriptorArgs(
                location=logs_location,
                input_format="org.apache.hadoop.mapred.TextInputFormat",
                output_format=(
                    "org.apache.hadoop.hive.ql.io."
                    "HiveIgnoreKeyTextOutputFormat"
                ),
                columns=[
                    aws.glue.CatalogTableStorageDescriptorColumnArgs(
                        name=col, type=typ
                    )
                    for col, typ in _CLOUDFRONT_COLUMNS
                ],
                ser_de_info=_SerDeInfo(
                    serialization_library=(
                        "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
                    ),
                    parameters={
                        "field.delim": "\t",
                        "serialization.format": "\t",
                    },
                ),
            ),
            opts=child,
        )

        # --- Athena workgroup ---
        self.workgroup = aws.athena.Workgroup(
            f"{name}-workgroup",
            name=database_name,
            configuration=aws.athena.WorkgroupConfigurationArgs(
                enforce_workgroup_configuration=True,
                publish_cloudwatch_metrics_enabled=False,
                result_configuration=_WgResultConfig(
                    output_location=self.results_bucket.bucket.apply(
                        lambda b: f"s3://{b}/results/"
                    ),
                ),
            ),
            force_destroy=True,
            opts=child,
        )

        # --- Curated, partitioned Parquet table (the ETL output) ---
        self.curated_bucket = aws.s3.Bucket(f"{name}-curated", opts=child)
        aws.s3.BucketPublicAccessBlock(
            f"{name}-curated-pab",
            bucket=self.curated_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.curated_bucket),
        )
        curated_columns = [
            ("request_id", "string"),
            ("ts_utc", "timestamp"),
            ("request_ip", "string"),
            ("uri", "string"),
            ("status", "int"),
            ("referrer", "string"),
            ("user_agent", "string"),
            ("query_decoded", "string"),
            ("is_beacon", "boolean"),
            ("event", "string"),
            ("evt_path", "string"),
            ("sid", "string"),
            ("eid", "string"),
            ("is_warp", "boolean"),
            ("is_bot", "boolean"),
            ("edge_location", "string"),
        ]
        self.web_events_table = aws.glue.CatalogTable(
            f"{name}-web-events-table",
            name="web_events",
            database_name=self.database.name,
            table_type="EXTERNAL_TABLE",
            partition_keys=[
                aws.glue.CatalogTablePartitionKeyArgs(name="dt", type="string")
            ],
            parameters={
                "EXTERNAL": "TRUE",
                "classification": "parquet",
                "parquet.compression": "SNAPPY",
            },
            storage_descriptor=aws.glue.CatalogTableStorageDescriptorArgs(
                location=self.curated_bucket.bucket.apply(
                    lambda b: f"s3://{b}/web_events/"
                ),
                input_format=(
                    "org.apache.hadoop.hive.ql.io.parquet."
                    "MapredParquetInputFormat"
                ),
                output_format=(
                    "org.apache.hadoop.hive.ql.io.parquet."
                    "MapredParquetOutputFormat"
                ),
                columns=[
                    aws.glue.CatalogTableStorageDescriptorColumnArgs(
                        name=col, type=typ
                    )
                    for col, typ in curated_columns
                ],
                ser_de_info=_SerDeInfo(
                    serialization_library=(
                        "org.apache.hadoop.hive.ql.io.parquet.serde."
                        "ParquetHiveSerDe"
                    ),
                    parameters={"serialization.format": "1"},
                ),
            ),
            opts=child,
        )

        # --- Transform Lambda (incremental, idempotent per-day rebuild) ---
        transform_role = aws.iam.Role(
            f"{name}-transform-role",
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
            opts=child,
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-transform-basic",
            role=transform_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=transform_role),
        )
        aws.iam.RolePolicy(
            f"{name}-transform-policy",
            role=transform_role.id,
            policy=Output.all(
                bucket_name,
                self.results_bucket.arn,
                self.curated_bucket.arn,
            ).apply(lambda a: _transform_policy_json(a[0], a[1], a[2])),
            opts=ResourceOptions(parent=transform_role),
        )
        self.transform_lambda = aws.lambda_.Function(
            f"{name}-transform",
            runtime="python3.12",
            handler="handler.handler",
            code=pulumi.FileArchive(_HANDLER_DIR),
            role=transform_role.arn,
            timeout=600,
            memory_size=256,
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "ANALYTICS_DB": database_name,
                    "ANALYTICS_WORKGROUP": database_name,
                    "CURATED_BUCKET": self.curated_bucket.bucket,
                    "CURATED_PREFIX": "web_events/",
                },
            ),
            opts=child,
        )
        # Daily schedule (logs are low-latency; one daily pass + the
        # yesterday/today default keeps partitions fresh and idempotent).
        schedule = aws.cloudwatch.EventRule(
            f"{name}-transform-schedule",
            schedule_expression="cron(30 9 * * ? *)",  # 09:30 UTC ~ 02:30 PT
            opts=child,
        )
        aws.cloudwatch.EventTarget(
            f"{name}-transform-target",
            rule=schedule.name,
            arn=self.transform_lambda.arn,
            opts=ResourceOptions(parent=schedule),
        )
        aws.lambda_.Permission(
            f"{name}-transform-perm",
            action="lambda:InvokeFunction",
            function=self.transform_lambda.name,
            principal="events.amazonaws.com",
            source_arn=schedule.arn,
            opts=ResourceOptions(parent=self.transform_lambda),
        )

        # --- GA4 daily metrics (second source; tiny NDJSON, overwritten) ---
        ga_columns = [
            ("dt", "string"),
            ("sessions", "bigint"),
            ("total_users", "bigint"),
            ("new_users", "bigint"),
            ("pageviews", "bigint"),
            ("engaged_sessions", "bigint"),
        ]
        self.ga_daily_table = aws.glue.CatalogTable(
            f"{name}-ga-daily-table",
            name="ga_daily",
            database_name=self.database.name,
            table_type="EXTERNAL_TABLE",
            parameters={"EXTERNAL": "TRUE", "classification": "json"},
            storage_descriptor=aws.glue.CatalogTableStorageDescriptorArgs(
                location=self.curated_bucket.bucket.apply(
                    lambda b: f"s3://{b}/ga_daily/"
                ),
                input_format="org.apache.hadoop.mapred.TextInputFormat",
                output_format=(
                    "org.apache.hadoop.hive.ql.io."
                    "HiveIgnoreKeyTextOutputFormat"
                ),
                columns=[
                    aws.glue.CatalogTableStorageDescriptorColumnArgs(
                        name=col, type=typ
                    )
                    for col, typ in ga_columns
                ],
                ser_de_info=_SerDeInfo(
                    serialization_library=(
                        "org.openx.data.jsonserde.JsonSerDe"
                    ),
                ),
            ),
            opts=child,
        )

        # GA4 extractor — container Lambda (deps too heavy for a zip). Only
        # built when a service-account key + property id are configured.
        if ga_service_account_key is not None and ga_property_id:
            # Lazy import: only needed when GA is configured, and keeps the
            # heavy build helper out of the import path otherwise.
            # pylint: disable=import-outside-toplevel
            from codebuild_docker_image import CodeBuildDockerImage

            ga_role = aws.iam.Role(
                f"{name}-ga-role",
                assume_role_policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
                opts=child,
            )
            aws.iam.RolePolicyAttachment(
                f"{name}-ga-basic",
                role=ga_role.name,
                policy_arn=(
                    "arn:aws:iam::aws:policy/service-role/"
                    "AWSLambdaBasicExecutionRole"
                ),
                opts=ResourceOptions(parent=ga_role),
            )
            aws.iam.RolePolicy(
                f"{name}-ga-policy",
                role=ga_role.id,
                policy=self.curated_bucket.arn.apply(_ga_policy_json),
                opts=ResourceOptions(parent=ga_role),
            )
            ga_image = CodeBuildDockerImage(
                f"{name}-ga-img",
                dockerfile_path=(
                    "infra/components/web_analytics/"
                    "ga_extract_lambda/Dockerfile"
                ),
                build_context_path=".",
                source_paths=[
                    "infra/components/web_analytics/ga_extract_lambda"
                ],
                lambda_function_name=f"{name}-ga-extract",
                lambda_config={
                    "role_arn": ga_role.arn,
                    "timeout": 300,
                    "memory_size": 512,
                    "environment": {
                        "GA_PROPERTY_ID": ga_property_id,
                        # Pulumi-encrypted config secret, injected at deploy.
                        "GA_SERVICE_ACCOUNT_KEY": ga_service_account_key,
                        "CURATED_BUCKET": self.curated_bucket.bucket,
                        "GA_PREFIX": "ga_daily/",
                    },
                },
                platform="linux/arm64",
                opts=ResourceOptions(parent=self, depends_on=[ga_role]),
            )
            self.ga_lambda = ga_image.lambda_function
            ga_sched = aws.cloudwatch.EventRule(
                f"{name}-ga-schedule",
                schedule_expression="cron(45 9 * * ? *)",
                opts=child,
            )
            aws.cloudwatch.EventTarget(
                f"{name}-ga-target",
                rule=ga_sched.name,
                arn=self.ga_lambda.arn,
                opts=ResourceOptions(parent=ga_sched),
            )
            aws.lambda_.Permission(
                f"{name}-ga-perm",
                action="lambda:InvokeFunction",
                function=self.ga_lambda.name,
                principal="events.amazonaws.com",
                source_arn=ga_sched.arn,
                opts=ResourceOptions(parent=self.ga_lambda),
            )

        # --- Read-only IAM policy (attach to MCP Lambda role) ---
        account_id = aws.get_caller_identity().account_id
        region = aws.get_region().name
        glue_resources = [
            f"arn:aws:glue:{region}:{account_id}:catalog",
            f"arn:aws:glue:{region}:{account_id}:database/{database_name}",
            f"arn:aws:glue:{region}:{account_id}:table/{database_name}/*",
        ]
        self.read_policy = aws.iam.Policy(
            f"{name}-read-policy",
            policy=Output.all(
                bucket_name,
                self.results_bucket.arn,
                self.curated_bucket.arn,
            ).apply(
                lambda args: _policy_json(
                    args[0], args[1], args[2], glue_resources
                )
            ),
            opts=child,
        )
        self.read_policy_arn = self.read_policy.arn
        self.database_name = self.database.name
        self.workgroup_name = self.workgroup.name

        self.register_outputs(
            {
                "database_name": self.database_name,
                "workgroup_name": self.workgroup_name,
                "read_policy_arn": self.read_policy_arn,
                "results_bucket": self.results_bucket.bucket,
            }
        )


def _policy_json(
    logs_bucket: str,
    results_bucket_arn: str,
    curated_bucket_arn: str,
    glue_resources: list,
) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Athena",
                    "Effect": "Allow",
                    "Action": [
                        "athena:StartQueryExecution",
                        "athena:StopQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:GetWorkGroup",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "GlueCatalog",
                    "Effect": "Allow",
                    "Action": [
                        "glue:GetDatabase",
                        "glue:GetDatabases",
                        "glue:GetTable",
                        "glue:GetTables",
                        "glue:GetPartition",
                        "glue:GetPartitions",
                    ],
                    "Resource": glue_resources,
                },
                {
                    "Sid": "ReadLogsAndCurated",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{logs_bucket}",
                        f"arn:aws:s3:::{logs_bucket}/*",
                        curated_bucket_arn,
                        f"{curated_bucket_arn}/*",
                    ],
                },
                {
                    "Sid": "ReadWriteResults",
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:ListBucket",
                        "s3:GetBucketLocation",
                    ],
                    "Resource": [
                        results_bucket_arn,
                        f"{results_bucket_arn}/*",
                    ],
                },
            ],
        }
    )


def _transform_policy_json(
    logs_bucket: str, results_bucket_arn: str, curated_bucket_arn: str
) -> str:
    """Permissions for the transform Lambda: read raw logs, manage the curated
    table + its partitions, write curated Parquet, run Athena."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Athena",
                    "Effect": "Allow",
                    "Action": [
                        "athena:StartQueryExecution",
                        "athena:StopQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:GetWorkGroup",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "GlueReadWritePartitions",
                    "Effect": "Allow",
                    "Action": [
                        "glue:GetDatabase",
                        "glue:GetDatabases",
                        "glue:GetTable",
                        "glue:GetTables",
                        "glue:GetPartition",
                        "glue:GetPartitions",
                        "glue:CreatePartition",
                        "glue:BatchCreatePartition",
                        "glue:UpdatePartition",
                        "glue:DeletePartition",
                        "glue:BatchDeletePartition",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "ReadLogs",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{logs_bucket}",
                        f"arn:aws:s3:::{logs_bucket}/*",
                    ],
                },
                {
                    "Sid": "WriteCuratedAndResults",
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                        "s3:GetBucketLocation",
                    ],
                    "Resource": [
                        curated_bucket_arn,
                        f"{curated_bucket_arn}/*",
                        results_bucket_arn,
                        f"{results_bucket_arn}/*",
                    ],
                },
            ],
        }
    )


def _ga_policy_json(curated_bucket_arn: str) -> str:
    """Permissions for the GA4 extractor Lambda: write the ga_daily NDJSON."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "WriteGaDaily",
                    "Effect": "Allow",
                    "Action": [
                        "s3:PutObject",
                        "s3:GetObject",
                        "s3:ListBucket",
                    ],
                    "Resource": [
                        curated_bucket_arn,
                        f"{curated_bucket_arn}/*",
                    ],
                }
            ],
        }
    )

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

from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Input, Output, ResourceOptions

# CloudFront standard access-log columns, in order.
_CLOUDFRONT_COLUMNS = [
    ("date", "date"),
    ("time", "string"),
    ("location", "string"),
    ("bytes", "bigint"),
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
    ("request_bytes", "bigint"),
    ("time_taken", "float"),
    ("xforwarded_for", "string"),
    ("ssl_protocol", "string"),
    ("ssl_cipher", "string"),
    ("response_result_type", "string"),
    ("http_version", "string"),
    ("fle_status", "string"),
    ("fle_encrypted_fields", "int"),
    ("c_port", "int"),
    ("time_to_first_byte", "float"),
    ("x_edge_detailed_result_type", "string"),
    ("sc_content_type", "string"),
    ("sc_content_len", "bigint"),
    ("sc_range_start", "bigint"),
    ("sc_range_end", "bigint"),
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
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
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
                ser_de_info=aws.glue.CatalogTableStorageDescriptorSerDeInfoArgs(
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
                result_configuration=aws.athena.WorkgroupConfigurationResultConfigurationArgs(
                    output_location=self.results_bucket.bucket.apply(
                        lambda b: f"s3://{b}/results/"
                    ),
                ),
            ),
            force_destroy=True,
            opts=child,
        )

        # --- Read-only IAM policy (attach to MCP Lambda role) ---
        self.read_policy = aws.iam.Policy(
            f"{name}-read-policy",
            policy=Output.all(
                bucket_name,
                self.results_bucket.arn,
            ).apply(
                lambda args: _policy_json(args[0], args[1])
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


def _policy_json(logs_bucket: str, results_bucket_arn: str) -> str:
    import json

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

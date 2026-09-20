"""Retained LangSmith export buckets, now also used for native receipt traces.

Resource type and bucket names intentionally stay stable to preserve history.
No LangSmith service resources, access keys, or paid API calls are provisioned.
"""

import pulumi
import pulumi_aws as aws
from pulumi import (
    ComponentResource,
    ResourceOptions,
)

# Get stack configuration
stack = pulumi.get_stack()


class TraceArchive(ComponentResource):
    """Retain the private trace bucket without paid export machinery."""

    def __init__(
        self,
        name: str,
        *,
        opts: ResourceOptions | None = None,
    ) -> None:
        super().__init__(
            f"custom:langsmith-bulk-export:{name}",
            name,
            None,
            opts,
        )

        self.export_bucket = aws.s3.Bucket(
            f"{name}-export-bucket",
            force_destroy=False,
            tags={
                "Name": f"{name}-export-bucket",
                "Purpose": "LangSmithBulkExport",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self, retain_on_delete=True),
        )

        aws.s3.BucketOwnershipControls(
            f"{name}-export-bucket-ownership",
            bucket=self.export_bucket.id,
            rule=aws.s3.BucketOwnershipControlsRuleArgs(
                object_ownership="BucketOwnerEnforced"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Keep archived and native trace objects private.
        aws.s3.BucketPublicAccessBlock(
            f"{name}-export-bucket-public-access",
            bucket=self.export_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self),
        )

        # Keep the bucket and its original URN as a history archive and native
        # trace store, without export Lambdas or cross-account credentials.
        self.register_outputs({"export_bucket": self.export_bucket.id})


class AnalyticsArchives(ComponentResource):
    """Preserve archive bucket identities after the runtime retirement."""

    def __init__(self, name: str) -> None:
        # Keep the old component type so the archive buckets retain their URNs.
        super().__init__(f"custom:emr-serverless-analytics:{name}", name, None)
        # Artifacts bucket for Spark job code and venv
        self.artifacts_bucket = aws.s3.Bucket(
            f"{name}-artifacts",
            force_destroy=False,
            tags={
                "Name": f"{name}-artifacts",
                "Purpose": "EMR Spark job artifacts",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self, retain_on_delete=True),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-artifacts-pab",
            bucket=self.artifacts_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.artifacts_bucket),
        )

        # Analytics output bucket
        self.analytics_bucket = aws.s3.Bucket(
            f"{name}-analytics-output",
            force_destroy=False,
            tags={
                "Name": f"{name}-analytics-output",
                "Purpose": "EMR Spark analytics results",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self, retain_on_delete=True),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-analytics-pab",
            bucket=self.analytics_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.analytics_bucket),
        )

        self.register_outputs(
            {
                "artifacts_bucket_name": self.artifacts_bucket.id,
                "analytics_bucket_name": self.analytics_bucket.id,
            }
        )

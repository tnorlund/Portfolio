"""Retain historical S3 analytics archives without deploying a Spark runtime."""

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions

stack = pulumi.get_stack()


class AnalyticsArchives(ComponentResource):
    """Preserve existing bucket identities while retiring EMR and its builder."""

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


def retain_analytics_archives() -> AnalyticsArchives:
    """Keep stored data without provisioning compute or expiring history."""
    return AnalyticsArchives(f"emr-analytics-{stack}")

"""Retained LangSmith export buckets, now also used for native receipt traces.

Resource type and bucket names intentionally stay stable to preserve history.
No LangSmith service resources, access keys, or paid API calls are provisioned.
"""

from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    ComponentResource,
    ResourceOptions,
)

# Get stack configuration
stack = pulumi.get_stack()


class LangSmithBulkExport(ComponentResource):
    """Retain the existing private trace bucket without paid export machinery."""

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

        # Keep the bucket and its original URN as a history archive and native
        # trace store. Paid export Lambdas and cross-account credentials retire.
        self.register_outputs({"export_bucket": self.export_bucket.id})

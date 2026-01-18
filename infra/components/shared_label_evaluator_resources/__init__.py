"""
Shared Label Evaluator Resources.

This component creates shared S3 buckets used by multiple label evaluator
components (EMR analytics, Step Function, viz-cache API). By centralizing
bucket creation here, we avoid circular dependencies between components.

Creates:
- batch_bucket: Stores receipt data, patterns, unified results, receipts_lookup
- viz_cache_bucket: Stores visualization cache files for the UI

These buckets are passed to:
- EMRServerlessAnalytics (needs ARNs for IAM policy)
- LabelEvaluatorStepFunction (needs names for Lambda environment)
- LabelEvaluatorVizCache (needs name for API Lambda)
"""

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


class SharedLabelEvaluatorResources(ComponentResource):
    """
    Shared resources for the label evaluator pipeline.

    This component owns all S3 buckets that need to be accessed by multiple
    components in the label evaluator pipeline. Creating them here breaks
    circular dependencies between EMR and Step Function components.
    """

    def __init__(
        self,
        name: str,
        *,
        opts: ResourceOptions | None = None,
    ):
        super().__init__(
            f"custom:shared-label-evaluator-resources:{name}",
            name,
            None,
            opts,
        )

        stack = pulumi.get_stack()
        is_prod = stack in ("prod", "production")

        # ============================================================
        # Batch Bucket - stores receipt data, patterns, results
        # ============================================================
        # Used by:
        # - Step Function Lambdas (read/write data/, patterns/, unified/)
        # - EMR Spark job (read receipts_lookup/, data/, unified/)
        self.batch_bucket = aws.s3.Bucket(
            f"{name}-batch-bucket",
            force_destroy=not is_prod,
            tags={
                "Name": f"{name}-batch-bucket",
                "Purpose": "Label evaluator batch files and results",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketVersioningV2(
            f"{name}-batch-bucket-versioning",
            bucket=self.batch_bucket.id,
            versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=ResourceOptions(parent=self.batch_bucket),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-batch-bucket-pab",
            bucket=self.batch_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.batch_bucket),
        )

        # ============================================================
        # Viz-Cache Bucket - stores visualization cache for UI
        # ============================================================
        # Used by:
        # - EMR Spark job (write receipts/, metadata.json, latest.json)
        # - Viz-cache API Lambda (read cached receipts)
        self.viz_cache_bucket = aws.s3.Bucket(
            f"{name}-viz-cache",
            force_destroy=True,  # Cache can always be regenerated
            tags={
                "Name": f"{name}-viz-cache",
                "Purpose": "Label evaluator visualization cache",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-viz-cache-pab",
            bucket=self.viz_cache_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.viz_cache_bucket),
        )

        # ============================================================
        # Outputs
        # ============================================================
        self.batch_bucket_name: Output[str] = self.batch_bucket.id
        self.batch_bucket_arn: Output[str] = self.batch_bucket.arn
        self.viz_cache_bucket_name: Output[str] = self.viz_cache_bucket.id
        self.viz_cache_bucket_arn: Output[str] = self.viz_cache_bucket.arn

        self.register_outputs(
            {
                "batch_bucket_name": self.batch_bucket_name,
                "batch_bucket_arn": self.batch_bucket_arn,
                "viz_cache_bucket_name": self.viz_cache_bucket_name,
                "viz_cache_bucket_arn": self.viz_cache_bucket_arn,
            }
        )


def create_shared_label_evaluator_resources(
    opts: ResourceOptions | None = None,
) -> SharedLabelEvaluatorResources:
    """Factory function to create shared label evaluator resources.

    Args:
        opts: Pulumi resource options

    Returns:
        SharedLabelEvaluatorResources component with batch and viz-cache buckets
    """
    stack = pulumi.get_stack()
    return SharedLabelEvaluatorResources(
        f"label-evaluator-{stack}",
        opts=opts,
    )

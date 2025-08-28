"""
S3 Buckets for ChromaDB Compaction Pipeline

This module defines the S3 bucket infrastructure for storing ChromaDB snapshots
and delta files using Pulumi ComponentResource pattern.
"""

# pylint: disable=too-many-instance-attributes  # Pulumi components need many attributes

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output


class ChromaDBBuckets(ComponentResource):
    """
    ComponentResource that creates S3 buckets for ChromaDB storage.

    Creates:
    - Main bucket for vectors with lifecycle policies
    - Proper folder structure for snapshots and deltas
    - Encryption and versioning enabled
    """

    def __init__(
        self,
        name: str,
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize ChromaDB S3 buckets.

        Args:
            name: The unique name of the resource
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__("chromadb:storage:S3Buckets", name, None, opts)

        # Get stack and account info
        if stack is None:
            stack = pulumi.get_stack()

        # Create main bucket - let Pulumi auto-generate unique name
        self.bucket = aws.s3.Bucket(
            f"{name}-vectors",
            # No inline configuration - all done via separate resources
            force_destroy=True,  # Only for dev environments
            tags={
                "Project": "ChromaDB",
                "Component": "Storage",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Configure versioning as a separate resource
        self.bucket_versioning = aws.s3.BucketVersioning(
            f"{name}-vectors-versioning",
            bucket=self.bucket.id,
            # pylint: disable=line-too-long
            versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
                status="Enabled",
            ),
            opts=ResourceOptions(parent=self),
        )

        # Configure server-side encryption as a separate resource
        # pylint: disable=line-too-long
        self.bucket_encryption = aws.s3.BucketServerSideEncryptionConfiguration(
            f"{name}-vectors-encryption",
            bucket=self.bucket.id,
            rules=[
                aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                    apply_server_side_encryption_by_default=(
                        # pylint: disable=line-too-long
                        aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                            sse_algorithm="AES256",
                        )
                    ),
                    bucket_key_enabled=True,
                ),
            ],
            opts=ResourceOptions(parent=self),
        )

        # Configure lifecycle rules as a separate resource
        self.bucket_lifecycle = aws.s3.BucketLifecycleConfiguration(
            f"{name}-vectors-lifecycle",
            bucket=self.bucket.id,
            rules=[
                # Clean up old deltas after 7 days
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="delete-old-deltas",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix="delta/",
                    ),
                    # pylint: disable=line-too-long
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=7,
                    ),
                ),
                # Delete old timestamped snapshots after 14 days
                # Only targets snapshot/timestamped/* prefix,
                # preserving snapshot/latest/
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="delete-old-timestamped-snapshots",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix="snapshot/timestamped/",
                    ),
                    # pylint: disable=line-too-long
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=14,
                    ),
                ),
                # Clean up intermediate chunks after 1 day
                # These are temporary files created during chunked compaction
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="delete-intermediate-chunks",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix="intermediate/",
                    ),
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=1,
                    ),
                ),
                # Clean up temp directories after 1 day
                # These are temporary files created during atomic uploads
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="delete-temp-uploads",
                    status="Enabled",
                    filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                        prefix="temp/",
                    ),
                    expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                        days=1,
                    ),
                ),
            ],
            opts=ResourceOptions(parent=self),
        )

        # Block public access
        self.bucket_public_access_block = aws.s3.BucketPublicAccessBlock(
            f"{name}-vectors-pab",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self),
        )

        # Create initial folder structure with placeholder objects
        # This helps visualize the structure in S3 console
        self.snapshot_folder = aws.s3.BucketObject(
            f"{name}-snapshot-folder",
            bucket=self.bucket.id,
            key="snapshot/.placeholder",
            content="This folder contains ChromaDB snapshots",
            content_type="text/plain",
            opts=ResourceOptions(parent=self),
        )

        self.delta_folder = aws.s3.BucketObject(
            f"{name}-delta-folder",
            bucket=self.bucket.id,
            key="delta/.placeholder",
            content="This folder contains ChromaDB delta files",
            content_type="text/plain",
            opts=ResourceOptions(parent=self),
        )

        self.latest_pointer = aws.s3.BucketObject(
            f"{name}-latest-pointer",
            bucket=self.bucket.id,
            key="snapshot/latest/pointer.txt",
            content="snapshot/initial/",  # Initial pointer
            content_type="text/plain",
            opts=ResourceOptions(parent=self),
        )

        # Bucket policy for Lambda access (to be updated when Lambdas are created)
        self.bucket_policy_document = Output.all(self.bucket.arn).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "DenyInsecureTransport",
                            "Effect": "Deny",
                            "Principal": "*",
                            "Action": "s3:*",
                            "Resource": [
                                args[0],
                                f"{args[0]}/*",
                            ],
                            "Condition": {
                                "Bool": {
                                    "aws:SecureTransport": "false",
                                },
                            },
                        },
                    ],
                }
            )
        )

        self.bucket_policy = aws.s3.BucketPolicy(
            f"{name}-bucket-policy",
            bucket=self.bucket.id,
            policy=self.bucket_policy_document,
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.bucket_name = self.bucket.id
        self.bucket_arn = self.bucket.arn
        self.delta_prefix = "delta/"
        self.snapshot_prefix = "snapshot/"
        self.latest_pointer_key = "snapshot/latest/pointer.txt"

        # Register outputs
        self.register_outputs(
            {
                "bucket_name": self.bucket_name,
                "bucket_arn": self.bucket_arn,
                "delta_prefix": self.delta_prefix,
                "snapshot_prefix": self.snapshot_prefix,
                "latest_pointer_key": self.latest_pointer_key,
            }
        )


def create_chromadb_buckets(
    name: str = "chromadb",
    opts: Optional[ResourceOptions] = None,
) -> ChromaDBBuckets:
    """
    Factory function to create ChromaDB S3 buckets.

    Args:
        name: Base name for the resources
        opts: Optional resource options

    Returns:
        ChromaDBBuckets component resource
    """
    return ChromaDBBuckets(name, opts=opts)

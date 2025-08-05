#!/usr/bin/env python3
"""
Local test script for ChromaDB S3 buckets.

This script tests the S3 bucket creation locally using Pulumi.
Run this before integrating into the main infrastructure.

Usage:
    python test_s3_local.py
"""

import pulumi
from .s3_buckets import ChromaDBBuckets


# Create a test Pulumi program
def create_test_infrastructure():
    """Create test S3 buckets for ChromaDB."""

    # Create the ChromaDB buckets
    chromadb_storage = ChromaDBBuckets(
        "chromadb-test",
        stack="local-test",
        account_id="123456789012",  # Use a dummy account ID for testing
    )

    # Export the outputs for verification
    pulumi.export("bucket_name", chromadb_storage.bucket_name)
    pulumi.export("bucket_arn", chromadb_storage.bucket_arn)
    pulumi.export("delta_prefix", chromadb_storage.delta_prefix)
    pulumi.export("snapshot_prefix", chromadb_storage.snapshot_prefix)
    pulumi.export("latest_pointer_key", chromadb_storage.latest_pointer_key)

    # Additional exports for testing
    pulumi.export("lifecycle_rules", chromadb_storage.bucket.lifecycle_rules)
    pulumi.export(
        "versioning_enabled", chromadb_storage.bucket.versioning.enabled
    )
    pulumi.export(
        "encryption_enabled",
        chromadb_storage.bucket.server_side_encryption_configuration,
    )

    return chromadb_storage


if __name__ == "__main__":
    print("Creating test ChromaDB S3 infrastructure...")
    print("\nThis will create the following resources:")
    print("- S3 bucket with versioning and encryption")
    print("- Lifecycle rules for delta cleanup and snapshot archival")
    print("- Initial folder structure")
    print("- Bucket policy for secure access")
    print("\nRun 'pulumi preview' to see what will be created")
    print("Run 'pulumi up' to create the resources")
    print("Run 'pulumi destroy' to clean up when done")

    # Note: To actually run this, you would need to:
    # 1. Initialize Pulumi: pulumi new aws-python --dir test-chromadb-s3
    # 2. Copy this file and s3_buckets.py to the directory
    # 3. Run: pulumi up

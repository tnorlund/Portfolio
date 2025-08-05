"""
Example usage of ChromaDB S3 buckets in main infrastructure.

This shows how to integrate the ChromaDB buckets into your main __main__.py file.
"""

import pulumi
from infra.chromadb_compaction import create_chromadb_buckets


# Example 1: Simple usage in __main__.py
def add_chromadb_to_main():
    """
    Add this to your infra/__main__.py file:
    """
    # Create ChromaDB storage infrastructure
    chromadb_storage = create_chromadb_buckets("chromadb")

    # Export the bucket details
    pulumi.export("chromadb_bucket_name", chromadb_storage.bucket_name)
    pulumi.export("chromadb_bucket_arn", chromadb_storage.bucket_arn)

    # You can pass these to your Lambda functions
    # For example, in your Lambda environment variables:
    lambda_env = {
        "CHROMADB_BUCKET": chromadb_storage.bucket_name,
        "DELTA_PREFIX": chromadb_storage.delta_prefix,
        "SNAPSHOT_PREFIX": chromadb_storage.snapshot_prefix,
        "LATEST_POINTER_KEY": chromadb_storage.latest_pointer_key,
    }

    return chromadb_storage


# Example 2: Integration with existing infrastructure
def integrate_with_existing_infra():
    """
    Example of integrating with your existing infrastructure:
    """
    from infra.chromadb_compaction import ChromaDBBuckets

    # Create with custom configuration
    chromadb_storage = ChromaDBBuckets(
        "chromadb",
        stack=pulumi.get_stack(),
        account_id=None,  # Will auto-detect
    )

    # Update your existing Lambda functions to use the bucket
    # For example, updating the polling handler:
    polling_lambda_env = {
        "DELTA_BUCKET": chromadb_storage.bucket_name,
        # ... other env vars
    }

    return chromadb_storage


# Example 3: Testing locally
def test_bucket_creation():
    """
    Test the bucket creation locally:
    """
    # 1. Create a test directory
    # mkdir test-chromadb && cd test-chromadb

    # 2. Initialize Pulumi
    # pulumi new aws-python

    # 3. Replace __main__.py with:
    """
    import pulumi
    from chromadb_compaction.s3_buckets import ChromaDBBuckets
    
    # Create test buckets
    storage = ChromaDBBuckets("test", stack="dev", account_id="123456789012")
    
    # Export outputs
    pulumi.export("bucket_name", storage.bucket_name)
    pulumi.export("lifecycle_rules", storage.bucket.lifecycle_rules)
    """

    # 4. Run pulumi preview to see what will be created
    # pulumi preview

    # 5. Run pulumi up to create resources
    # pulumi up

    # 6. Clean up when done
    # pulumi destroy


# Example 4: Full integration snippet for __main__.py
MAIN_PY_SNIPPET = """
# Add to your infra/__main__.py:

from infra.chromadb_compaction import create_chromadb_buckets

# ... existing imports and code ...

# Create ChromaDB infrastructure
print("Creating ChromaDB S3 storage...")
chromadb_storage = create_chromadb_buckets("chromadb")

# Export ChromaDB outputs
pulumi.export("chromadb_bucket_name", chromadb_storage.bucket_name)
pulumi.export("chromadb_bucket_arn", chromadb_storage.bucket_arn)
pulumi.export("chromadb_delta_prefix", chromadb_storage.delta_prefix)
pulumi.export("chromadb_snapshot_prefix", chromadb_storage.snapshot_prefix)

# Update your Lambda environment variables
# For the polling handler that creates deltas:
polling_lambda.environment.variables.update({
    "DELTA_BUCKET": chromadb_storage.bucket_name,
    "DELTA_PREFIX": chromadb_storage.delta_prefix,
})

# For future compactor Lambda:
# compactor_lambda.environment.variables.update({
#     "CHROMADB_BUCKET": chromadb_storage.bucket_name,
#     "SNAPSHOT_PREFIX": chromadb_storage.snapshot_prefix,
#     "LATEST_POINTER_KEY": chromadb_storage.latest_pointer_key,
# })
"""

if __name__ == "__main__":
    print("ChromaDB S3 Bucket Integration Examples")
    print("=" * 50)
    print("\nTo integrate into your main infrastructure:")
    print(MAIN_PY_SNIPPET)
    print("\nTo test locally:")
    print("1. Copy s3_buckets.py to a test directory")
    print("2. Run: pulumi new aws-python")
    print("3. Replace __main__.py with the test code")
    print("4. Run: pulumi up")

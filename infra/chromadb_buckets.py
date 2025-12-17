"""Shared ChromaDB buckets for routes and infrastructure."""

# This module exports the ChromaDB bucket created in __main__.py
# Routes can import bucket_name from here to avoid circular dependencies
# The bucket itself is created in __main__.py and imported here

# Note: This creates a deferred import pattern
# Routes import bucket_name from here, which will be set by __main__.py
# This avoids circular imports while allowing routes to be self-contained

import pulumi
from chromadb_compaction import ChromaDBBuckets

# Create the bucket here (same as __main__.py was doing)
# This ensures routes can import it before __main__.py runs
# Since both use the same name pattern, Pulumi will see them as the same resource
# after we import the existing state from __main__.py
shared_chromadb_buckets = ChromaDBBuckets(
    f"chromadb-{pulumi.get_stack()}-shared-buckets",
)

# Export bucket name for easy access
bucket_name = shared_chromadb_buckets.bucket_name
bucket_arn = shared_chromadb_buckets.bucket_arn

pulumi.export("chromadb_bucket_name", bucket_name)
pulumi.export("chromadb_bucket_arn", bucket_arn)

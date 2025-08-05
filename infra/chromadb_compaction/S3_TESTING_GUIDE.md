# S3 Bucket Testing Guide

This guide helps you test the ChromaDB S3 bucket infrastructure locally before integrating into your main infrastructure.

## Quick Start

### 1. Test Locally with Pulumi

```bash
# Create a test directory
mkdir ~/chromadb-s3-test && cd ~/chromadb-s3-test

# Initialize a new Pulumi project
pulumi new aws-python

# Copy the S3 bucket module
cp /path/to/example/infra/chromadb_compaction/s3_buckets.py .
```

### 2. Create Test Program

Replace the generated `__main__.py` with:

```python
"""Test ChromaDB S3 bucket creation."""
import pulumi
from s3_buckets import ChromaDBBuckets

# Create the buckets
storage = ChromaDBBuckets(
    "chromadb",
    stack=pulumi.get_stack(),
)

# Export outputs to verify
pulumi.export("bucket_name", storage.bucket_name)
pulumi.export("bucket_arn", storage.bucket_arn)
pulumi.export("delta_prefix", storage.delta_prefix)
pulumi.export("snapshot_prefix", storage.snapshot_prefix)
pulumi.export("latest_pointer_key", storage.latest_pointer_key)

# Verify lifecycle rules
pulumi.export("lifecycle_rules", storage.bucket.lifecycle_rules)
```

### 3. Preview and Deploy

```bash
# Configure AWS credentials
export AWS_PROFILE=your-profile  # or use AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY

# Preview what will be created
pulumi preview

# Create the resources
pulumi up

# Check the outputs
pulumi stack output
```

### 4. Verify in AWS Console

After deployment, verify in the AWS S3 console:

1. Navigate to S3 in AWS Console
2. Find bucket: `chromadb-vectors-{stack}-{account_id}`
3. Check:
   - ✅ Versioning is enabled
   - ✅ Encryption is enabled (AES256)
   - ✅ Public access is blocked
   - ✅ Lifecycle rules are configured
   - ✅ Initial folder structure exists

### 5. Test S3 Operations

```bash
# Get bucket name
BUCKET=$(pulumi stack output bucket_name)

# List contents
aws s3 ls s3://$BUCKET/

# You should see:
# PRE delta/
# PRE snapshot/

# Check the latest pointer
aws s3 cp s3://$BUCKET/snapshot/latest/pointer.txt -
# Output: snapshot/initial/

# Test writing a delta (simulating Lambda)
echo '{"test": "data"}' | aws s3 cp - s3://$BUCKET/delta/test-delta.json

# Verify lifecycle rules
aws s3api get-bucket-lifecycle-configuration --bucket $BUCKET
```

### 6. Clean Up

```bash
# Destroy the test resources
pulumi destroy

# Remove the test directory
cd .. && rm -rf chromadb-s3-test
```

## Integration into Main Infrastructure

Once tested, integrate into your main `infra/__main__.py`:

```python
# Import at the top
from infra.chromadb_compaction import create_chromadb_buckets

# In your main infrastructure code
chromadb_storage = create_chromadb_buckets("chromadb")

# Use in Lambda environment variables
your_lambda.environment.variables.update({
    "DELTA_BUCKET": chromadb_storage.bucket_name,
    "DELTA_PREFIX": chromadb_storage.delta_prefix,
})

# Export for reference
pulumi.export("chromadb_bucket", chromadb_storage.bucket_name)
```

## Troubleshooting

### Issue: Bucket name already exists
```
Error: creating S3 Bucket: BucketAlreadyExists
```
**Solution**: The bucket name must be globally unique. The component automatically appends stack and account ID, but if testing multiple times, add a unique suffix.

### Issue: Access denied creating bucket
```
Error: creating S3 Bucket: AccessDenied
```
**Solution**: Ensure your AWS credentials have S3 permissions:
- `s3:CreateBucket`
- `s3:PutBucketPolicy`
- `s3:PutBucketVersioning`
- `s3:PutBucketLifecycleConfiguration`
- `s3:PutBucketPublicAccessBlock`

### Issue: Pulumi state conflicts
**Solution**: Use different stack names for testing:
```bash
pulumi stack init test-chromadb-s3
pulumi stack select test-chromadb-s3
```

## What the S3 Bucket Provides

The `ChromaDBBuckets` component creates:

1. **Main S3 Bucket**
   - Name: `chromadb-vectors-{stack}-{account_id}`
   - Versioning enabled for snapshot safety
   - AES256 encryption at rest
   - Public access blocked

2. **Lifecycle Policies**
   - Deltas deleted after 7 days
   - Old snapshots moved to IA storage after 30 days
   - Very old snapshots moved to Glacier after 90 days
   - Old versions kept for 90 days

3. **Initial Structure**
   ```
   /
   ├── delta/
   │   └── .placeholder
   ├── snapshot/
   │   ├── .placeholder
   │   └── latest/
   │       └── pointer.txt  (contains "snapshot/initial/")
   ```

4. **Security**
   - Denies all non-HTTPS connections
   - Ready for Lambda role policies
   - No public access allowed

## Next Steps

After successful S3 bucket testing:

1. Add SQS queue for delta notifications
2. Create IAM roles for Lambda functions
3. Build producer Lambda for creating deltas
4. Build compactor Lambda for merging deltas
5. Add monitoring and alarms

The S3 bucket is the foundation - all other components depend on it.
import pulumi
import pulumi_aws as aws

raw_bucket = aws.s3.Bucket(
    "raw-image-bucket",
    opts=pulumi.ResourceOptions(ignore_changes=["acl", "grants"]),
)

# Enforce owner-only bucket (no ACLs)
raw_bucket_ownership = aws.s3.BucketOwnershipControls(
    "raw-image-bucket-ownership",
    bucket=raw_bucket.id,
    rule={"objectOwnership": "BucketOwnerEnforced"},
)
pulumi.export("raw_bucket_name", raw_bucket.bucket)

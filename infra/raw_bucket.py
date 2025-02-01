import pulumi
import pulumi_aws as aws

raw_bucket = aws.s3.Bucket("raw-image-bucket")
pulumi.export("raw_bucket_name", raw_bucket.bucket)
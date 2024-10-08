import pulumi
import pulumi_aws as aws
import json

# The DynamoDB table
dynamodb_table = aws.dynamodb.Table(
    "GHActionTable",
    attributes=[
        aws.dynamodb.TableAttributeArgs(
            name="PK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="SK",
            type="S",
        ),
    ],
    hash_key="PK",
    range_key="SK",
    billing_mode="PAY_PER_REQUEST",
    ttl=aws.dynamodb.TableTtlArgs(
        attribute_name="TimeToLive",
        enabled=True,
    ),
    stream_enabled=True,
    stream_view_type="NEW_IMAGE",
    tags={
        "Environment": "dev",
        "Name": "GHActionTable",
    },
)

pulumi.export("table_name", dynamodb_table.name)
pulumi.export("region", aws.config.region)


# Create an S3 bucket with website configuration
site_bucket = aws.s3.Bucket("siteBucket",
    website={
        "indexDocument": "index.html",
        "errorDocument": "error.html",
    }
)

# Upload index.html and error.html to the bucket
index_html = aws.s3.BucketObject("index.html",
    bucket=site_bucket.id,
    source=pulumi.FileAsset("index.html"),  # Assumes you have an index.html file in your project directory
    content_type="text/html"
)

error_html = aws.s3.BucketObject("error.html",
    bucket=site_bucket.id,
    source=pulumi.FileAsset("error.html"),  # Assumes you have an error.html file in your project directory
    content_type="text/html"
)

# Define a bucket policy to make the bucket objects publicly readable
bucket_policy = aws.s3.BucketPolicy("bucketPolicy",
    bucket=site_bucket.bucket,
    policy=site_bucket.bucket.apply(
        lambda bucket_name: json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
            }],
        })
    )
)

# Configure the BucketPublicAccessBlock to allow public policies
public_access_block = aws.s3.BucketPublicAccessBlock("publicAccessBlock",
    bucket=site_bucket.id,
    block_public_acls=False,
    ignore_public_acls=False,
    block_public_policy=False,
    restrict_public_buckets=False
)

pulumi.export('websiteUrl', site_bucket.website_endpoint)

# open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())

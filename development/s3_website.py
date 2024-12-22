import pulumi
import pulumi_aws as aws
import json

# Create an S3 bucket with website configuration
site_bucket = aws.s3.Bucket("siteBucket",
    website={
        "indexDocument": "index.html",
    }
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

# Create a CloudFront distribution
cdn = aws.cloudfront.Distribution("cdn",
    origins=[{
        "domainName": site_bucket.bucket_regional_domain_name,
        "originId": site_bucket.id,
    }],
    enabled=True,
    default_root_object="index.html",
    default_cache_behavior={
        "allowedMethods": ["GET", "HEAD"],
        "cachedMethods": ["GET", "HEAD"],
        "targetOriginId": site_bucket.id,
        "viewerProtocolPolicy": "redirect-to-https",
        "forwardedValues": {
            "cookies": {"forward": "none"},
            "queryString": False,
        },
        "minTtl": 0,
        "defaultTtl": 3600,
        "maxTtl": 86400,
    },
    price_class="PriceClass_100",
    restrictions={
        "geoRestriction": {
            "restrictionType": "none",
        },
    },
    viewer_certificate={
        "cloudfrontDefaultCertificate": True,
    }
)

pulumi.export('bucketName', site_bucket.id)
pulumi.export('websiteUrl', site_bucket.website_endpoint)
pulumi.export('cdnUrl', cdn.domain_name)
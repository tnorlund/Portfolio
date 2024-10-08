import pulumi
import pulumi_aws as aws

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

# An S3 bucket that hosts the static website
website_bucket = aws.s3.Bucket(
    "s3-website-bucket",
    website=aws.s3.BucketWebsiteArgs(
        index_document="index.html", error_document="error.html"
    ),
)

# Upload the index and error documents to the bucket
index_html = aws.s3.BucketObject(
    "index.html",
    bucket=website_bucket.id,
    source=pulumi.FileAsset("index.html"),
    content_type="text/html",
)
error_html = aws.s3.BucketObject(
    "error.html",
    bucket=website_bucket.id,
    source=pulumi.FileAsset("error.html"),
    content_type="text/html",
)

# Create a CloudFront distribution for the bucket
cdn = aws.cloudfront.Distribution(
    "cdnDistribution",
    origins=[
        aws.cloudfront.DistributionOriginArgs(
            domain_name=website_bucket.bucket_website_domain_name,
            origin_id=website_bucket.arn,
        )
    ],
    enabled=True,
    default_root_object="index.html",
    default_cache_behavior=aws.cloudfront.DistributionDefaultCacheBehaviorArgs(
        target_origin_id=website_bucket.arn,
        viewer_protocol_policy="allow-all",
        allowed_methods=["GET", "HEAD"],
        cached_methods=["GET", "HEAD"],
        forwarded_values=aws.cloudfront.DistributionDefaultCacheBehaviorForwardedValuesArgs(
            query_string=False,
            cookies=aws.cloudfront.DistributionDefaultCacheBehaviorForwardedValuesCookiesArgs(
                forward="none"
            ),
        ),
    ),
    price_class="PriceClass_100",
    custom_error_responses=[
        aws.cloudfront.DistributionCustomErrorResponseArgs(
            error_code=404, response_code=404, response_page_path="/error.html"
        )
    ],
    restrictions=aws.cloudfront.DistributionRestrictionsArgs(
        geo_restriction=aws.cloudfront.DistributionRestrictionsGeoRestrictionArgs(
            restriction_type="none"
        )
    ),
    viewer_certificate=aws.cloudfront.DistributionViewerCertificateArgs(
        cloudfront_default_certificate=True
    ),
)

# Export the URLs of the bucket and the CloudFront distribution
pulumi.export(
    "bucket_url",
    pulumi.Output.concat("http://", website_bucket.bucket_website_domain_name),
)
pulumi.export("cdn_url", pulumi.Output.concat("https://", cdn.domain_name))

# open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())

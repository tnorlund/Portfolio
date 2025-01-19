import pulumi
import pulumi_aws as aws
import json

# Detect the current Pulumi stack
stack = pulumi.get_stack()

# Our base domain (Route 53 Hosted Zone is for this apex)
BASE_DOMAIN = "tylernorlund.com"

# Decide site domain based on the stack
if stack == "prod":
    site_domain = f"www.{BASE_DOMAIN}"  # e.g., "www.tylernorlund.com"
else:
    site_domain = f"{stack}.{BASE_DOMAIN}"  # e.g., "dev.tylernorlund.com"

# 1. Lookup the existing hosted zone
hosted_zone = aws.route53.get_zone(name=BASE_DOMAIN)

# 2. Request an ACM certificate (in us-east-1)
certificate = aws.acm.Certificate(
    "siteCertificate",
    domain_name=site_domain,
    validation_method="DNS",
)

# 3. Create a DNS record for validation
cert_validation_options = certificate.domain_validation_options[0]
validation_record = aws.route53.Record(
    "siteCertValidationRecord",
    zone_id=hosted_zone.zone_id,
    name=cert_validation_options.resource_record_name,
    type=cert_validation_options.resource_record_type,
    records=[cert_validation_options.resource_record_value],
    ttl=60,
)

# 4. Wait for the certificate to be validated
certificate_validation = aws.acm.CertificateValidation(
    "siteCertificateValidation",
    certificate_arn=certificate.arn,
    validation_record_fqdns=[validation_record.fqdn],
)

# 5. Create S3 bucket (for your static site)
site_bucket = aws.s3.Bucket(
    "siteBucket",
    website={"indexDocument": "index.html"},
)

# 6. Make the bucket publicly readable
bucket_policy = aws.s3.BucketPolicy(
    "bucketPolicy",
    bucket=site_bucket.bucket,
    policy=site_bucket.bucket.apply(
        lambda bucket_name: json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*"
            }]
        })
    ),
)

public_access_block = aws.s3.BucketPublicAccessBlock(
    "publicAccessBlock",
    bucket=site_bucket.id,
    block_public_acls=False,
    ignore_public_acls=False,
    block_public_policy=False,
    restrict_public_buckets=False,
)

# 7. Create a CloudFront distribution with the validated cert
cdn = aws.cloudfront.Distribution(
    "cdn",
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
        "geoRestriction": {"restrictionType": "none"}
    },
    viewer_certificate={
        "acmCertificateArn": certificate_validation.certificate_arn,
        "sslSupportMethod": "sni-only",
        "minimumProtocolVersion": "TLSv1.2_2019",
    },
    aliases=[site_domain],
    # Ensure the cert is fully validated before creating the distribution
    opts=pulumi.ResourceOptions(depends_on=[certificate_validation]),
)

# 8. Route 53 alias record -> CloudFront
alias_record = aws.route53.Record(
    "siteAliasRecord",
    zone_id=hosted_zone.zone_id,
    name=site_domain,
    type="A",
    aliases=[{
        "name": cdn.domain_name,
        "zone_id": cdn.hosted_zone_id,
        "evaluateTargetHealth": True,
    }],
)

# Exports (optional)
pulumi.export("bucketName", site_bucket.bucket)
pulumi.export("cloudFrontDomain", cdn.domain_name)
pulumi.export("websiteDomain", site_domain)
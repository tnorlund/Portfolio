import pulumi
import pulumi_aws as aws
import json

# Detect the current Pulumi stack
stack = pulumi.get_stack()

# Our base domain
BASE_DOMAIN = "tylernorlund.com"

########################
# 1) Decide site domains
########################
if stack == "prod":
    # For production, we want BOTH the apex and www
    # The first domain is the "primary" domain for the cert
    # The others go into subject_alternative_names
    primary_domain = BASE_DOMAIN
    alt_domains = [f"www.{BASE_DOMAIN}"]
    site_domains = [primary_domain] + alt_domains  # For convenience
else:
    # For non-prod, just use "<stack>.tylernorlund.com"
    primary_domain = f"{stack}.{BASE_DOMAIN}"
    alt_domains = []
    site_domains = [primary_domain]

########################
# 2) Request ACM Certificate
########################
# We set domain_name = primary_domain
# And if we are in prod, we also set subject_alternative_names for the "www" domain
certificate = aws.acm.Certificate(
    "siteCertificate",
    domain_name=primary_domain,
    subject_alternative_names=alt_domains if stack == "prod" else None,
    validation_method="DNS",
)

# 3) Create DNS validation records using an .apply()
def create_validation_records(domain_validation_options):
    records = []
    for idx, dvo in enumerate(domain_validation_options):
        record = aws.route53.Record(
            f"certValidationRecord-{idx}",
            zone_id=aws.route53.get_zone(name=BASE_DOMAIN).zone_id,
            name=dvo.resource_record_name,
            type=dvo.resource_record_type,
            records=[dvo.resource_record_value],
            ttl=60
        )
        records.append(record)
    return records

validation_records = certificate.domain_validation_options.apply(
    lambda dvos: create_validation_records(dvos)
)

# 4) Certificate validation resource.
# We also need to wait until 'validation_records' is created
# in order to get the FQDNs to pass into CertificateValidation.
certificate_validation = pulumi.Output.all(
    certificate_arn=certificate.arn,
    records=validation_records
).apply(lambda args: aws.acm.CertificateValidation(
    "siteCertificateValidation",
    certificate_arn=args["certificate_arn"],
    validation_record_fqdns=[r.fqdn for r in args["records"]],
))

########################
# 5) Create S3 Bucket (for static site) + Public Access
########################
site_bucket = aws.s3.Bucket(
    "siteBucket",
    website={
        "indexDocument": "index.html",
        "errorDocument": "index.html",  # Serve index.html on 404
    },
)

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

########################
# 6) Create CloudFront Distribution
########################
# If prod, we set 'aliases' = [tylernorlund.com, www.tylernorlund.com]
# Otherwise, it's just [<stack>.tylernorlund.com]
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
    custom_error_responses=[
        {
        "errorCode": 403,
        "responseCode": 200,
        "responsePagePath": "/index.html",
    },
    {
        "errorCode": 404,
        "responseCode": 200,
        "responsePagePath": "/index.html",
    },
    ],
    # Add all domain names in 'aliases'
    aliases=site_domains,
    # Ensure the cert is fully validated before creating the distribution
    opts=pulumi.ResourceOptions(depends_on=[certificate_validation]),
)

pulumi.export("cdn_distribution_id", cdn.id)

########################
# 7) Route53 Alias Records
########################
# We create an Alias A record for each domain in 'site_domains'.
# Each domain needs an ALIAS that points to the CloudFront distribution.
hosted_zone = aws.route53.get_zone(name=BASE_DOMAIN)

for domain in site_domains:
    aws.route53.Record(
        f"aliasRecord-{domain}",
        zone_id=hosted_zone.zone_id,
        name=domain,
        type="A",
        aliases=[{
            "name": cdn.domain_name,
            "zone_id": cdn.hosted_zone_id,
            "evaluateTargetHealth": True,
        }],
    )

########################
# 8) Exports
########################
pulumi.export("cdn_bucket_name", site_bucket.bucket)
pulumi.export("domains", site_domains)
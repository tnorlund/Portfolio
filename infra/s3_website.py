import json

import pulumi
import pulumi_aws as aws

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
# And if we are in prod, we also set subject_alternative_names for the
# "www" domain
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
            ttl=60,
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
    certificate_arn=certificate.arn, records=validation_records
).apply(
    lambda args: aws.acm.CertificateValidation(
        "siteCertificateValidation",
        certificate_arn=args["certificate_arn"],
        validation_record_fqdns=[r.fqdn for r in args["records"]],
    )
)

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
        lambda bucket_name: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{bucket_name}/*",
                    }
                ],
            }
        )
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
# 6) CloudFront Function (Enhanced for Performance)
########################
js_optimization_function = aws.cloudfront.Function(
    "jsOptimizationFunction",
    runtime="cloudfront-js-2.0",  # Use latest runtime for better performance
    comment="Optimize JavaScript delivery, handle clean URLs and add performance headers",
    publish=True,
    code="""
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    var headers = request.headers;
    
    // Handle clean URLs and trailing slashes (existing logic)
    if (uri !== '/' && uri.endsWith('/')) {
        return {
            statusCode: 301,
            statusDescription: 'Moved Permanently',
            headers: { location: { value: uri.slice(0, -1) } }
        };
    }
    
    // Add preload hints for critical JavaScript chunks based on route
    if (uri === '/' || uri === '/receipt' || uri === '/receipt.html') {
        if (!headers['cloudfront-viewer-country']) {
            headers['cloudfront-viewer-country'] = { value: 'US' };
        }
        
        // Add early hints for critical resources
        headers['x-preload-hint'] = { 
            value: 'vendor.js,main.js,common.js'
        };
    }
    
    // Optimize caching for Next.js static chunks
    if (uri.includes('/_next/static/chunks/') && uri.endsWith('.js')) {
        headers['x-cache-control-override'] = { 
            value: 'public,max-age=31536000,immutable'
        };
    }
    
    // Handle SPA routing
    if (!uri.includes('.') && uri !== '/') {
        request.uri = uri + '.html';
    }
    
    return request;
}
""",
)

# 7) Create CloudFront Distribution (Optimized for Performance)
########################
# If prod, we set 'aliases' = [tylernorlund.com, www.tylernorlund.com]
# Otherwise, it's just [<stack>.tylernorlund.com]
cdn = aws.cloudfront.Distribution(
    "cdn",
    origins=[
        {
            "domainName": site_bucket.bucket_regional_domain_name,
            "originId": site_bucket.id,
        }
    ],
    enabled=True,
    default_root_object="index.html",
    # HTTP/3 support for faster connection establishment
    http_version="http2and3",
    # Optimized cache behaviors for different content types
    ordered_cache_behaviors=[
        {
            # Cache behavior for Next.js JavaScript chunks - Maximum caching
            "pathPattern": "/_next/static/chunks/*",
            "targetOriginId": site_bucket.id,
            "viewerProtocolPolicy": "redirect-to-https",
            "allowedMethods": ["GET", "HEAD"],
            "cachedMethods": ["GET", "HEAD"],
            "forwardedValues": {
                "queryString": False,
                "cookies": {"forward": "none"},
            },
            "minTtl": 31536000,  # 1 year - immutable chunks
            "defaultTtl": 31536000,  # 1 year
            "maxTtl": 31536000,  # 1 year
            "compress": True,  # Enable gzip/brotli compression
        },
        {
            # Cache behavior for other Next.js static assets
            "pathPattern": "/_next/static/*",
            "targetOriginId": site_bucket.id,
            "viewerProtocolPolicy": "redirect-to-https",
            "allowedMethods": ["GET", "HEAD"],
            "cachedMethods": ["GET", "HEAD"],
            "forwardedValues": {
                "queryString": False,
                "cookies": {"forward": "none"},
            },
            "minTtl": 86400,  # 24 hours
            "defaultTtl": 31536000,  # 1 year
            "maxTtl": 31536000,  # 1 year
            "compress": True,
        },
        {
            # Cache behavior for other static assets (images, fonts, etc.)
            "pathPattern": "/static/*",
            "targetOriginId": site_bucket.id,
            "viewerProtocolPolicy": "redirect-to-https",
            "allowedMethods": ["GET", "HEAD"],
            "cachedMethods": ["GET", "HEAD"],
            "forwardedValues": {
                "queryString": False,
                "cookies": {"forward": "none"},
            },
            "minTtl": 86400,  # 24 hours
            "defaultTtl": 2592000,  # 30 days
            "maxTtl": 31536000,  # 1 year
            "compress": True,
        },
    ],
    # Default cache behavior for HTML and other content
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
        "defaultTtl": 3600,  # 1 hour for HTML
        "maxTtl": 86400,  # 24 hours max
        "compress": True,  # Enable compression
        "functionAssociations": [
            {
                "eventType": "viewer-request",
                "functionArn": js_optimization_function.arn,
            }
        ],
    },
    # Upgrade to better edge locations for improved performance
    price_class="PriceClass_200",  # US, Canada, Europe, Asia
    restrictions={"geoRestriction": {"restrictionType": "none"}},
    # Enhanced TLS configuration
    viewer_certificate={
        "acmCertificateArn": certificate_validation.certificate_arn,
        "sslSupportMethod": "sni-only",
        "minimumProtocolVersion": "TLSv1.2_2021",  # Latest TLS version
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
# 8) Route53 Alias Records
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
        aliases=[
            {
                "name": cdn.domain_name,
                "zone_id": cdn.hosted_zone_id,
                "evaluateTargetHealth": True,
            }
        ],
    )

########################
# 9) Exports
########################
pulumi.export("cdn_bucket_name", site_bucket.bucket)
pulumi.export("domains", site_domains)

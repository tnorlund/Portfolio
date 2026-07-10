import json

import pulumi
import pulumi_aws as aws

# Detect the current Pulumi stack
stack = pulumi.get_stack()
config = pulumi.Config("portfolio")
cloudfront_log_retention_days = (
    config.get_int("cloudFrontLogRetentionDays") or 90
)
google_analytics_measurement_id = (
    config.get("googleAnalyticsMeasurementId") or ""
)
google_tag_manager_id = config.get("googleTagManagerId") or ""

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
    # Keep minimal bucket config and ignore ACL/grant changes to avoid PutBucketAcl on ACL-disabled buckets
    opts=pulumi.ResourceOptions(ignore_changes=["acl", "grants"]),
)

# Enforce bucket owner ownership controls (disables ACLs)
site_bucket_ownership = aws.s3.BucketOwnershipControls(
    "siteBucket-ownership",
    bucket=site_bucket.id,
    rule={"objectOwnership": "BucketOwnerEnforced"},
)

# Configure CORS as a separate resource
site_bucket_cors = aws.s3.BucketCorsConfiguration(
    "siteBucket-cors",
    bucket=site_bucket.id,
    cors_rules=[
        aws.s3.BucketCorsConfigurationCorsRuleArgs(
            allowed_headers=["*"],
            allowed_methods=["GET", "HEAD"],
            allowed_origins=[
                "https://tylernorlund.com",
                "https://www.tylernorlund.com",
                "https://dev.tylernorlund.com",
                "http://localhost:3000",  # For development
                "http://localhost:3001",  # Alternative dev port
            ],
            expose_headers=["ETag"],
            max_age_seconds=3000,
        )
    ],
)

# Note: Bucket policy will be created after CDN to avoid circular dependency

# Keep S3 bucket private (no public access)
public_access_block = aws.s3.BucketPublicAccessBlock(
    "publicAccessBlock",
    bucket=site_bucket.id,
    block_public_acls=True,
    ignore_public_acls=True,
    block_public_policy=True,
    restrict_public_buckets=True,
)

########################
# 5b) CloudFront Standard Log Bucket
########################
cloudfront_logs_bucket = aws.s3.Bucket(
    "cloudfrontLogsBucket",
    force_destroy=False,
)

cloudfront_logs_bucket_ownership = aws.s3.BucketOwnershipControls(
    "cloudfrontLogsBucket-ownership",
    bucket=cloudfront_logs_bucket.id,
    rule={"objectOwnership": "BucketOwnerPreferred"},
)

cloudfront_logs_bucket_acl = aws.s3.BucketAcl(
    "cloudfrontLogsBucket-acl",
    bucket=cloudfront_logs_bucket.id,
    acl="log-delivery-write",
    opts=pulumi.ResourceOptions(
        depends_on=[cloudfront_logs_bucket_ownership]
    ),
)

cloudfront_logs_public_access_block = aws.s3.BucketPublicAccessBlock(
    "cloudfrontLogsBucket-publicAccessBlock",
    bucket=cloudfront_logs_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=False,
    restrict_public_buckets=True,
)

cloudfront_logs_lifecycle = aws.s3.BucketLifecycleConfiguration(
    "cloudfrontLogsBucket-lifecycle",
    bucket=cloudfront_logs_bucket.id,
    rules=[
        aws.s3.BucketLifecycleConfigurationRuleArgs(
            id="expire-cloudfront-standard-logs",
            status="Enabled",
            filter=aws.s3.BucketLifecycleConfigurationRuleFilterArgs(
                prefix="cloudfront/",
            ),
            expiration=aws.s3.BucketLifecycleConfigurationRuleExpirationArgs(
                days=cloudfront_log_retention_days,
            ),
        )
    ],
)

# The live collector must exist before the distribution is declared so its
# Function URL can be wired as a custom origin. Fixed analytics names are kept
# production-only, matching the durable WebAnalytics component.
live_web_analytics = None
if stack == "prod":
    from components.web_analytics import LiveWebAnalytics

    live_web_analytics = LiveWebAnalytics("web-analytics-live")

########################
# 6) CloudFront Function (Enhanced for Performance)
########################
js_optimization_function = aws.cloudfront.Function(
    "jsOptimizationFunction",
    runtime="cloudfront-js-2.0",  # Use latest runtime for better performance
    comment="Optimize delivery, handle clean URLs, and add performance headers for images and JS",
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

    // Handle image requests - ensure proper headers for AVIF/WebP
    if (uri.startsWith('/assets/') && (uri.endsWith('.avif') || uri.endsWith('.webp') || uri.endsWith('.jpg'))) {
        // Ensure Accept header is preserved for content negotiation
        if (!headers.accept) {
            headers.accept = { value: 'image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8' };
        }

        // Add cache-control hint for images
        headers['x-image-request'] = { value: 'true' };

        // Log AVIF requests for debugging (will appear in CloudWatch)
        if (uri.endsWith('.avif')) {
            console.log('AVIF request: ' + uri);
        }
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

    // Handle static pages and SPA fallback
    if (!uri.includes('.') && uri !== '/' && !uri.startsWith('/assets/')) {
        var staticPages = ['/receipt', '/resume'];
        if (staticPages.indexOf(uri) > -1) {
            request.uri = uri + '.html';
        } else {
            request.uri = '/index.html';
        }
    }

    return request;
}
""",
)

# CloudFront function for setting proper Content-Type headers on image responses
content_type_function = aws.cloudfront.Function(
    "contentTypeFunction",
    name=f"content-type-{stack}",
    runtime="cloudfront-js-2.0",
    comment="Set proper Content-Type headers for image files",
    publish=True,
    code="""
function handler(event) {
    var response = event.response;
    var request = event.request;
    var uri = request.uri;

    // Set proper Content-Type for image files in /assets/ directory
    if (uri.startsWith('/assets/')) {
        if (uri.endsWith('.avif')) {
            response.headers['content-type'] = { value: 'image/avif' };
        } else if (uri.endsWith('.webp')) {
            response.headers['content-type'] = { value: 'image/webp' };
        } else if (uri.endsWith('.jpg') || uri.endsWith('.jpeg')) {
            response.headers['content-type'] = { value: 'image/jpeg' };
        } else if (uri.endsWith('.png')) {
            response.headers['content-type'] = { value: 'image/png' };
        }
    }

    return response;
}
""",
)

# 7) Create CloudFront Distribution (Optimized for Performance)
########################
# If prod, we set 'aliases' = [tylernorlund.com, www.tylernorlund.com]
# Otherwise, it's just [<stack>.tylernorlund.com]

# Create Origin Access Control for secure S3 access
origin_access_control = aws.cloudfront.OriginAccessControl(
    "originAccessControl",
    name=f"OAC-{stack}",  # Use stack name instead of bucket ID
    origin_access_control_origin_type="s3",
    signing_behavior="always",
    signing_protocol="sigv4",
    description="Origin Access Control for S3 bucket",
)

# Create a response headers policy for CORS
cors_response_headers_policy = aws.cloudfront.ResponseHeadersPolicy(
    "corsResponseHeadersPolicy",
    name=f"CORS-Policy-{stack}",
    comment="CORS policy for image assets",
    cors_config={
        "access_control_allow_credentials": False,
        "access_control_allow_headers": {
            "items": ["*"],
        },
        "access_control_allow_methods": {
            "items": ["GET", "HEAD"],
        },
        "access_control_allow_origins": {
            "items": [
                "https://tylernorlund.com",
                "https://www.tylernorlund.com",
                "https://dev.tylernorlund.com",
                "http://localhost:3000",
                "http://localhost:3001",
            ],
        },
        "access_control_max_age_sec": 86400,
        "origin_override": False,
    },
)

cdn_origins = [
    {
        # Use S3 REST API endpoint instead of website hosting for better AVIF support
        "domainName": site_bucket.bucket_domain_name,
        "originId": site_bucket.id,
        "originAccessControlId": origin_access_control.id,
        "s3OriginConfig": {
            "originAccessIdentity": "",  # Required but empty when using OAC
        },
    }
]
live_cache_behaviors = []
origin_groups = []
if live_web_analytics is not None:
    live_origin_id = "web-analytics-live-collector"
    live_origin_group_id = "web-analytics-live-failover"
    cdn_origins.append(
        {
            "domainName": live_web_analytics.function_url_domain,
            "originId": live_origin_id,
            "connectionAttempts": 1,
            "connectionTimeout": 2,
            "originAccessControlId": (
                live_web_analytics.origin_access_control_id
            ),
            "customOriginConfig": {
                "httpPort": 80,
                "httpsPort": 443,
                "originProtocolPolicy": "https-only",
                "originSslProtocols": ["TLSv1.2"],
                "originReadTimeout": 6,
            },
        }
    )
    origin_groups.append(
        {
            "originId": live_origin_group_id,
            "failoverCriteria": {
                "statusCodes": [
                    400,
                    403,
                    404,
                    429,
                    500,
                    502,
                    503,
                    504,
                ]
            },
            "members": [
                {"originId": live_origin_id},
                {"originId": site_bucket.id},
            ],
        }
    )
    live_cache_behaviors.append(
        {
            "pathPattern": "/analytics/pixel.txt",
            "targetOriginId": live_origin_group_id,
            "viewerProtocolPolicy": "redirect-to-https",
            "allowedMethods": ["GET", "HEAD"],
            "cachedMethods": ["GET", "HEAD"],
            # AWS-managed CachingDisabled policy. Query strings are forwarded
            # separately by the narrow origin policy and are never cached.
            "cachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
            "originRequestPolicyId": (
                live_web_analytics.origin_request_policy_id
            ),
            "compress": False,
        }
    )

cdn = aws.cloudfront.Distribution(
    "cdn",
    origins=cdn_origins,
    origin_groups=origin_groups,
    enabled=True,
    default_root_object="index.html",
    logging_config=aws.cloudfront.DistributionLoggingConfigArgs(
        bucket=cloudfront_logs_bucket.bucket_domain_name,
        include_cookies=False,
        prefix=f"cloudfront/{stack}/",
    ),
    # HTTP/3 support for faster connection establishment
    http_version="http2and3",
    # Optimized cache behaviors for different content types
    ordered_cache_behaviors=[
        *live_cache_behaviors,
        {
            # Cache behavior for receipt images (AVIF, WebP, JPEG) - With CORS support
            "pathPattern": "/assets/*",
            "targetOriginId": site_bucket.id,
            "viewerProtocolPolicy": "redirect-to-https",
            "allowedMethods": ["GET", "HEAD"],
            "cachedMethods": ["GET", "HEAD"],
            "forwardedValues": {
                "queryString": False,
                "cookies": {"forward": "none"},
                "headers": [
                    "Accept",
                    "Accept-Encoding",
                    "Origin",  # Required for CORS
                    "Access-Control-Request-Method",
                    "Access-Control-Request-Headers",
                ],
            },
            "responseHeadersPolicyId": cors_response_headers_policy.id,  # Add CORS headers
            "minTtl": 86400,  # 24 hours
            "defaultTtl": 2592000,  # 30 days for images
            "maxTtl": 31536000,  # 1 year max
            "compress": False,  # Don't compress images (they're already compressed)
            "functionAssociations": [
                {
                    "eventType": "viewer-response",
                    "functionArn": content_type_function.arn,
                }
            ],
        },
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
    opts=pulumi.ResourceOptions(
        depends_on=[certificate_validation, cloudfront_logs_bucket_acl]
    ),
)

pulumi.export("cdn_distribution_id", cdn.id)

if live_web_analytics is not None:
    # New Function URLs (October 2025 onward) require both resource-policy
    # actions. Scope both to this distribution and URL-only invocation.
    aws.lambda_.Permission(
        "web-analytics-live-cloudfront-url-permission",
        action="lambda:InvokeFunctionUrl",
        function=live_web_analytics.collector_lambda.name,
        principal="cloudfront.amazonaws.com",
        source_arn=cdn.arn,
        function_url_auth_type="AWS_IAM",
        opts=pulumi.ResourceOptions(
            parent=live_web_analytics.collector_lambda,
            depends_on=[cdn],
        ),
    )
    aws.lambda_.Permission(
        "web-analytics-live-cloudfront-invoke-permission",
        action="lambda:InvokeFunction",
        function=live_web_analytics.collector_lambda.name,
        principal="cloudfront.amazonaws.com",
        source_arn=cdn.arn,
        invoked_via_function_url=True,
        opts=pulumi.ResourceOptions(
            parent=live_web_analytics.collector_lambda,
            depends_on=[cdn],
        ),
    )

# S3 bucket policy for Origin Access Control (now that CDN is defined)
bucket_policy = aws.s3.BucketPolicy(
    "bucketPolicy",
    bucket=site_bucket.bucket,
    policy=pulumi.Output.all(
        bucket_name=site_bucket.bucket,
        distribution_id=cdn.id,
    ).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "cloudfront.amazonaws.com"},
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{args['bucket_name']}/*",
                        "Condition": {
                            "StringEquals": {
                                "AWS:SourceArn": f"arn:aws:cloudfront::{aws.get_caller_identity().account_id}:distribution/{args['distribution_id']}"
                            }
                        },
                    }
                ],
            }
        )
    ),
    opts=pulumi.ResourceOptions(depends_on=[cdn]),
)

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
pulumi.export("cloudfront_logs_bucket_name", cloudfront_logs_bucket.bucket)
if live_web_analytics is not None:
    pulumi.export("web_events_live_table_name", live_web_analytics.table_name)
    pulumi.export(
        "web_analytics_collector_lambda_name",
        live_web_analytics.collector_lambda.name,
    )
    pulumi.export("web_analytics_collector_path", "/analytics/pixel.txt")
pulumi.export(
    "cloudfront_log_retention_days", cloudfront_log_retention_days
)
pulumi.export("domains", site_domains)
pulumi.export(
    "google_analytics_measurement_id", google_analytics_measurement_id
)
pulumi.export("google_tag_manager_id", google_tag_manager_id)

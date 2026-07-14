"""Public HTTP API, static Lambda routes, logging, and custom domain."""

import pulumi
import pulumi_aws as aws

# Import cache generator routes first (API routes depend on them).
import routes.address_similarity_cache_generator.infra  # noqa: F401
import routes.image_details_cache_generator.infra  # noqa: F401
import routes.layoutlm_inference_cache_generator.infra  # noqa: F401
from components.http_api_route import create_lambda_route
from routes.address_similarity.infra import address_similarity_lambda
from routes.ai_usage.infra import ai_usage_lambda
from routes.health_check.infra import health_check_lambda
from routes.image_count.infra import image_count_lambda
from routes.image_details_cache.infra import image_details_cache_lambda
from routes.images.infra import images_lambda
from routes.job_training_metrics.infra import job_training_metrics_lambda
from routes.label_validation_count.infra import label_validation_count_lambda
from routes.merchant_counts.infra import merchant_counts_lambda
from routes.process.infra import process_lambda
from routes.random_image_details.infra import random_image_details_lambda
from routes.random_receipt_details.infra import random_receipt_details_lambda
from routes.reader_summary.infra import reader_summary_lambda
from routes.receipt_count.infra import receipt_count_lambda
from routes.receipts.infra import receipts_lambda

BASE_DOMAIN = "tylernorlund.com"
ALLOWED_API_ORIGINS = (
    "http://localhost:3000",
    "https://tylernorlund.com",
    "https://www.tylernorlund.com",
    "https://dev.tylernorlund.com",
)
ACCESS_LOG_FORMAT = (
    "{"
    '"requestId":"$context.requestId",'
    '"ip":"$context.identity.sourceIp",'
    '"caller":"$context.identity.caller",'
    '"user":"$context.identity.user",'
    '"requestTime":"$context.requestTime",'
    '"httpMethod":"$context.httpMethod",'
    '"resourcePath":"$context.resourcePath",'
    '"status":"$context.status",'
    '"protocol":"$context.protocol",'
    '"responseLength":"$context.responseLength"'
    "}"
)


def _domain_name(stack_name: str) -> str:
    """Return the stack-specific API hostname."""
    subdomain = "api" if stack_name == "prod" else f"{stack_name}-api"
    return f"{subdomain}.{BASE_DOMAIN}"


stack = pulumi.get_stack()
api_domain_name = _domain_name(stack)

api = aws.apigatewayv2.Api(
    "my-api",
    protocol_type="HTTP",
    cors_configuration=aws.apigatewayv2.ApiCorsConfigurationArgs(
        allow_origins=list(ALLOWED_API_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Type"],
        allow_credentials=True,
        max_age=3600,
    ),
)

# Routes whose Lambdas exist when this module is imported. Routes backed by
# conditionally-created Lambdas are registered from __main__.py with the same
# shared helper after those functions are available.
_STATIC_ROUTES = (
    ("health_check", "GET /health_check", health_check_lambda),
    (
        "random_image_details",
        "GET /random_image_details",
        random_image_details_lambda,
    ),
    ("image_count", "GET /image_count", image_count_lambda),
    ("images", "GET /images", images_lambda),
    (
        "label_validation_count",
        "GET /label_validation_count",
        label_validation_count_lambda,
    ),
    ("merchant_counts", "GET /merchant_counts", merchant_counts_lambda),
    ("receipt_count", "GET /receipt_count", receipt_count_lambda),
    ("receipts", "GET /receipts", receipts_lambda),
    (
        "random_receipt_details",
        "GET /random_receipt_details",
        random_receipt_details_lambda,
    ),
    (
        "address_similarity",
        "GET /address_similarity",
        address_similarity_lambda,
    ),
    (
        "image_details_cache",
        "GET /image_details_cache",
        image_details_cache_lambda,
    ),
    ("process", "GET /process", process_lambda),
    ("ai_usage", "GET /ai_usage", ai_usage_lambda),
    ("reader_summary", "POST /reader_summary", reader_summary_lambda),
    (
        "job_training_metrics",
        "GET /jobs/{job_id}/training-metrics",
        job_training_metrics_lambda,
    ),
)

static_route_resources = {
    name: create_lambda_route(
        api=api,
        integration_name=f"{name}_lambda_integration",
        route_name=f"{name}_route",
        route_key=route_key,
        lambda_function=lambda_function,
        permission_name=f"{name}_lambda_permission",
    )
    for name, route_key, lambda_function in _STATIC_ROUTES
}

log_group = aws.cloudwatch.LogGroup(
    "api_gateway_log_group",
    name=api.id.apply(
        lambda api_id: f"API-Gateway-Execution-Logs_{api_id}_default"
    ),
    retention_in_days=14,
)

stage = aws.apigatewayv2.Stage(
    "api_stage",
    api_id=api.id,
    name="$default",
    auto_deploy=True,
    default_route_settings=aws.apigatewayv2.StageDefaultRouteSettingsArgs(
        throttling_burst_limit=10000,
        throttling_rate_limit=20000,
        detailed_metrics_enabled=True,
    ),
    route_settings=[
        aws.apigatewayv2.StageRouteSettingArgs(
            route_key="POST /reader_summary",
            throttling_burst_limit=10,
            throttling_rate_limit=2,
            detailed_metrics_enabled=True,
        )
    ],
    access_log_settings=aws.apigatewayv2.StageAccessLogSettingsArgs(
        destination_arn=log_group.arn,
        format=ACCESS_LOG_FORMAT,
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[
            resource.route for resource in static_route_resources.values()
        ]
    ),
)

hosted_zone = aws.route53.get_zone(name=BASE_DOMAIN)

api_certificate = aws.acm.Certificate(
    "apiCertificate",
    domain_name=api_domain_name,
    validation_method="DNS",
)

api_cert_validation_options = api_certificate.domain_validation_options[0]
api_cert_validation_record = aws.route53.Record(
    "apiCertValidationRecord",
    zone_id=hosted_zone.zone_id,
    name=api_cert_validation_options.resource_record_name,
    type=api_cert_validation_options.resource_record_type,
    records=[api_cert_validation_options.resource_record_value],
    ttl=60,
)

api_certificate_validation = aws.acm.CertificateValidation(
    "apiCertificateValidation",
    certificate_arn=api_certificate.arn,
    validation_record_fqdns=[api_cert_validation_record.fqdn],
)

api_custom_domain = aws.apigatewayv2.DomainName(
    "apiCustomDomain",
    domain_name=api_domain_name,
    domain_name_configuration=(
        aws.apigatewayv2.DomainNameDomainNameConfigurationArgs(
            certificate_arn=api_certificate_validation.certificate_arn,
            endpoint_type="REGIONAL",
            security_policy="TLS_1_2",
        )
    ),
    opts=pulumi.ResourceOptions(depends_on=[api_certificate_validation]),
)

api_mapping = aws.apigatewayv2.ApiMapping(
    "apiBasePathMapping",
    api_id=api.id,
    domain_name=api_custom_domain.id,
    stage=stage.id,
)

api_alias_record = aws.route53.Record(
    "apiAliasRecord",
    zone_id=hosted_zone.zone_id,
    name=api_domain_name,
    type="A",
    aliases=[
        {
            "name": (
                api_custom_domain.domain_name_configuration.target_domain_name
            ),
            "zone_id": (
                api_custom_domain.domain_name_configuration.hosted_zone_id
            ),
            "evaluateTargetHealth": True,
        }
    ],
)

pulumi.export("api_domain", f"https://{api_domain_name}")

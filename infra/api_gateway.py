import pulumi
import pulumi_aws as aws
from routes.ai_usage.infra import ai_usage_lambda

# Import your Lambda/route definitions
from routes.health_check.infra import health_check_lambda
from routes.image_count.infra import image_count_lambda
from routes.images.infra import images_lambda
from routes.label_validation_count.infra import label_validation_count_lambda
from routes.merchant_counts.infra import merchant_counts_lambda
from routes.process.infra import process_lambda
from routes.random_image_details.infra import random_image_details_lambda
from routes.random_receipt_details.infra import random_receipt_details_lambda
from routes.receipt_count.infra import receipt_count_lambda
from routes.receipts.infra import receipts_lambda
# Import cache generator route first (API route depends on it)
import routes.address_similarity_cache_generator.infra  # noqa: F401
from routes.address_similarity.infra import address_similarity_lambda

# Detect the current Pulumi stack
stack = pulumi.get_stack()

BASE_DOMAIN = "tylernorlund.com"

# For "prod" => api.tylernorlund.com
# otherwise   => dev-api.tylernorlund.com
if stack == "prod":
    api_domain_name = f"api.{BASE_DOMAIN}"
else:
    api_domain_name = f"{stack}-api.{BASE_DOMAIN}"

# ─────────────────────────────────────────────────────────────────────────────────
# 1. MAIN API DEFINITION
# ─────────────────────────────────────────────────────────────────────────────────
api = aws.apigatewayv2.Api(
    "my-api",
    protocol_type="HTTP",
    cors_configuration=aws.apigatewayv2.ApiCorsConfigurationArgs(
        allow_origins=[
            "http://localhost:3000",
            "https://tylernorlund.com",
            "https://www.tylernorlund.com",
            "https://dev.tylernorlund.com",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Type"],
        allow_credentials=True,
        max_age=3600,
    ),
)

# Define your integrations and routes
# ------------------------------------------------------------------------------
# /health_check
integration_health_check = aws.apigatewayv2.Integration(
    "health_check_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=health_check_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_health_check = aws.apigatewayv2.Route(
    "health_check_route",
    api_id=api.id,
    route_key="GET /health_check",
    target=integration_health_check.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_health_check = aws.lambda_.Permission(
    "health_check_lambda_permission",
    action="lambda:InvokeFunction",
    function=health_check_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /random_image_details
integration_random_image_details = aws.apigatewayv2.Integration(
    "random_image_details_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=random_image_details_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_random_image_details = aws.apigatewayv2.Route(
    "random_image_details_route",
    api_id=api.id,
    route_key="GET /random_image_details",
    target=integration_random_image_details.id.apply(
        lambda id: f"integrations/{id}"
    ),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_random_image_details = aws.lambda_.Permission(
    "random_image_details_lambda_permission",
    action="lambda:InvokeFunction",
    function=random_image_details_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /image_count
integration_image_count = aws.apigatewayv2.Integration(
    "image_count_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=image_count_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_image_count = aws.apigatewayv2.Route(
    "image_count_route",
    api_id=api.id,
    route_key="GET /image_count",
    target=integration_image_count.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_image_count = aws.lambda_.Permission(
    "image_count_lambda_permission",
    action="lambda:InvokeFunction",
    function=image_count_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /images
integration_images = aws.apigatewayv2.Integration(
    "images_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=images_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_images = aws.apigatewayv2.Route(
    "images_route",
    api_id=api.id,
    route_key="GET /images",
    target=integration_images.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_images = aws.lambda_.Permission(
    "images_lambda_permission",
    action="lambda:InvokeFunction",
    function=images_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /label_validation_count
integration_label_validation_count = aws.apigatewayv2.Integration(
    "label_validation_count_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=label_validation_count_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_label_validation_count = aws.apigatewayv2.Route(
    "label_validation_count_route",
    api_id=api.id,
    route_key="GET /label_validation_count",
    target=integration_label_validation_count.id.apply(
        lambda id: f"integrations/{id}"
    ),  # noqa: E501
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_label_validation_count = aws.lambda_.Permission(
    "label_validation_count_lambda_permission",
    action="lambda:InvokeFunction",
    function=label_validation_count_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /merchant_counts
integration_merchant_counts = aws.apigatewayv2.Integration(
    "merchant_counts_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=merchant_counts_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_merchant_counts = aws.apigatewayv2.Route(
    "merchant_counts_route",
    api_id=api.id,
    route_key="GET /merchant_counts",
    target=integration_merchant_counts.id.apply(
        lambda id: f"integrations/{id}"
    ),  # noqa: E501
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_merchant_counts = aws.lambda_.Permission(
    "merchant_counts_lambda_permission",
    action="lambda:InvokeFunction",
    function=merchant_counts_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /receipt_count
integration_receipt_count = aws.apigatewayv2.Integration(
    "receipt_count_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=receipt_count_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_receipt_count = aws.apigatewayv2.Route(
    "receipt_count_route",
    api_id=api.id,
    route_key="GET /receipt_count",
    target=integration_receipt_count.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_receipt_count = aws.lambda_.Permission(
    "receipt_count_lambda_permission",
    action="lambda:InvokeFunction",
    function=receipt_count_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /receipts
integration_receipts = aws.apigatewayv2.Integration(
    "receipts_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=receipts_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_receipts = aws.apigatewayv2.Route(
    "receipts_route",
    api_id=api.id,
    route_key="GET /receipts",
    target=integration_receipts.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_receipts = aws.lambda_.Permission(
    "receipts_lambda_permission",
    action="lambda:InvokeFunction",
    function=receipts_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)


# /random_receipt_details
integration_random_receipt_details = aws.apigatewayv2.Integration(
    "random_receipt_details_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=random_receipt_details_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_random_receipt_details = aws.apigatewayv2.Route(
    "random_receipt_details_route",
    api_id=api.id,
    route_key="GET /random_receipt_details",
    target=integration_random_receipt_details.id.apply(
        lambda id: f"integrations/{id}"
    ),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_random_receipt_details = aws.lambda_.Permission(
    "random_receipt_details_lambda_permission",
    action="lambda:InvokeFunction",
    function=random_receipt_details_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# /address_similarity
integration_address_similarity = aws.apigatewayv2.Integration(
    "address_similarity_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=address_similarity_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_address_similarity = aws.apigatewayv2.Route(
    "address_similarity_route",
    api_id=api.id,
    route_key="GET /address_similarity",
    target=integration_address_similarity.id.apply(
        lambda id: f"integrations/{id}"
    ),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_address_similarity = aws.lambda_.Permission(
    "address_similarity_lambda_permission",
    action="lambda:InvokeFunction",
    function=address_similarity_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)


# /process
integration_process = aws.apigatewayv2.Integration(
    "process_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=process_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_process = aws.apigatewayv2.Route(
    "process_route",
    api_id=api.id,
    route_key="GET /process",
    target=integration_process.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_process = aws.lambda_.Permission(
    "process_lambda_permission",
    action="lambda:InvokeFunction",
    function=process_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)


# /ai_usage
integration_ai_usage = aws.apigatewayv2.Integration(
    "ai_usage_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=ai_usage_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_ai_usage = aws.apigatewayv2.Route(
    "ai_usage_route",
    api_id=api.id,
    route_key="GET /ai_usage",
    target=integration_ai_usage.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_ai_usage = aws.lambda_.Permission(
    "ai_usage_lambda_permission",
    action="lambda:InvokeFunction",
    function=ai_usage_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)


# Replace the if stack == "dev" block with these unconditional declarations

# ─────────────────────────────────────────────────────────────────────────────────
# 2. DEPLOYMENT + LOGGING
# ─────────────────────────────────────────────────────────────────────────────────
log_group = aws.cloudwatch.LogGroup(
    "api_gateway_log_group",
    name=api.id.apply(lambda id: f"API-Gateway-Execution-Logs_{id}_default"),
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
    access_log_settings=aws.apigatewayv2.StageAccessLogSettingsArgs(
        destination_arn=log_group.arn,
        format=(
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
        ),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────────
# 3. CUSTOM DOMAIN SETUP
# ─────────────────────────────────────────────────────────────────────────────────

# Lookup your existing Route 53 hosted zone for tylernorlund.com
hosted_zone = aws.route53.get_zone(name=BASE_DOMAIN)

api_certificate = aws.acm.Certificate(
    "apiCertificate",
    domain_name=api_domain_name,
    validation_method="DNS",
)

# Create a DNS validation record
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

# Create the actual custom domain in API Gateway v2
api_custom_domain = aws.apigatewayv2.DomainName(
    "apiCustomDomain",
    domain_name=api_domain_name,
    domain_name_configuration=aws.apigatewayv2.DomainNameDomainNameConfigurationArgs(  # noqa: E501
        certificate_arn=api_certificate_validation.certificate_arn,
        endpoint_type="REGIONAL",  # HTTP APIs only support REGIONAL
        security_policy="TLS_1_2",
    ),
    opts=pulumi.ResourceOptions(depends_on=[api_certificate_validation]),
)

# Map your API and stage to this new domain
api_mapping = aws.apigatewayv2.ApiMapping(
    "apiBasePathMapping",
    api_id=api.id,
    domain_name=api_custom_domain.id,
    stage=stage.id,  # or "$default"
)

# Create a Route 53 alias for the API domain
api_alias_record = aws.route53.Record(
    "apiAliasRecord",
    zone_id=hosted_zone.zone_id,
    name=api_domain_name,
    type="A",
    aliases=[
        {
            "name": (
                api_custom_domain.domain_name_configuration.target_domain_name
            ),  # noqa: E501
            "zone_id": (
                api_custom_domain.domain_name_configuration.hosted_zone_id
            ),  # noqa: E501
            "evaluateTargetHealth": True,
        }
    ],
)

# Finally, export your custom domain
pulumi.export("api_domain", f"https://{api_domain_name}")

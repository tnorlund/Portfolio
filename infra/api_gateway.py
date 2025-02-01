import pulumi
import pulumi_aws as aws

# Import your Lambda/route definitions
from routes.health_check.infra import health_check_lambda
from routes.images.infra import images_lambda
from routes.image_details.infra import image_details_lambda
from routes.receipts.infra import receipts_lambda
from routes.receipt_word_tag.infra import receipt_word_tag_lambda
from routes.process.infra import process_lambda
from raw_bucket import raw_bucket

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
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Length", "Content-Type"],
        allow_credentials=False,
        max_age=86400,
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

# /image_details
integration_image_details = aws.apigatewayv2.Integration(
    "image_details_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=image_details_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_image_details = aws.apigatewayv2.Route(
    "image_details_route",
    api_id=api.id,
    route_key="GET /image_details",
    target=integration_image_details.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_image_details = aws.lambda_.Permission(
    "image_details_lambda_permission",
    action="lambda:InvokeFunction",
    function=image_details_lambda.name,
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

# /receipt_word_tag
integration_receipt_word_tag = aws.apigatewayv2.Integration(
    "receipt_word_tag_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=receipt_word_tag_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_receipt_word_tag = aws.apigatewayv2.Route(
    "receipt_word_tag_route",
    api_id=api.id,
    route_key="GET /receipt_word_tag",
    target=integration_receipt_word_tag.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(
        replace_on_changes=["route_key", "target"],
        delete_before_replace=True,
    ),
)
lambda_permission_receipt_word_tag = aws.lambda_.Permission(
    "receipt_word_tag_lambda_permission",
    action="lambda:InvokeFunction",
    function=receipt_word_tag_lambda.name,
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
    route_settings=[
        {
            "routeKey": route_health_check.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        },
        {
            "routeKey": route_images.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        },
        {
            "routeKey": route_image_details.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        },
        {
            "routeKey": route_receipts.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        },
        {
            "routeKey": route_receipt_word_tag.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        },
        {
            "routeKey": route_process.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        },
    ],
    auto_deploy=True,
    access_log_settings=aws.apigatewayv2.StageAccessLogSettingsArgs(
        destination_arn=log_group.arn,
        format='{"requestId":"$context.requestId","ip":"$context.identity.sourceIp","caller":"$context.identity.caller","user":"$context.identity.user","requestTime":"$context.requestTime","httpMethod":"$context.httpMethod","resourcePath":"$context.resourcePath","status":"$context.status","protocol":"$context.protocol","responseLength":"$context.responseLength"}',
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
    domain_name_configuration=aws.apigatewayv2.DomainNameDomainNameConfigurationArgs(
        certificate_arn=api_certificate_validation.certificate_arn,
        endpoint_type="REGIONAL",  # HTTP APIs only support REGIONAL
        security_policy="TLS_1_2",
    ),
    opts=pulumi.ResourceOptions(depends_on=[api_certificate_validation]),
)

# Map your API + stage to this new domain (base path = empty string => "https://api.example.com/")
api_mapping = aws.apigatewayv2.ApiMapping(
    "apiBasePathMapping",
    api_id=api.id,
    domain_name=api_custom_domain.id,
    stage=stage.id,  # or "$default"
)

# Create a Route 53 alias to point "api.tylernorlund.com" or "dev-api.tylernorlund.com" -> the API domain
api_alias_record = aws.route53.Record(
    "apiAliasRecord",
    zone_id=hosted_zone.zone_id,
    name=api_domain_name,
    type="A",
    aliases=[
        {
            "name": api_custom_domain.domain_name_configuration.target_domain_name,
            "zone_id": api_custom_domain.domain_name_configuration.hosted_zone_id,
            "evaluateTargetHealth": True,
        }
    ],
)

# Finally, export your custom domain
pulumi.export("api_domain", f"https://{api_domain_name}")
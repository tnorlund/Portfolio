import pulumi
import pulumi_aws as aws

# Import the different routes
from routes.health_check.infra import health_check_lambda
from routes.images.infra import images_lambda
from routes.image_details.infra import image_details_lambda

api = aws.apigatewayv2.Api(
    "my-api",
    protocol_type="HTTP",
    cors_configuration=aws.apigatewayv2.ApiCorsConfigurationArgs(
        # Allow your local dev URL or "*" for all
        allow_origins=["*"],
        # Which HTTP methods to allow (GET, POST, OPTIONS, etc.)
        allow_methods=["GET", "POST", "OPTIONS"],
        # Which headers can be sent by the client
        allow_headers=["Content-Type", "Authorization"],
        # Whether or not the browser should expose response headers
        # to client-side JavaScript
        expose_headers=["Content-Length", "Content-Type"],
        # Whether or not cookies or Authorization headers are allowed
        allow_credentials=False,
        # How long (in seconds) to cache the response of a preflight request
        max_age=86400,  # 1 day
    ),
)

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

# DEPLOYMENT
log_group = aws.cloudwatch.LogGroup(
    "api_gateway_log_group",
    name=api.id.apply(lambda id: f"API-Gateway-Execution-Logs_{id}_default"),
    retention_in_days=14,  # Adjust the retention period as needed
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
    ],
    auto_deploy=True,
    access_log_settings=aws.apigatewayv2.StageAccessLogSettingsArgs(
        destination_arn=log_group.arn,
        format='{"requestId":"$context.requestId","ip":"$context.identity.sourceIp","caller":"$context.identity.caller","user":"$context.identity.user","requestTime":"$context.requestTime","httpMethod":"$context.httpMethod","resourcePath":"$context.resourcePath","status":"$context.status","protocol":"$context.protocol","responseLength":"$context.responseLength"}',
    ),
)

pulumi.export("api_endpoint", api.api_endpoint)

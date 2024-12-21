import pulumi
import pulumi_aws as aws
import s3_website  # noqa: F401
from routes.health_check.infra import health_check_lambda
from dynamo_db import dynamodb_table
import json

pulumi.export("region", aws.config.region)

# Define the API Gateway
api = aws.apigatewayv2.Api(
    "my-api",
    protocol_type="HTTP",
)

# Create the API Gateway Integration
integration = aws.apigatewayv2.Integration(
    "lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=health_check_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)


# Define a CloudWatch Log Group for API Gateway logs
log_group = aws.cloudwatch.LogGroup(
    "api_gateway_log_group",
    name=api.id.apply(lambda id: f"API-Gateway-Execution-Logs_{id}_default"),
    retention_in_days=14,  # Adjust the retention period as needed
)

log_group_role = aws.iam.Role(
    "log_group_role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "apigateway.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
)

# Ensure permissions for CloudWatch Logs
log_group_policy = aws.iam.Policy(
    "log_group_policy",
    description="Policy to allow API Gateway to write logs to CloudWatch",
    policy=log_group.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                        "Resource": f"{arn}:*",
                    }
                ],
            }
        )
    ),
)

log_group_role_policy_attachment = aws.iam.RolePolicyAttachment(
    "log_group_role_policy_attachment",
    role=log_group_role.name,
    policy_arn=log_group_policy.arn,
)


# Define the API Gateway Route
route = aws.apigatewayv2.Route(
    "health_check_route",
    api_id=api.id,
    route_key="GET /health_check",
    target=integration.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(replace_on_changes=["route_key", "target"]),
)

# Create the API Gateway Stage
stage = aws.apigatewayv2.Stage(
    "api_stage",
    api_id=api.id,
    name="$default",
    route_settings=[
        {
            "routeKey": route.route_key,
            "throttlingBurstLimit": 5000,
            "throttlingRateLimit": 10000,
        }
    ],
    auto_deploy=True,
    access_log_settings=aws.apigatewayv2.StageAccessLogSettingsArgs(
        destination_arn=log_group.arn,
        format='{"requestId":"$context.requestId","ip":"$context.identity.sourceIp","caller":"$context.identity.caller","user":"$context.identity.user","requestTime":"$context.requestTime","httpMethod":"$context.httpMethod","resourcePath":"$context.resourcePath","status":"$context.status","protocol":"$context.protocol","responseLength":"$context.responseLength"}',
    ),
)

# Add permission for API Gateway to invoke the Lambda function
lambda_permission = aws.lambda_.Permission(
    "lambda_permission",
    action="lambda:InvokeFunction",
    function=health_check_lambda.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# Export the API endpoint
pulumi.export("api_endpoint", api.api_endpoint)

# Open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())

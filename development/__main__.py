import pulumi
import pulumi_aws as aws
import s3_website  # noqa: F401
from dynamo_db import dynamodb_table
import json

pulumi.export("region", aws.config.region)

# Define the IAM role for the Lambda function with permissions for the DynamoDB table
lambda_role = aws.iam.Role("lambdaRole",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {
                    "Service": "lambda.amazonaws.com"
                },
                "Effect": "Allow",
                "Sid": ""
            }
        ]
    }"""
)

# Attach the necessary policies to the role
lambda_policy = aws.iam.Policy("lambdaPolicy",
    description="IAM policy for Lambda to access DynamoDB",
    policy=dynamodb_table.arn.apply(lambda arn: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Scan",
                    "dynamodb:Query"
                ],
                "Resource": arn
            }
        ]
    }))
)

lambda_role_policy_attachment = aws.iam.RolePolicyAttachment("lambdaRolePolicyAttachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn
)

# Attach the necessary policies to the role
aws.iam.RolePolicyAttachment("lambdaLoggingPolicy",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)

# Define the Lambda function and add the DynamoDB table as an environment variable
lambda_function = aws.lambda_.Function("myLambdaFunction",
    role=lambda_role.arn,
    runtime="python3.8",
    handler="index.lambda_handler",
    code=pulumi.AssetArchive({
        ".": pulumi.FileArchive("./lambda")
    }),
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
        },
    ),
)

# Create a CloudWatch Log Group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    "lambda_log_group",
    name=lambda_function.name.apply(lambda name: f"/aws/lambda/{name}"),
    retention_in_days=14,  # Adjust the retention period as needed
)

# Define the API Gateway
api = aws.apigatewayv2.Api("my-api",
    protocol_type="HTTP",
)

# Create the API Gateway Integration
integration = aws.apigatewayv2.Integration("lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=lambda_function.invoke_arn,
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
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "apigateway.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    })
)

# Ensure permissions for CloudWatch Logs
log_group_policy = aws.iam.Policy(
    "log_group_policy",
    description="Policy to allow API Gateway to write logs to CloudWatch",
    policy=log_group.arn.apply(lambda arn: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": f"{arn}:*"
            }
        ]
    }))
)

log_group_role_policy_attachment = aws.iam.RolePolicyAttachment(
    "log_group_role_policy_attachment",
    role=log_group_role.name,
    policy_arn=log_group_policy.arn
)


# Define the API Gateway Route
route = aws.apigatewayv2.Route("health_check_route",
    api_id=api.id,
    route_key="GET /health_check",
    target=integration.id.apply(lambda id: f"integrations/{id}"),
    opts=pulumi.ResourceOptions(replace_on_changes=["route_key", "target"]),
)

# Create the API Gateway Stage
stage = aws.apigatewayv2.Stage("api_stage",
    api_id=api.id,
    name="$default",
    route_settings=[{
        "routeKey": route.route_key,
        "throttlingBurstLimit": 5000,
        "throttlingRateLimit": 10000,
    }],
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
    function=lambda_function.name,
    principal="apigateway.amazonaws.com",
    source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
)

# Export the API endpoint
pulumi.export("api_endpoint", api.api_endpoint)

# Open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())

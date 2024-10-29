import pulumi
import pulumi_aws as aws

# Define the API Gateway
api = aws.apigatewayv2.Api(
    "my-api",
    protocol_type="HTTP",
)
"""
Optimized infrastructure for label validation count Lambda.

Key optimizations:
1. Right-sized memory allocation based on workload
2. Provisioned concurrency for consistent performance
3. Optimized IAM permissions (least privilege)
4. CloudWatch Insights for monitoring
5. Reserved concurrent executions
"""

import json
import os

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive, Config

# Import dependencies
from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer

# Configuration
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
DYNAMODB_TABLE_NAME = dynamodb_table.name

# Get configuration for environment
config = Config()
is_production = pulumi.get_stack() == "prod"

# Performance configuration based on environment
MEMORY_SIZE = 1536 if is_production else 512  # More memory = more CPU
TIMEOUT = 30  # Reduced from 300s - queries shouldn't take that long
RESERVED_CONCURRENT_EXECUTIONS = 100 if is_production else None
PROVISIONED_CONCURRENT_EXECUTIONS = 2 if is_production else 0

# Create optimized IAM role with minimal permissions
lambda_role = aws.iam.Role(
    f"api_{ROUTE_NAME}_lambda_role",
    description="Optimized role for label validation count Lambda",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Effect": "Allow"
        }]
    }),
    tags={"Name": f"api-{ROUTE_NAME}-lambda-role", "Environment": pulumi.get_stack()}
)

# Optimized IAM policy - only what's needed
lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="Minimal IAM policy for label validation count Lambda",
    policy=pulumi.Output.all(dynamodb_table.arn).apply(
        lambda arns: json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DynamoDBReadAccess",
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:Query",  # For GSI queries
                        "dynamodb:DescribeTable"  # For table metadata
                    ],
                    "Resource": [
                        arns[0],  # Main table
                        f"{arns[0]}/index/GSI1",  # For label queries
                        f"{arns[0]}/index/GSITYPE"  # For type queries
                    ]
                },
                {
                    "Sid": "CloudWatchMetrics",
                    "Effect": "Allow",
                    "Action": [
                        "cloudwatch:PutMetricData"
                    ],
                    "Resource": "*",
                    "Condition": {
                        "StringEquals": {
                            "cloudwatch:namespace": "LabelValidation"
                        }
                    }
                }
            ]
        })
    ),
    tags={"Name": f"api-{ROUTE_NAME}-lambda-policy", "Environment": pulumi.get_stack()}
)

# Attach policies
lambda_role_policy_attachment = aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_policy_attachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn
)

# Basic execution role for CloudWatch Logs
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)

# Lambda Insights for enhanced monitoring (optional but recommended)
if is_production:
    aws.iam.RolePolicyAttachment(
        f"api_{ROUTE_NAME}_lambda_insights",
        role=lambda_role.name,
        policy_arn="arn:aws:iam::aws:policy/CloudWatchLambdaInsightsExecutionRolePolicy"
    )

# Optimized Lambda function
label_validation_count_lambda = aws.lambda_.Function(
    f"api_{ROUTE_NAME}_GET_lambda",
    runtime="python3.12",  # Latest runtime for best performance
    architectures=["arm64"],  # ARM for better price/performance
    role=lambda_role.arn,
    code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
    handler="index.handler",
    layers=[
        dynamo_layer.arn,
        # Lambda Insights layer for production monitoring
        "arn:aws:lambda:us-east-1:580247275435:layer:LambdaInsightsExtension-Arm64:52"
        if is_production else None
    ] if is_production else [dynamo_layer.arn],
    
    # Optimized configuration
    memory_size=MEMORY_SIZE,
    timeout=TIMEOUT,
    reserved_concurrent_executions=RESERVED_CONCURRENT_EXECUTIONS,
    
    # Environment variables
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
            "LOG_LEVEL": "INFO" if is_production else "DEBUG",
            # Python optimization flags
            "PYTHONOPTIMIZE": "1",  # Remove assert statements and __debug__ code
            "PYTHONHASHSEED": "0",  # Consistent hash seeds for better caching
        }
    },
    
    # Enable active tracing for X-Ray
    tracing_config={"mode": "Active"} if is_production else {"mode": "PassThrough"},
    
    # Dead letter queue for failed invocations
    dead_letter_config={
        "target_arn": dlq.arn
    } if is_production else None,
    
    tags={
        "Name": f"api-{ROUTE_NAME}-lambda",
        "Environment": pulumi.get_stack(),
        "Route": ROUTE_NAME,
        "Type": "API"
    }
)

# Provisioned concurrency for consistent performance (production only)
if is_production and PROVISIONED_CONCURRENT_EXECUTIONS > 0:
    provisioned_config = aws.lambda_.ProvisionedConcurrencyConfig(
        f"api_{ROUTE_NAME}_provisioned_concurrency",
        function_name=label_validation_count_lambda.name,
        provisioned_concurrent_executions=PROVISIONED_CONCURRENT_EXECUTIONS,
        qualifier=label_validation_count_lambda.version
    )

# CloudWatch log group with retention
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    name=f"/aws/lambda/{label_validation_count_lambda.name}",
    retention_in_days=7 if is_production else 3,  # Shorter retention to save costs
    tags={
        "Name": f"api-{ROUTE_NAME}-logs",
        "Environment": pulumi.get_stack()
    }
)

# CloudWatch alarms for production monitoring
if is_production:
    # Error rate alarm
    error_alarm = aws.cloudwatch.MetricAlarm(
        f"api_{ROUTE_NAME}_error_alarm",
        name=f"{ROUTE_NAME}-error-rate",
        comparison_operator="GreaterThanThreshold",
        evaluation_periods=2,
        metric_name="Errors",
        namespace="AWS/Lambda",
        period=300,
        statistic="Sum",
        threshold=10,
        alarm_description="Alert when Lambda error rate is high",
        dimensions={"FunctionName": label_validation_count_lambda.name}
    )
    
    # Duration alarm
    duration_alarm = aws.cloudwatch.MetricAlarm(
        f"api_{ROUTE_NAME}_duration_alarm",
        name=f"{ROUTE_NAME}-duration",
        comparison_operator="GreaterThanThreshold",
        evaluation_periods=2,
        metric_name="Duration",
        namespace="AWS/Lambda",
        period=300,
        statistic="Average",
        threshold=5000,  # 5 seconds
        alarm_description="Alert when Lambda duration is high",
        dimensions={"FunctionName": label_validation_count_lambda.name}
    )
    
    # Concurrent executions alarm
    concurrent_alarm = aws.cloudwatch.MetricAlarm(
        f"api_{ROUTE_NAME}_concurrent_alarm",
        name=f"{ROUTE_NAME}-concurrent-executions",
        comparison_operator="GreaterThanThreshold",
        evaluation_periods=1,
        metric_name="ConcurrentExecutions",
        namespace="AWS/Lambda",
        period=60,
        statistic="Maximum",
        threshold=80,  # 80% of reserved capacity
        alarm_description="Alert when concurrent executions are high",
        dimensions={"FunctionName": label_validation_count_lambda.name}
    )

# Dead letter queue for production
if is_production:
    dlq = aws.sqs.Queue(
        f"api_{ROUTE_NAME}_dlq",
        message_retention_seconds=1209600,  # 14 days
        tags={
            "Name": f"api-{ROUTE_NAME}-dlq",
            "Environment": pulumi.get_stack()
        }
    )

# Export Lambda function details
pulumi.export(f"{ROUTE_NAME}_lambda_arn", label_validation_count_lambda.arn)
pulumi.export(f"{ROUTE_NAME}_lambda_name", label_validation_count_lambda.name)
if is_production:
    pulumi.export(f"{ROUTE_NAME}_memory_size", MEMORY_SIZE)
    pulumi.export(f"{ROUTE_NAME}_provisioned_concurrency", PROVISIONED_CONCURRENT_EXECUTIONS)
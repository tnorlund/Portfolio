import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table

region = aws.config.region 
caller_identity = aws.get_caller_identity()
account_id = caller_identity.account_id
pulumi_stack_name = pulumi.get_stack()
ecr_image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/cluster-ocr:{pulumi_stack_name}"

# 1. Create an IAM Role for the Lambda function
lambda_role = aws.iam.Role(
    "lambda-execution-role",
    assume_role_policy=pulumi.Output.json_dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Effect": "Allow",
            }
        ],
    }),
)

# Attach the AWSLambdaBasicExecutionRole policy
aws.iam.RolePolicyAttachment(
    "lambda-basic-execution-policy",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# 2. Create the Lambda function
lambda_function = aws.lambda_.Function(
    "cluster-lambda",
    package_type="Image",
    image_uri=ecr_image_uri,
    role=lambda_role.arn,
    memory_size=1024,
    timeout=60 * 2,  # 2 minutes
    environment={
        "variables": {
            "DYNAMO_DB_TABLE": dynamodb_table.name,  # Add environment variables if needed
        }
    },
)

# 3. Export the Lambda function name and ARN
pulumi.export("lambda_function_name", lambda_function.name)
pulumi.export("lambda_function_arn", lambda_function.arn)
import json
import pulumi
import pulumi_aws as aws

# Import the Lambda functions from their respective modules
from lambda_functions.receipt_processor.infra import (
    list_receipts_lambda,
    process_receipt_lambda,
)

# Create IAM role for Step Functions
step_function_role = aws.iam.Role(
    "receipt_processor_step_function_role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Effect": "Allow",
                    "Sid": "",
                }
            ],
        }
    ),
)

# Create policy to allow Step Functions to invoke Lambda
lambda_invoke_policy = aws.iam.Policy(
    "step_function_lambda_invoke_policy",
    description="IAM policy for Step Functions to invoke Lambda",
    policy=pulumi.Output.all(
        list_receipts_lambda.arn, process_receipt_lambda.arn
    ).apply(
        lambda arns: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["lambda:InvokeFunction"],
                        "Resource": arns,
                    }
                ],
            }
        )
    ),
)

# Attach Lambda invoke policy to Step Functions role
lambda_invoke_policy_attachment = aws.iam.RolePolicyAttachment(
    "step_function_lambda_invoke_policy_attachment",
    role=step_function_role.name,
    policy_arn=lambda_invoke_policy.arn,
)

# Create the Step Function state machine
step_function_definition = pulumi.Output.all(
    list_receipts_lambda.arn, process_receipt_lambda.arn
).apply(
    lambda arns: json.dumps(
        {
            "Comment": "A state machine that processes receipts in parallel",
            "StartAt": "ListReceipts",
            "States": {
                "ListReceipts": {
                    "Type": "Task",
                    "Resource": arns[0],
                    "Next": "ProcessReceipts",
                },
                "ProcessReceipts": {
                    "Type": "Map",
                    "InputPath": "$.receipts",
                    "ItemsPath": "$",
                    "MaxConcurrency": 10,  # Process up to 10 receipts in parallel
                    "Iterator": {
                        "StartAt": "ProcessReceipt",
                        "States": {
                            "ProcessReceipt": {
                                "Type": "Task",
                                "Resource": arns[1],
                                "End": True,
                            }
                        },
                    },
                    "End": True,
                },
            },
        }
    )
)

receipt_processor_step_function = aws.sfn.StateMachine(
    "receipt_processor_step_function",
    role_arn=step_function_role.arn,
    definition=step_function_definition,
)

# Export the Step Function ARN
pulumi.export(
    "receipt_processor_step_function_arn", receipt_processor_step_function.arn
)

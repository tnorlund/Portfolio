"""
Pulumi infrastructure for Trigger Re-OCR Lambda.

This component creates a container-based Lambda that triggers regional
re-OCR by creating an OCRJob in DynamoDB and sending a message to the
OCR job SQS queue.

The Lambda can be invoked directly with:
{
    "image_id": "uuid-string",
    "receipt_id": 1,
    "reocr_region": {"x": 0.65, "y": 0.0, "width": 0.35, "height": 1.0},
    "reocr_reason": "manual_trigger"
}

Architecture:
- Lightweight container Lambda (only receipt_dynamo)
- DynamoDB for receipt lookup and OCR job creation
- SQS for job queue submission
"""

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment

try:
    from codebuild_docker_image import CodeBuildDockerImage
except ImportError as e:
    raise ImportError(
        "CodeBuildDockerImage is required for TriggerReOCRLambda. "
        "Ensure codebuild_docker_image.py is in the Pulumi project root."
    ) from e


class TriggerReOCRLambda(ComponentResource):
    """
    Container Lambda for triggering regional re-OCR.

    This Lambda:
    1. Receives (image_id, receipt_id, reocr_region)
    2. Looks up receipt S3 info from DynamoDB
    3. Creates an OCRJob entity in DynamoDB
    4. Sends a message to the OCR job SQS queue

    Exports:
    - lambda_function: The Lambda function resource
    - lambda_arn: ARN for invoking the Lambda
    - lambda_role_name: IAM role name
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        ocr_job_queue_url: pulumi.Input[str],
        ocr_job_queue_arn: pulumi.Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # ============================================================
        # IAM Role
        # ============================================================
        lambda_role = Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution
        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ECR permissions for container Lambda
        RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # DynamoDB access policy
        dynamodb_policy = RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=lambda_role.id,
            policy=Output.from_input(dynamodb_table_arn).apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:Query",
                                ],
                                "Resource": [
                                    arn,
                                    f"{arn}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # SQS send policy
        sqs_policy = RolePolicy(
            f"{name}-lambda-sqs-policy",
            role=lambda_role.id,
            policy=Output.from_input(ocr_job_queue_arn).apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["sqs:SendMessage"],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ============================================================
        # Container Lambda
        # ============================================================
        lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 60,  # Lightweight — just DynamoDB + SQS
            "memory_size": 512,
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "OCR_JOB_QUEUE_URL": ocr_job_queue_url,
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/trigger_reocr_lambda/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
            ],
            lambda_function_name=f"{name}-{stack}-trigger-reocr",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy, sqs_policy]
            ),
        )

        self.lambda_function = docker_image.lambda_function
        self.lambda_arn = self.lambda_function.arn
        self.lambda_role_name = lambda_role.name

        # Register outputs
        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
                "lambda_role_name": self.lambda_role_name,
            }
        )


def create_trigger_reocr_lambda(
    dynamodb_table_name: pulumi.Input[str],
    dynamodb_table_arn: pulumi.Input[str],
    ocr_job_queue_url: pulumi.Input[str],
    ocr_job_queue_arn: pulumi.Input[str],
) -> TriggerReOCRLambda:
    """
    Factory function to create the Trigger Re-OCR Lambda.

    Args:
        dynamodb_table_name: Name of the DynamoDB table
        dynamodb_table_arn: ARN of the DynamoDB table
        ocr_job_queue_url: URL of the OCR job SQS queue
        ocr_job_queue_arn: ARN of the OCR job SQS queue

    Returns:
        TriggerReOCRLambda component with lambda_arn output
    """
    return TriggerReOCRLambda(
        "trigger-reocr",
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        ocr_job_queue_url=ocr_job_queue_url,
        ocr_job_queue_arn=ocr_job_queue_arn,
    )

"""Pulumi component that runs per-epoch checkpoint evaluation on SageMaker.

This reuses the *existing* LayoutLM training container image and execution
role — no new container is built. A small trigger Lambda launches an ephemeral
SageMaker **Processing** job that runs ``layoutlm-cli eval-checkpoints`` against
a finished training run, scoring every retained checkpoint on the run's frozen
validation set and writing ``epochs.json`` (plus per-epoch showcase receipts)
back into the training bucket under ``epoch-eval/<job>/``.

Processing jobs are ephemeral: they spin up, run, write to S3, and terminate.
Nothing is left running between evaluations.

The optional EventBridge rule (off by default) re-evaluates a run automatically
when its SageMaker training job completes — mirroring the auto-CoreML-export
pattern. It is gated so training completions don't silently incur GPU cost.
"""

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Input, Output, ResourceOptions

stack = pulumi.get_stack()


class EpochEvalInfra(ComponentResource):
    """Trigger + IAM for SageMaker Processing-based checkpoint evaluation."""

    def __init__(
        self,
        name: str,
        *,
        ecr_repo_url: Input[str],
        sagemaker_role_arn: Input[str],
        output_bucket: Input[str],
        dynamodb_table_name: Input[str],
        region: str,
        account_id: str,
        # GPU processing-job quota (ml.g4dn.xlarge) was granted, so default to
        # GPU — the full checkpoint sweep runs in ~10 min vs ~hours on CPU.
        # Override per invocation via the trigger event's "instance_type"
        # (e.g. ml.c5.9xlarge if GPU quota is ever exhausted).
        instance_type: str = "ml.g4dn.xlarge",
        enable_auto_trigger: bool = False,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:sagemaker-epoch-eval:{name}", name, None, opts
        )

        # IAM role for the trigger Lambda.
        self.lambda_role = aws.iam.Role(
            f"{name}-trigger-role",
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
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            ],
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicy(
            f"{name}-trigger-policy",
            role=self.lambda_role.id,
            policy=Output.from_input(sagemaker_role_arn).apply(
                lambda role_arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sagemaker:CreateProcessingJob",
                                    "sagemaker:DescribeProcessingJob",
                                    "sagemaker:ListProcessingJobs",
                                    "sagemaker:StopProcessingJob",
                                    "sagemaker:AddTags",
                                ],
                                "Resource": (
                                    f"arn:aws:sagemaker:{region}:"
                                    f"{account_id}:processing-job/*"
                                ),
                            },
                            # Hand the SageMaker execution role to the job.
                            {
                                "Effect": "Allow",
                                "Action": "iam:PassRole",
                                "Resource": role_arn,
                                "Condition": {
                                    "StringEquals": {
                                        "iam:PassedToService": (
                                            "sagemaker.amazonaws.com"
                                        )
                                    }
                                },
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        self.trigger_lambda = aws.lambda_.Function(
            f"{name}-trigger",
            runtime="python3.12",
            handler="index.handler",
            role=self.lambda_role.arn,
            timeout=30,
            memory_size=256,
            code=pulumi.AssetArchive(
                {"index.py": pulumi.StringAsset(_TRIGGER_CODE)}
            ),
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables=Output.all(
                    ecr_repo_url,
                    output_bucket,
                    sagemaker_role_arn,
                    dynamodb_table_name,
                ).apply(
                    lambda args: {
                        "ECR_IMAGE_URI": f"{args[0]}:latest",
                        "OUTPUT_BUCKET": args[1],
                        "SAGEMAKER_ROLE_ARN": args[2],
                        "DYNAMO_TABLE_NAME": args[3],
                        "DEFAULT_INSTANCE_TYPE": instance_type,
                    }
                ),
            ),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-trigger-logs",
            name=self.trigger_lambda.name.apply(
                lambda fn: f"/aws/lambda/{fn}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # Optional: auto-evaluate when a training job completes.
        if enable_auto_trigger:
            rule = aws.cloudwatch.EventRule(
                f"{name}-on-training-complete",
                description=(
                    "Run per-epoch evaluation when a LayoutLM training job "
                    "completes"
                ),
                event_pattern=json.dumps(
                    {
                        "source": ["aws.sagemaker"],
                        "detail-type": [
                            "SageMaker Training Job State Change"
                        ],
                        "detail": {
                            "TrainingJobStatus": ["Completed"],
                            "TrainingJobName": [{"prefix": "layoutlm-"}],
                        },
                    }
                ),
                tags={"Component": name},
                opts=ResourceOptions(parent=self),
            )
            aws.cloudwatch.EventTarget(
                f"{name}-on-training-complete-target",
                rule=rule.name,
                arn=self.trigger_lambda.arn,
                opts=ResourceOptions(parent=self),
            )
            aws.lambda_.Permission(
                f"{name}-eventbridge-permission",
                action="lambda:InvokeFunction",
                function=self.trigger_lambda.name,
                principal="events.amazonaws.com",
                source_arn=rule.arn,
                opts=ResourceOptions(parent=self),
            )

        self.register_outputs(
            {"trigger_lambda_arn": self.trigger_lambda.arn}
        )


# Inline Lambda that launches the SageMaker Processing job. Kept inline (like
# the start-training Lambda) so it ships with the component.
_TRIGGER_CODE = '''
import json
import os
import re
from datetime import datetime

import boto3

sagemaker = boto3.client("sagemaker")


def _resolve_job_name(event):
    """Accept a direct invoke ({"job_name": ...}) or a training-complete event."""
    if event.get("job_name"):
        return event["job_name"]
    detail = event.get("detail") or {}
    return detail.get("TrainingJobName")


def handler(event, context):
    event = event or {}
    job_name = _resolve_job_name(event)
    if not job_name:
        raise ValueError("job_name (or detail.TrainingJobName) is required")

    output_bucket = os.environ["OUTPUT_BUCKET"]
    table = os.environ["DYNAMO_TABLE_NAME"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    instance_type = event.get(
        "instance_type", os.environ.get("DEFAULT_INSTANCE_TYPE", "ml.g4dn.xlarge")
    )

    # Processing job names: <=63 chars, [a-zA-Z0-9-]. Sanitize + timestamp.
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-zA-Z0-9-]", "-", job_name)[:40].strip("-")
    proc_name = f"epoch-eval-{slug}-{ts}"[:63]

    output_s3 = f"s3://{output_bucket}/epoch-eval/{job_name}/"
    args = [
        "--job-name", job_name,
        "--dynamo-table", table,
        "--region", region,
        "--output-dir", "/opt/ml/processing/output",
        "--output-s3-uri", output_s3,
    ]
    if event.get("max_receipts") is not None:
        args += ["--max-receipts", str(event["max_receipts"])]
    if event.get("num_showcase") is not None:
        args += ["--num-showcase", str(event["num_showcase"])]
    # Older runs (pre val_receipt_keys persistence) drift, so default to
    # tolerating a hash mismatch unless the caller explicitly forbids it.
    if event.get("allow_hash_mismatch", True):
        args += ["--allow-hash-mismatch"]

    config = {
        "ProcessingJobName": proc_name,
        "RoleArn": os.environ["SAGEMAKER_ROLE_ARN"],
        "AppSpecification": {
            "ImageUri": os.environ["ECR_IMAGE_URI"],
            "ContainerEntrypoint": ["layoutlm-cli", "eval-checkpoints"],
            "ContainerArguments": args,
        },
        "ProcessingResources": {
            "ClusterConfig": {
                "InstanceCount": 1,
                "InstanceType": instance_type,
                "VolumeSizeInGB": int(event.get("volume_size", 50)),
            }
        },
        "StoppingCondition": {
            "MaxRuntimeInSeconds": int(event.get("max_runtime_seconds", 10800))
        },
        "Environment": {
            "DYNAMO_TABLE_NAME": table,
            "AWS_REGION": region,
        },
        "Tags": [
            {"Key": "purpose", "Value": "layoutlm-epoch-eval"},
            {"Key": "training-job", "Value": job_name[:256]},
        ],
    }

    resp = sagemaker.create_processing_job(**config)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "processing_job_name": proc_name,
                "processing_job_arn": resp["ProcessingJobArn"],
                "training_job": job_name,
                "output_s3": output_s3,
                "instance_type": instance_type,
            }
        ),
    }
'''

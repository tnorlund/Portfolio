"""SageMaker Training Infrastructure for LayoutLM models.

This component provides managed ML training infrastructure using AWS SageMaker,
replacing the custom EC2-based training setup. Benefits include:
- No instance management (SageMaker handles provisioning/teardown)
- Built-in spot instance support with automatic checkpointing
- Managed container execution
- Integrated CloudWatch logging
- Pay only for training time
"""

import json
import hashlib
import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output


class SageMakerTrainingInfra(ComponentResource):
    """SageMaker-based training infrastructure for LayoutLM models.

    Creates:
    - ECR repository for training container image
    - IAM role for SageMaker execution (DynamoDB, S3, ECR access)
    - S3 bucket for model outputs and dataset caching
    - CodeBuild project to build/push Docker image on changes
    - Lambda function to start training jobs programmatically
    """

    def __init__(
        self,
        name: str,
        dynamodb_table_name: Output[str],
        opts: ResourceOptions | None = None,
    ):
        super().__init__("custom:ml:SageMakerTrainingInfra", name, None, opts)

        stack = pulumi.get_stack()
        region = aws.get_region().name
        account_id = aws.get_caller_identity().account_id

        # ---------------------------------------------------------------------
        # ECR Repository for Training Image
        # ---------------------------------------------------------------------
        self.ecr_repo = aws.ecr.Repository(
            f"{name}-repo",
            name=f"layoutlm-trainer-{stack}",
            image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            image_tag_mutability="MUTABLE",  # Allow :latest updates
            tags={"Component": name, "Purpose": "layoutlm-training"},
            opts=ResourceOptions(parent=self),
        )

        # Lifecycle policy to keep only recent images
        aws.ecr.LifecyclePolicy(
            f"{name}-lifecycle",
            repository=self.ecr_repo.name,
            policy=json.dumps({
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": "Keep last 5 images",
                        "selection": {
                            "tagStatus": "any",
                            "countType": "imageCountMoreThan",
                            "countNumber": 5,
                        },
                        "action": {"type": "expire"},
                    }
                ]
            }),
            opts=ResourceOptions(parent=self.ecr_repo),
        )

        # ---------------------------------------------------------------------
        # S3 Bucket for Model Outputs
        # ---------------------------------------------------------------------
        self.output_bucket = aws.s3.Bucket(
            f"{name}-output",
            bucket=f"layoutlm-training-{stack}-{account_id[:8]}",
            force_destroy=True,  # Allow deletion even with objects
            tags={"Component": name, "Purpose": "training-outputs"},
            opts=ResourceOptions(parent=self),
        )

        # Block public access
        aws.s3.BucketPublicAccessBlock(
            f"{name}-output-pab",
            bucket=self.output_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self.output_bucket),
        )

        # ---------------------------------------------------------------------
        # IAM Role for SageMaker Execution
        # ---------------------------------------------------------------------
        self.sagemaker_role = aws.iam.Role(
            f"{name}-sagemaker-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "sagemaker.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # SageMaker execution policy
        sagemaker_policy = aws.iam.RolePolicy(
            f"{name}-sagemaker-policy",
            role=self.sagemaker_role.id,
            policy=Output.all(
                self.output_bucket.arn,
                self.ecr_repo.arn,
                dynamodb_table_name,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    # S3 access for model outputs and data
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            args[0],
                            f"{args[0]}/*",
                        ],
                    },
                    # ECR access to pull training image
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecr:GetDownloadUrlForLayer",
                            "ecr:BatchGetImage",
                            "ecr:BatchCheckLayerAvailability",
                        ],
                        "Resource": args[1],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ecr:GetAuthorizationToken",
                        "Resource": "*",
                    },
                    # DynamoDB access for training data
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                            "dynamodb:BatchGetItem",
                            "dynamodb:PutItem",  # For job logging
                            "dynamodb:UpdateItem",
                        ],
                        "Resource": [
                            f"arn:aws:dynamodb:{region}:{account_id}:table/{args[2]}",
                            f"arn:aws:dynamodb:{region}:{account_id}:table/{args[2]}/index/*",
                        ],
                    },
                    # CloudWatch Logs
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                            "logs:DescribeLogStreams",
                        ],
                        "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/sagemaker/*",
                    },
                    # CloudWatch Metrics
                    {
                        "Effect": "Allow",
                        "Action": "cloudwatch:PutMetricData",
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "cloudwatch:namespace": "/aws/sagemaker/TrainingJobs",
                            }
                        },
                    },
                ],
            })),
            opts=ResourceOptions(parent=self.sagemaker_role),
        )

        # ---------------------------------------------------------------------
        # CodeBuild for Docker Image Building
        # ---------------------------------------------------------------------
        self.codebuild_role = aws.iam.Role(
            f"{name}-codebuild-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codebuild.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        codebuild_policy = aws.iam.RolePolicy(
            f"{name}-codebuild-policy",
            role=self.codebuild_role.id,
            policy=Output.all(self.ecr_repo.arn).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    # ECR push access
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecr:GetDownloadUrlForLayer",
                            "ecr:BatchGetImage",
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:PutImage",
                            "ecr:InitiateLayerUpload",
                            "ecr:UploadLayerPart",
                            "ecr:CompleteLayerUpload",
                        ],
                        "Resource": args[0],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ecr:GetAuthorizationToken",
                        "Resource": "*",
                    },
                    # CloudWatch Logs
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                        ],
                        "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/codebuild/*",
                    },
                    # S3 for source (if needed)
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:GetObjectVersion",
                        ],
                        "Resource": "*",
                    },
                ],
            })),
            opts=ResourceOptions(parent=self.codebuild_role),
        )

        # CodeBuild project
        self.codebuild_project = aws.codebuild.Project(
            f"{name}-image-builder",
            description="Builds LayoutLM training Docker image",
            build_timeout=30,  # 30 minutes
            service_role=self.codebuild_role.arn,
            environment=aws.codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_MEDIUM",
                image="aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                type="LINUX_CONTAINER",
                privileged_mode=True,  # Required for Docker builds
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AWS_ACCOUNT_ID",
                        value=account_id,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AWS_REGION",
                        value=region,
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="ECR_REPO",
                        value=self.ecr_repo.repository_url,
                    ),
                ],
            ),
            source=aws.codebuild.ProjectSourceArgs(
                type="GITHUB",
                location="https://github.com/tnorlund/Portfolio.git",
                git_clone_depth=1,
                buildspec="infra/sagemaker_training/buildspec.yml",
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # ---------------------------------------------------------------------
        # Lambda to Start Training Jobs
        # ---------------------------------------------------------------------
        self.lambda_role = aws.iam.Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            ],
            opts=ResourceOptions(parent=self),
        )

        lambda_policy = aws.iam.RolePolicy(
            f"{name}-lambda-policy",
            role=self.lambda_role.id,
            policy=Output.all(
                self.sagemaker_role.arn,
                self.output_bucket.arn,
                self.ecr_repo.arn,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    # SageMaker training job management
                    {
                        "Effect": "Allow",
                        "Action": [
                            "sagemaker:CreateTrainingJob",
                            "sagemaker:DescribeTrainingJob",
                            "sagemaker:StopTrainingJob",
                            "sagemaker:ListTrainingJobs",
                        ],
                        "Resource": f"arn:aws:sagemaker:{region}:{account_id}:training-job/*",
                    },
                    # Pass role to SageMaker
                    {
                        "Effect": "Allow",
                        "Action": "iam:PassRole",
                        "Resource": args[0],
                        "Condition": {
                            "StringEquals": {
                                "iam:PassedToService": "sagemaker.amazonaws.com",
                            }
                        },
                    },
                ],
            })),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Lambda function code (inline for simplicity)
        lambda_code = self._generate_lambda_code(
            region=region,
            account_id=account_id,
        )

        self.start_training_lambda = aws.lambda_.Function(
            f"{name}-start-training",
            runtime="python3.11",
            handler="index.handler",
            role=self.lambda_role.arn,
            timeout=30,
            memory_size=256,
            code=pulumi.AssetArchive({
                "index.py": pulumi.StringAsset(lambda_code),
            }),
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables=Output.all(
                    self.ecr_repo.repository_url,
                    self.output_bucket.bucket,
                    self.sagemaker_role.arn,
                    dynamodb_table_name,
                ).apply(lambda args: {
                    "ECR_IMAGE_URI": f"{args[0]}:latest",
                    "OUTPUT_BUCKET": args[1],
                    "SAGEMAKER_ROLE_ARN": args[2],
                    "DYNAMO_TABLE_NAME": args[3],
                }),
            ),
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # ---------------------------------------------------------------------
        # Outputs
        # ---------------------------------------------------------------------
        self.register_outputs({
            "ecr_repo_url": self.ecr_repo.repository_url,
            "output_bucket": self.output_bucket.bucket,
            "sagemaker_role_arn": self.sagemaker_role.arn,
            "start_training_lambda_arn": self.start_training_lambda.arn,
            "codebuild_project_name": self.codebuild_project.name,
        })

    def _generate_lambda_code(self, region: str, account_id: str) -> str:
        """Generate the Lambda function code for starting training jobs."""
        return '''
import json
import os
import boto3
from datetime import datetime

sagemaker = boto3.client("sagemaker")

def handler(event, context):
    """Start a SageMaker training job.

    Event parameters:
    - job_name: Unique name for the training job (required)
    - instance_type: SageMaker instance type (default: ml.g5.xlarge)
    - instance_count: Number of instances (default: 1)
    - use_spot: Whether to use spot instances (default: True)
    - max_runtime_hours: Maximum runtime in hours (default: 24)
    - hyperparameters: Dict of training hyperparameters
    """
    # Extract parameters
    job_name = event.get("job_name")
    if not job_name:
        job_name = f"layoutlm-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    instance_type = event.get("instance_type", "ml.g5.xlarge")
    instance_count = event.get("instance_count", 1)
    use_spot = event.get("use_spot", True)
    max_runtime_hours = event.get("max_runtime_hours", 24)

    # Default hyperparameters
    hyperparameters = {
        "epochs": "10",
        "batch_size": "8",
        "learning_rate": "5e-5",
        "warmup_ratio": "0.1",
        "early_stopping_patience": "2",
        **{k: str(v) for k, v in event.get("hyperparameters", {}).items()},
    }

    # Add required parameters
    hyperparameters["dynamo_table"] = os.environ["DYNAMO_TABLE_NAME"]
    hyperparameters["job_name"] = job_name
    hyperparameters["output_s3_path"] = os.environ["OUTPUT_BUCKET"]

    # Build training job config
    training_job_config = {
        "TrainingJobName": job_name,
        "AlgorithmSpecification": {
            "TrainingImage": os.environ["ECR_IMAGE_URI"],
            "TrainingInputMode": "File",
        },
        "RoleArn": os.environ["SAGEMAKER_ROLE_ARN"],
        "HyperParameters": hyperparameters,
        "ResourceConfig": {
            "InstanceType": instance_type,
            "InstanceCount": instance_count,
            "VolumeSizeInGB": 100,
        },
        "StoppingCondition": {
            "MaxRuntimeInSeconds": max_runtime_hours * 3600,
        },
        "OutputDataConfig": {
            "S3OutputPath": f"s3://{os.environ['OUTPUT_BUCKET']}/runs",
        },
    }

    # Add spot configuration if enabled
    if use_spot:
        training_job_config["EnableManagedSpotTraining"] = True
        training_job_config["StoppingCondition"]["MaxWaitTimeInSeconds"] = (
            max_runtime_hours * 3600 + 3600  # +1 hour for spot wait
        )
        training_job_config["CheckpointConfig"] = {
            "S3Uri": f"s3://{os.environ['OUTPUT_BUCKET']}/checkpoints/{job_name}",
        }

    # Create the training job
    response = sagemaker.create_training_job(**training_job_config)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "job_name": job_name,
            "job_arn": response["TrainingJobArn"],
            "instance_type": instance_type,
            "use_spot": use_spot,
        }),
    }
'''

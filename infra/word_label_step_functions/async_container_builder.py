"""
Async container builder using CodeBuild and CodePipeline.

This approach builds containers in the background using AWS CodeBuild,
similar to the fast_lambda_layer.py pattern but for Docker containers.
"""

import json
from pathlib import Path
from typing import Dict, Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import codebuild, codepipeline, ecr, iam, s3
from pulumi_command import local


class AsyncContainerBuilder(ComponentResource):
    """
    Build Docker containers asynchronously using CodeBuild.
    
    This allows `pulumi up` to complete quickly while containers build
    in the background on AWS infrastructure.
    """
    
    def __init__(
        self,
        name: str,
        dockerfile_path: str,
        context_path: str,
        ecr_repository: ecr.Repository,
        build_args: Optional[Dict[str, str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            "custom:docker:AsyncContainerBuilder",
            name,
            None,
            opts,
        )
        
        self.name = name
        self.dockerfile_path = dockerfile_path
        self.context_path = context_path
        self.ecr_repository = ecr_repository
        self.build_args = build_args or {}
        
        # Create S3 bucket for source artifacts
        self.artifact_bucket = s3.Bucket(
            f"{name}-artifacts",
            force_destroy=True,
            opts=ResourceOptions(parent=self),
        )
        
        # Create CodeBuild project
        self._create_codebuild_project()
        
        # Create CodePipeline
        self._create_pipeline()
        
        # Upload source and trigger pipeline
        self._setup_source_upload()
        
    def _create_codebuild_project(self):
        """Create CodeBuild project for building containers."""
        
        # Create IAM role for CodeBuild
        self.codebuild_role = iam.Role(
            f"{self.name}-codebuild-role",
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
        
        # Attach policies for ECR, S3, and CloudWatch
        iam.RolePolicy(
            f"{self.name}-codebuild-policy",
            role=self.codebuild_role.id,
            policy=Output.all(
                self.artifact_bucket.arn,
                self.ecr_repository.arn,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                        ],
                        "Resource": "arn:aws:logs:*:*:*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:GetObjectVersion",
                            "s3:PutObject",
                        ],
                        "Resource": f"{args[0]}/*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecr:GetAuthorizationToken",
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:GetDownloadUrlForLayer",
                            "ecr:BatchGetImage",
                            "ecr:PutImage",
                            "ecr:InitiateLayerUpload",
                            "ecr:UploadLayerPart",
                            "ecr:CompleteLayerUpload",
                        ],
                        "Resource": "*",
                    },
                ],
            })),
            opts=ResourceOptions(parent=self),
        )
        
        # Create buildspec
        buildspec = {
            "version": 0.2,
            "phases": {
                "pre_build": {
                    "commands": [
                        "echo Logging in to Amazon ECR...",
                        "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
                        "REPOSITORY_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME",
                        "IMAGE_TAG=${CODEBUILD_RESOLVED_SOURCE_VERSION:-latest}",
                    ],
                },
                "build": {
                    "commands": [
                        "echo Build started on `date`",
                        f"docker build -t $REPOSITORY_URI:latest -t $REPOSITORY_URI:$IMAGE_TAG -f {self.dockerfile_path} {' '.join(f'--build-arg {k}={v}' for k, v in self.build_args.items())} .",
                        "echo Build completed on `date`",
                    ],
                },
                "post_build": {
                    "commands": [
                        "echo Pushing the Docker image...",
                        "docker push $REPOSITORY_URI:latest",
                        "docker push $REPOSITORY_URI:$IMAGE_TAG",
                        'echo "[{\\"name\\":\\"${IMAGE_REPO_NAME}\\",\\"imageUri\\":\\"${REPOSITORY_URI}:${IMAGE_TAG}\\"}]" > imagedefinitions.json',
                    ],
                },
            },
            "artifacts": {
                "files": ["imagedefinitions.json"],
            },
        }
        
        # Create CodeBuild project
        self.codebuild_project = codebuild.Project(
            f"{self.name}-build",
            service_role=self.codebuild_role.arn,
            artifacts=codebuild.ProjectArtifactsArgs(
                type="CODEPIPELINE",
            ),
            environment=codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/standard:7.0",
                type="ARM_CONTAINER",  # Use ARM for consistency
                privileged_mode=True,  # Required for Docker
                environment_variables=[
                    codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AWS_DEFAULT_REGION",
                        value=aws.get_region().name,
                    ),
                    codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="AWS_ACCOUNT_ID",
                        value=aws.get_caller_identity().account_id,
                    ),
                    codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="IMAGE_REPO_NAME",
                        value=self.ecr_repository.name,
                    ),
                ],
            ),
            source=codebuild.ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(buildspec),
            ),
            cache=codebuild.ProjectCacheArgs(
                type="LOCAL",
                modes=["LOCAL_DOCKER_LAYER_CACHE", "LOCAL_SOURCE_CACHE"],
            ),
            opts=ResourceOptions(parent=self),
        )
    
    def _create_pipeline(self):
        """Create CodePipeline for automated builds."""
        
        # Create IAM role for CodePipeline
        self.pipeline_role = iam.Role(
            f"{self.name}-pipeline-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codepipeline.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )
        
        # Attach policies
        iam.RolePolicy(
            f"{self.name}-pipeline-policy",
            role=self.pipeline_role.id,
            policy=Output.all(
                self.artifact_bucket.arn,
                self.codebuild_project.arn,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:GetObjectVersion",
                            "s3:PutObject",
                            "s3:GetBucketLocation",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            args[0],
                            f"{args[0]}/*",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "codebuild:BatchGetBuilds",
                            "codebuild:StartBuild",
                        ],
                        "Resource": args[1],
                    },
                ],
            })),
            opts=ResourceOptions(parent=self),
        )
        
        # Create pipeline
        self.pipeline = codepipeline.Pipeline(
            f"{self.name}-pipeline",
            role_arn=self.pipeline_role.arn,
            artifact_stores=[
                codepipeline.PipelineArtifactStoreArgs(
                    type="S3",
                    location=self.artifact_bucket.bucket,
                ),
            ],
            stages=[
                # Source stage
                codepipeline.PipelineStageArgs(
                    name="Source",
                    actions=[
                        codepipeline.PipelineStageActionArgs(
                            name="Source",
                            category="Source",
                            owner="AWS",
                            provider="S3",
                            version="1",
                            output_artifacts=["source_output"],
                            configuration={
                                "S3Bucket": self.artifact_bucket.bucket,
                                "S3ObjectKey": f"{self.name}/source.zip",
                            },
                        ),
                    ],
                ),
                # Build stage
                codepipeline.PipelineStageArgs(
                    name="Build",
                    actions=[
                        codepipeline.PipelineStageActionArgs(
                            name="Build",
                            category="Build",
                            owner="AWS",
                            provider="CodeBuild",
                            version="1",
                            input_artifacts=["source_output"],
                            output_artifacts=["build_output"],
                            configuration={
                                "ProjectName": self.codebuild_project.name,
                            },
                        ),
                    ],
                ),
            ],
            opts=ResourceOptions(parent=self),
        )
    
    def _setup_source_upload(self):
        """Set up source upload and pipeline triggering."""
        
        # Create a command to zip and upload source
        self.upload_source = local.Command(
            f"{self.name}-upload-source",
            create=self.artifact_bucket.bucket.apply(
                lambda bucket: f"""#!/bin/bash
set -e

echo "📦 Packaging source for {self.name}..."

# Create temp directory
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Copy source files
cp -r {self.context_path}/* "$TEMP_DIR/"

# Create zip file
cd "$TEMP_DIR"
zip -r source.zip . -x "*.git*" "*__pycache__*" "*.pytest_cache*"

# Upload to S3
aws s3 cp source.zip s3://{bucket}/{self.name}/source.zip

# Trigger pipeline
aws codepipeline start-pipeline-execution --name {self.pipeline.name}

echo "✅ Pipeline triggered for {self.name}"
"""
            ),
            opts=ResourceOptions(parent=self),
        )
    
    @property
    def image_uri(self) -> Output[str]:
        """Get the image URI for use in Lambda functions."""
        return Output.concat(
            self.ecr_repository.repository_url,
            ":latest"
        )


# Example usage:
"""
# In chromadb_lambdas.py:
from async_container_builder import AsyncContainerBuilder

# Create async builder for polling Lambda
self.polling_builder = AsyncContainerBuilder(
    "chromadb-poll",
    dockerfile_path="chromadb_polling_lambda/Dockerfile",
    context_path=str(Path(__file__).parent.parent.parent),
    ecr_repository=self.polling_repo,
    build_args={"PYTHON_VERSION": "3.12"},
    opts=ResourceOptions(parent=self),
)

# Use the image URI (build happens asynchronously)
self.polling_lambda = Function(
    f"chromadb-poll-fn-{stack}",
    package_type="Image",
    image_uri=self.polling_builder.image_uri,
    # ... rest of config
)
"""
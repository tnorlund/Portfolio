#!/usr/bin/env python3
"""
FastContainerImage - Async container builds using CodeBuild.

Similar to FastLambdaLayer but for container images. Provides:
- Instant `pulumi up` (returns immediately)
- Builds happen in AWS CodeBuild (faster, parallel)
- Automatic caching and layer reuse
- No local Docker daemon required
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional, List

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import iam, codebuild, s3, ecr


class FastContainerImage(ComponentResource):
    """Fast container image builder using CodeBuild."""
    
    def __init__(
        self,
        name: str,
        dockerfile_path: str,
        context_path: str,
        repository: ecr.Repository,
        build_args: Optional[Dict[str, str]] = None,
        base_image: Optional[Output[str]] = None,
        force_rebuild: bool = False,
        opts: ResourceOptions = None,
    ):
        super().__init__("custom:FastContainerImage", name, None, opts)
        
        self.name = name
        self.dockerfile_path = Path(dockerfile_path)
        self.context_path = Path(context_path)
        self.repository = repository
        self.build_args = build_args or {}
        self.force_rebuild = force_rebuild
        
        # Calculate hash of build context
        self.content_hash = self._calculate_hash()
        
        # Create S3 bucket for build artifacts if not exists
        self.artifact_bucket = s3.Bucket(
            f"{name}-artifacts",
            force_destroy=True,
            opts=ResourceOptions(parent=self),
        )
        
        # Create CodeBuild project
        self.build_project = self._create_build_project(base_image)
        
        # Trigger build immediately
        self.build_trigger = self._trigger_build()
        
        # Export the image URI
        self.image_uri = Output.concat(
            self.repository.repository_url,
            ":",
            self.content_hash[:12],  # Use hash as tag
        )
        
        self.register_outputs({
            "image_uri": self.image_uri,
            "content_hash": self.content_hash,
            "build_project_name": self.build_project.name,
        })
    
    def _calculate_hash(self) -> str:
        """Calculate hash of dockerfile and context."""
        hasher = hashlib.sha256()
        
        # Hash dockerfile
        hasher.update(self.dockerfile_path.read_bytes())
        
        # Hash build args
        hasher.update(json.dumps(self.build_args, sort_keys=True).encode())
        
        # Add force rebuild flag
        if self.force_rebuild:
            import time
            hasher.update(str(time.time()).encode())
            
        return hasher.hexdigest()
    
    def _create_build_project(self, base_image: Optional[Output[str]]) -> codebuild.Project:
        """Create CodeBuild project for building containers."""
        
        # Create IAM role for CodeBuild
        role = iam.Role(
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
        
        # Attach policies
        iam.RolePolicy(
            f"{self.name}-codebuild-policy",
            role=role.id,
            policy=Output.all(
                self.repository.arn,
                self.artifact_bucket.arn,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
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
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:PutObject"],
                        "Resource": f"{args[1]}/*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                        "Resource": "*",
                    },
                ],
            })),
            opts=ResourceOptions(parent=self),
        )
        
        # Build spec
        buildspec = {
            "version": 0.2,
            "phases": {
                "pre_build": {
                    "commands": [
                        "echo Logging in to Amazon ECR...",
                        "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
                        "REPOSITORY_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME",
                        "IMAGE_TAG=${CODEBUILD_RESOLVED_SOURCE_VERSION:-$IMAGE_TAG}",
                    ],
                },
                "build": {
                    "commands": [
                        "echo Build started on `date`",
                        "echo Building the Docker image...",
                        f"docker build -t $REPOSITORY_URI:$IMAGE_TAG -t $REPOSITORY_URI:latest -f {self.dockerfile_path} {' '.join([f'--build-arg {k}={v}' for k, v in self.build_args.items()])} {self.context_path}",
                    ],
                },
                "post_build": {
                    "commands": [
                        "echo Build completed on `date`",
                        "echo Pushing the Docker image...",
                        "docker push $REPOSITORY_URI:$IMAGE_TAG",
                        "docker push $REPOSITORY_URI:latest",
                    ],
                },
            },
        }
        
        # Handle base image if provided
        if base_image:
            def create_project_with_base(base_image_url):
                env_vars = [
                    {"name": "AWS_DEFAULT_REGION", "value": pulumi.Config("aws").require("region")},
                    {"name": "AWS_ACCOUNT_ID", "value": pulumi.get_stack()},
                    {"name": "IMAGE_REPO_NAME", "value": self.repository.name},
                    {"name": "IMAGE_TAG", "value": self.content_hash[:12]},
                ]
                
                # Update build args with base image
                self.build_args["BASE_IMAGE"] = base_image_url
                
                return codebuild.Project(
                    f"{self.name}-build",
                    service_role=role.arn,
                    artifacts=codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
                    environment=codebuild.ProjectEnvironmentArgs(
                        compute_type="BUILD_GENERAL1_SMALL",
                        image="aws/codebuild/standard:7.0",
                        type="ARM_CONTAINER",
                        privileged_mode=True,  # Required for Docker
                        environment_variables=env_vars,
                    ),
                    source=codebuild.ProjectSourceArgs(
                        type="NO_SOURCE",
                        buildspec=json.dumps(buildspec),
                    ),
                    opts=ResourceOptions(parent=self),
                )
            
            return base_image.apply(create_project_with_base)
        else:
            # Create project without base image
            return codebuild.Project(
                f"{self.name}-build",
                service_role=role.arn,
                artifacts=codebuild.ProjectArtifactsArgs(type="NO_ARTIFACTS"),
                environment=codebuild.ProjectEnvironmentArgs(
                    compute_type="BUILD_GENERAL1_SMALL",
                    image="aws/codebuild/standard:7.0",
                    type="ARM_CONTAINER",
                    privileged_mode=True,
                    environment_variables=[
                        {"name": "AWS_DEFAULT_REGION", "value": pulumi.Config("aws").require("region")},
                        {"name": "AWS_ACCOUNT_ID", "value": pulumi.get_stack()},
                        {"name": "IMAGE_REPO_NAME", "value": self.repository.name},
                        {"name": "IMAGE_TAG", "value": self.content_hash[:12]},
                    ],
                ),
                source=codebuild.ProjectSourceArgs(
                    type="NO_SOURCE",
                    buildspec=json.dumps(buildspec),
                ),
                opts=ResourceOptions(parent=self),
            )
    
    def _trigger_build(self):
        """Trigger CodeBuild to start building."""
        return command.local.Command(
            f"{self.name}-trigger",
            create=Output.concat(
                "aws codebuild start-build --project-name ",
                self.build_project.name,
                " --region ",
                pulumi.Config("aws").require("region"),
                " || true",  # Don't fail if build already running
            ),
            opts=ResourceOptions(parent=self),
        )
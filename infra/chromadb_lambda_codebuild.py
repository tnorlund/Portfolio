"""
Alternative ChromaDB Lambda deployment using CodeBuild for container building.

This approach doesn't require the Docker provider and uses AWS services only.
"""
import json
import pulumi
import pulumi_aws as aws
from pathlib import Path

# Create ECR repository
chroma_repo = aws.ecr.Repository(
    "chromadb-lambda-repo",
    name="chromadb-lambda",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True,
    ),
    force_delete=True,
)

# Create S3 bucket for build artifacts
build_bucket = aws.s3.Bucket(
    "chromadb-build-artifacts",
    force_destroy=True,
)

# Upload source code to S3
source_archive = aws.s3.BucketObject(
    "chromadb-source",
    bucket=build_bucket.id,
    key="chromadb-lambda/source.zip",
    source=pulumi.FileArchive(str(Path(__file__).parent / "chromadb_lambda")),
)

# IAM role for CodeBuild
codebuild_role = aws.iam.Role(
    "chromadb-codebuild-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "codebuild.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }),
)

# CodeBuild policy
codebuild_policy = aws.iam.RolePolicy(
    "chromadb-codebuild-policy",
    role=codebuild_role.id,
    policy=pulumi.Output.all(
        build_bucket.arn,
        chroma_repo.arn,
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
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": f"{args[0]}/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                ],
                "Resource": "*",
            },
        ],
    })),
)

# CodeBuild project
build_project = aws.codebuild.Project(
    "chromadb-docker-build",
    service_role=codebuild_role.arn,
    artifacts=aws.codebuild.ProjectArtifactsArgs(
        type="NO_ARTIFACTS",
    ),
    environment=aws.codebuild.ProjectEnvironmentArgs(
        compute_type="BUILD_GENERAL1_SMALL",
        image="aws/codebuild/standard:7.0",
        type="ARM_CONTAINER",
        privileged_mode=True,  # Required for Docker
        environment_variables=[
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="AWS_DEFAULT_REGION",
                value=aws.config.region,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="AWS_ACCOUNT_ID",
                value=aws.get_caller_identity().account_id,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="IMAGE_REPO_NAME",
                value=chroma_repo.name,
            ),
            aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                name="IMAGE_TAG",
                value="latest",
            ),
        ],
    ),
    source=aws.codebuild.ProjectSourceArgs(
        type="S3",
        location=pulumi.Output.concat(
            build_bucket.id, "/", source_archive.key
        ),
        buildspec="""version: 0.2
phases:
  pre_build:
    commands:
      - echo Logging in to Amazon ECR...
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
  build:
    commands:
      - echo Build started on `date`
      - echo Building the Docker image...
      - docker build -t $IMAGE_REPO_NAME:$IMAGE_TAG --platform linux/arm64 .
      - docker tag $IMAGE_REPO_NAME:$IMAGE_TAG $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG
  post_build:
    commands:
      - echo Build completed on `date`
      - echo Pushing the Docker image...
      - docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG
""",
    ),
)

# Trigger build on create/update
build_trigger = aws.codebuild.SourceCredential(
    "chromadb-build-trigger",
    auth_type="PERSONAL_ACCESS_TOKEN",
    server_type="GITHUB",
    token="dummy",  # You'd use a real token or different trigger
)

# Lambda function using the built image
chroma_lambda = aws.lambda_.Function(
    "chromadb-lambda",
    name=f"chromadb-lambda-{pulumi.get_stack()}",
    package_type="Image",
    image_uri=pulumi.Output.concat(
        chroma_repo.repository_url, ":latest"
    ),
    role=codebuild_role.arn,  # Reuse or create new role
    architectures=["arm64"],
    memory_size=2048,
    timeout=300,
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={
            "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
        },
    ),
    ephemeral_storage=aws.lambda_.FunctionEphemeralStorageArgs(
        size=2048,
    ),
)
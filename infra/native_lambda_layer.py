"""
Native Lambda Layer implementation using only Pulumi's built-in components.
No custom shell scripts - uses Pulumi's native AWS resources.
"""

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pulumi
import pulumi_aws as aws
from pulumi import FileArchive, Output


class NativeLambdaLayer:
    """Lambda layer implementation using only Pulumi native components."""

    def __init__(self, name: str, package_path: str, description: str = None):
        self.name = name
        self.package_path = Path(package_path)
        self.description = description or f"Lambda layer for {name}"

        # Create the layer using native Pulumi components
        self._create_layer()

    def _create_layer(self):
        """Create lambda layer using native Pulumi components only."""

        # Step 1: Calculate package hash (for change detection)
        package_hash = self._calculate_package_hash()
        print(f"📦 Package hash for {self.name}: {package_hash[:12]}...")

        # Step 2: Create zip archive using Pulumi's native FileArchive
        layer_archive = self._create_layer_archive()

        # Step 3: Create Lambda layer version directly
        self.layer_version = aws.lambda_.LayerVersion(
            f"{self.name}-layer",
            layer_name=self.name,
            code=layer_archive,
            compatible_runtimes=["python3.12"],
            compatible_architectures=["arm64", "x86_64"],
            description=self.description,
            opts=pulumi.ResourceOptions(
                # Only recreate if package content changed
                additional_secret_outputs=["code"],
                replace_on_changes=["code"],
            ),
        )

        # Export the layer ARN
        self.arn = self.layer_version.arn

        # Step 4: Output useful information
        pulumi.export(f"{self.name}_layer_arn", self.arn)
        pulumi.export(f"{self.name}_layer_version", self.layer_version.version)

    def _calculate_package_hash(self) -> str:
        """Calculate hash of package contents for change detection."""
        hash_md5 = hashlib.md5()

        # Walk through all files in package directory
        for root, dirs, files in os.walk(self.package_path):
            # Sort to ensure consistent ordering
            dirs.sort()
            files.sort()

            for file in files:
                file_path = Path(root) / file
                # Skip common non-essential files
                if file_path.suffix in [".pyc", ".pyo"] or file_path.name in [
                    ".DS_Store"
                ]:
                    continue

                try:
                    with open(file_path, "rb") as f:
                        # Hash file path and content
                        hash_md5.update(
                            str(
                                file_path.relative_to(self.package_path)
                            ).encode()
                        )
                        hash_md5.update(f.read())
                except (IOError, OSError):
                    continue

        return hash_md5.hexdigest()

    def _create_layer_archive(self) -> FileArchive:
        """Create layer archive using Pulumi's native FileArchive."""

        # Create temporary zip file for the layer
        temp_zip_path = f"/tmp/pulumi-layer-{self.name}.zip"

        # Create zip file with proper Lambda layer structure
        with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Lambda layers expect python packages in python/ directory
            for root, dirs, files in os.walk(self.package_path):
                for file in files:
                    file_path = Path(root) / file

                    # Skip unwanted files
                    if file_path.suffix in [
                        ".pyc",
                        ".pyo",
                    ] or file_path.name in [".DS_Store"]:
                        continue

                    # Calculate relative path from package root
                    rel_path = file_path.relative_to(self.package_path)

                    # Add to zip with python/ prefix (Lambda layer requirement)
                    archive_path = f"python/{rel_path}"
                    zipf.write(file_path, archive_path)

        # Return Pulumi FileArchive pointing to our zip
        return FileArchive(temp_zip_path)


class NativeLambdaLayerWithCodeBuild(NativeLambdaLayer):
    """
    Native implementation that also handles CodeBuild for complex dependencies.
    Combines native Pulumi with minimal CodeBuild when needed.
    """

    def __init__(
        self,
        name: str,
        package_path: str,
        description: str = None,
        needs_build: bool = False,
    ):
        self.needs_build = needs_build
        super().__init__(name, package_path, description)

    def _create_layer(self):
        """Create layer with optional CodeBuild step for complex dependencies."""

        if not self.needs_build:
            # Simple case: use native implementation
            super()._create_layer()
            return

        # Complex case: use CodeBuild for dependency installation
        self._create_layer_with_codebuild()

    def _create_layer_with_codebuild(self):
        """Create layer using CodeBuild for dependency resolution."""

        # Step 1: Upload source to S3 using native S3 bucket object
        source_bucket = self._get_or_create_bucket()
        source_object = self._upload_source_to_s3(source_bucket)

        # Step 2: Create CodeBuild project using native resources
        codebuild_project = self._create_codebuild_project(source_bucket)

        # Step 3: Create custom resource to trigger build and get result
        build_trigger = self._create_build_trigger(
            codebuild_project, source_object
        )

        # Step 4: Create layer version from built artifact
        self.layer_version = aws.lambda_.LayerVersion(
            f"{self.name}-layer",
            layer_name=self.name,
            code=aws.lambda_.LayerVersionCodeArgs(
                s3_bucket=source_bucket.bucket, s3_key=f"{self.name}/layer.zip"
            ),
            compatible_runtimes=["python3.12"],
            compatible_architectures=["arm64", "x86_64"],
            description=self.description,
            opts=pulumi.ResourceOptions(depends_on=[build_trigger]),
        )

        self.arn = self.layer_version.arn
        pulumi.export(f"{self.name}_layer_arn", self.arn)

    def _get_or_create_bucket(self) -> aws.s3.Bucket:
        """Get or create S3 bucket for layer artifacts."""
        # This would typically reference an existing bucket
        # For example purposes, creating a new one
        return aws.s3.Bucket(
            f"{self.name}-layer-bucket",
            opts=pulumi.ResourceOptions(protect=True),
        )

    def _upload_source_to_s3(
        self, bucket: aws.s3.Bucket
    ) -> aws.s3.BucketObject:
        """Upload source package to S3 using native BucketObject."""

        # Create source archive
        source_archive = self._create_source_archive()

        # Upload using native S3 BucketObject
        return aws.s3.BucketObject(
            f"{self.name}-source",
            bucket=bucket.bucket,
            key=f"{self.name}/source.zip",
            source=source_archive,
            # This ensures the object is updated when source changes
            etag=self._calculate_package_hash()[:16],
        )

    def _create_source_archive(self) -> FileArchive:
        """Create source archive for CodeBuild."""
        temp_zip_path = f"/tmp/pulumi-source-{self.name}.zip"

        with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.package_path):
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix not in [".pyc", ".pyo"]:
                        rel_path = file_path.relative_to(self.package_path)
                        zipf.write(file_path, rel_path)

        return FileArchive(temp_zip_path)

    def _create_codebuild_project(
        self, bucket: aws.s3.Bucket
    ) -> aws.codebuild.Project:
        """Create CodeBuild project using native Pulumi resources."""

        # Create IAM role for CodeBuild
        codebuild_role = aws.iam.Role(
            f"{self.name}-codebuild-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "codebuild.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )

        # Attach necessary policies
        aws.iam.RolePolicyAttachment(
            f"{self.name}-codebuild-logs-policy",
            role=codebuild_role.name,
            policy_arn="arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
        )

        aws.iam.RolePolicyAttachment(
            f"{self.name}-codebuild-s3-policy",
            role=codebuild_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
        )

        # Create CodeBuild project
        return aws.codebuild.Project(
            f"{self.name}-layer-builder-{pulumi.get_stack()}",  # Pulumi logical name with stack
            service_role=codebuild_role.arn,
            artifacts=aws.codebuild.ProjectArtifactsArgs(
                type="S3",
                location=bucket.bucket.apply(lambda b: f"{b}/{self.name}"),
                name="layer.zip",
                packaging="ZIP",
            ),
            environment=aws.codebuild.ProjectEnvironmentArgs(
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/amazonlinux2-x86_64-standard:3.0",
                type="LINUX_CONTAINER",
            ),
            source=aws.codebuild.ProjectSourceArgs(
                type="S3",
                location=bucket.bucket.apply(
                    lambda b: f"{b}/{self.name}/source.zip"
                ),
            ),
            # Build specification embedded directly (no external files)
            buildspec="version: 0.2\nphases:\n  build:\n    commands:\n      - pip install -r requirements.txt -t python/\n      - zip -r layer.zip python/",
        )

    def _create_build_trigger(
        self,
        project: aws.codebuild.Project,
        source_object: aws.s3.BucketObject,
    ) -> pulumi.CustomResource:
        """Create custom resource to trigger CodeBuild and wait for completion."""

        # This would be a custom Pulumi provider or use pulumi-command
        # For now, showing the concept
        return pulumi.CustomResource(
            f"{self.name}-build-trigger",
            "aws:lambda:InvokeFunctionArg",  # This would be a custom provider
            {
                "project_name": project.name,
                "source_object": source_object.key,
                "depends_on": source_object,
            },
            opts=pulumi.ResourceOptions(depends_on=[project, source_object]),
        )


# Usage Examples:
def create_simple_layer():
    """Example: Simple layer without complex dependencies."""
    return NativeLambdaLayer(
        name="receipt-dynamo",
        package_path="./receipt_dynamo",
        description="DynamoDB operations layer",
    )


def create_complex_layer():
    """Example: Complex layer that needs CodeBuild."""
    return NativeLambdaLayerWithCodeBuild(
        name="receipt-label",
        package_path="./receipt_label",
        description="ML labeling layer with complex dependencies",
        needs_build=True,  # This layer has complex pip dependencies
    )

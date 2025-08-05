"""
Fast Docker builder with hash-based change detection.

This module provides utilities to skip Docker builds when source code hasn't changed,
similar to the fast_lambda_layer.py approach but for containerized Lambdas.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

import pulumi
from pulumi import Output
from pulumi_aws import s3
from pulumi_command import local


def calculate_docker_context_hash(
    context_path: Path,
    dockerfile_path: Path,
    include_patterns: List[str] = None,
) -> str:
    """
    Calculate a hash of the Docker build context to detect changes.
    
    Args:
        context_path: Root path for Docker build context
        dockerfile_path: Path to Dockerfile
        include_patterns: List of glob patterns to include (default: all files)
    
    Returns:
        SHA256 hash of the build context
    """
    hash_obj = hashlib.sha256()
    
    # Hash the Dockerfile
    if dockerfile_path.exists():
        with open(dockerfile_path, "rb") as f:
            hash_obj.update(f.read())
    
    # Default patterns if none provided
    if include_patterns is None:
        include_patterns = [
            "**/*.py",
            "**/requirements.txt",
            "**/pyproject.toml",
            "**/Dockerfile",
        ]
    
    # Hash all relevant files in the context
    files_to_hash = []
    for pattern in include_patterns:
        files_to_hash.extend(context_path.glob(pattern))
    
    # Sort for consistent ordering
    for file_path in sorted(set(files_to_hash)):
        if file_path.is_file():
            # Hash file content
            with open(file_path, "rb") as f:
                hash_obj.update(f.read())
            # Hash relative path for completeness
            rel_path = file_path.relative_to(context_path)
            hash_obj.update(str(rel_path).encode())
    
    return hash_obj.hexdigest()


def create_hash_check_script(
    bucket_name: str,
    image_name: str,
    context_hash: str,
    ecr_repo_url: str,
) -> str:
    """
    Generate a script that checks if we need to rebuild based on hash.
    
    Returns a bash script that:
    1. Checks if the hash has changed
    2. Checks if the image exists in ECR
    3. Returns 0 if build needed, 1 if can skip
    """
    return f"""#!/bin/bash
set -e

BUCKET="{bucket_name}"
IMAGE_NAME="{image_name}"
HASH="{context_hash}"
ECR_REPO="{ecr_repo_url}"

echo "🔍 Checking if Docker build needed for {image_name}..."

# Check stored hash
STORED_HASH=$(aws s3 cp s3://$BUCKET/docker-hashes/$IMAGE_NAME/hash.txt - 2>/dev/null || echo '')

if [ "$STORED_HASH" = "$HASH" ]; then
    echo "✅ No changes detected (hash: ${{HASH:0:12}}...)"
    
    # Verify image exists in ECR
    if aws ecr describe-images --repository-name $(echo $ECR_REPO | cut -d'/' -f2) --image-ids imageTag=latest &>/dev/null; then
        echo "✅ Image exists in ECR. Skipping build."
        # Update the image URI output file so Pulumi knows the current image
        echo "$ECR_REPO:latest" > /tmp/{image_name}_uri.txt
        exit 1  # Skip build
    else
        echo "⚠️  Image missing from ECR. Will rebuild."
    fi
else
    echo "📝 Changes detected. Will build new image."
    echo "   Old hash: ${{STORED_HASH:0:12}}..."
    echo "   New hash: ${{HASH:0:12}}..."
fi

exit 0  # Proceed with build
"""


def create_conditional_docker_build(
    name: str,
    context_path: Path,
    dockerfile_path: Path,
    ecr_repo_url: Output[str],
    hash_bucket: s3.Bucket,
    include_patterns: List[str] = None,
    build_args: Dict[str, str] = None,
) -> local.Command:
    """
    Create a conditional Docker build that only runs when source changes.
    
    This is similar to the fast_lambda_layer approach but for Docker images.
    """
    # Calculate hash of the build context
    context_hash = calculate_docker_context_hash(
        context_path, dockerfile_path, include_patterns
    )
    
    # Create the build script
    build_script = ecr_repo_url.apply(
        lambda repo_url: f"""#!/bin/bash
set -e

HASH="{context_hash}"
ECR_REPO="{repo_url}"
IMAGE_NAME="{name}"

# Build the image
echo "🏗️  Building Docker image..."
docker build -t $IMAGE_NAME:latest \\
    -f {dockerfile_path} \\
    {' '.join(f'--build-arg {k}={v}' for k, v in (build_args or {}).items())} \\
    {context_path}

# Tag for ECR
docker tag $IMAGE_NAME:latest $ECR_REPO:latest

# Push to ECR
echo "📤 Pushing to ECR..."
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_REPO
docker push $ECR_REPO:latest

# Save hash to S3
echo "$HASH" | aws s3 cp - s3://{hash_bucket.bucket}/docker-hashes/$IMAGE_NAME/hash.txt

# Output the image URI
echo "$ECR_REPO:latest" > /tmp/{name}_uri.txt

echo "✅ Docker image built and pushed successfully"
"""
    )
    
    # Check script determines if we need to build
    check_script = Output.all(
        hash_bucket.bucket, ecr_repo_url
    ).apply(
        lambda args: create_hash_check_script(
            args[0], name, context_hash, args[1]
        )
    )
    
    # Create conditional build command
    conditional_build = local.Command(
        f"{name}-docker-build",
        create=Output.all(check_script, build_script).apply(
            lambda scripts: f"""#!/bin/bash
# First check if build is needed
cat > /tmp/check_{name}.sh << 'EOF'
{scripts[0]}
EOF

chmod +x /tmp/check_{name}.sh

if /tmp/check_{name}.sh; then
    # Build needed, run build script
    cat > /tmp/build_{name}.sh << 'EOF'
{scripts[1]}
EOF
    
    chmod +x /tmp/build_{name}.sh
    /tmp/build_{name}.sh
else
    echo "⚡ Skipping build - no changes detected"
fi
"""
        ),
        triggers=[context_hash],  # Pulumi tracks this
    )
    
    return conditional_build


# Example usage in chromadb_lambdas.py:
"""
# Instead of using pulumi_docker.Image directly, use:
from fast_docker_builder import create_conditional_docker_build

# Create hash bucket for tracking
hash_bucket = aws.s3.Bucket(
    "docker-build-hashes",
    force_destroy=True,
)

# Create conditional build
polling_build = create_conditional_docker_build(
    name="chromadb-poll",
    context_path=Path(__file__).parent.parent.parent,
    dockerfile_path=Path(__file__).parent / "chromadb_polling_lambda" / "Dockerfile",
    ecr_repo_url=self.polling_repo.repository_url,
    hash_bucket=hash_bucket,
    include_patterns=[
        "receipt_label/**/*.py",
        "receipt_dynamo/**/*.py",
        "infra/word_label_step_functions/chromadb_polling_lambda/**",
    ],
    build_args={"PYTHON_VERSION": "3.12"},
)

# Use the output image URI for Lambda
self.polling_lambda = Function(
    f"chromadb-poll-fn-{stack}",
    package_type="Image",
    image_uri=local.Command(
        f"get-polling-image-uri",
        create="cat /tmp/chromadb-poll_uri.txt",
        depends_on=[polling_build],
    ).stdout,
    # ... rest of config
)
"""
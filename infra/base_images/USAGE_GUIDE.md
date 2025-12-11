# Base Images Usage Guide

This guide explains how to use the dependency graph-driven base images to speed up container Lambda builds and reduce CodeBuild costs.

## Overview

Base images contain pre-installed Python packages, allowing Lambda container builds to skip installing dependencies and only copy handler code. This dramatically reduces:
- S3 upload size (no need to upload package directories)
- CodeBuild build time (no pip install step)
- Overall deployment costs

## Dependency Graph

The dependency graph is automatically built from `pyproject.toml` files and tracks which packages depend on which other packages:

- `receipt-dynamo` - No dependencies (base package)
- `receipt-chroma` → depends on `receipt-dynamo`
- `receipt_label` → depends on `receipt-dynamo`
- `receipt_upload` → depends on `receipt-dynamo`
- `receipt_places` → depends on `receipt-dynamo`
- `receipt-agent` → depends on `receipt-dynamo`, `receipt-chroma`, `receipt_label`

## Available Base Images

Currently, the following base images are created:

1. **receipt-dynamo base** - Contains `receipt_dynamo` package
2. **receipt-label base** - Contains `receipt_dynamo` + `receipt_label` packages

## Using Base Images in Container Lambdas

### Step 1: Determine Required Packages

Look at your Lambda's Dockerfile to see which packages it needs. For example:

```dockerfile
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_label/ /tmp/receipt_label/
```

This Lambda needs: `receipt-dynamo`, `receipt-chroma`, `receipt_label`

### Step 2: Find Suitable Base Image

Use the `BaseImages.get_base_image_uri_for_package()` method to find a base image that contains your required packages:

```python
from infra.base_images.base_images import BaseImages

base_images = BaseImages("base", pulumi.get_stack())

# For a Lambda that needs receipt_label
base_image_uri = base_images.get_base_image_uri_for_package("receipt_label")
# Returns: label_base_image.tags[0] (contains receipt_dynamo + receipt_label)

# For a Lambda that needs receipt_chroma
base_image_uri = base_images.get_base_image_uri_for_package("receipt-chroma")
# Returns: None (no base image exists yet - would need to create one)
```

### Step 3: Create Dockerfile.with_base

Create a version of your Dockerfile that uses the base image:

```dockerfile
# syntax=docker/dockerfile:1
ARG BASE_IMAGE_URI
FROM ${BASE_IMAGE_URI:-public.ecr.aws/lambda/python:3.12}

# Only copy handler code (packages already in base image)
COPY infra/my_lambda/handler.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.lambda_handler"]
```

### Step 4: Update CodeBuildDockerImage Call

Pass the `base_image_uri` parameter to `CodeBuildDockerImage`:

```python
from infra.components.codebuild_docker_image import CodeBuildDockerImage

docker_image = CodeBuildDockerImage(
    "my-lambda-image",
    dockerfile_path="infra/my_lambda/Dockerfile.with_base",
    build_context_path=".",
    base_image_uri=base_image_uri,  # Pass base image URI
    lambda_function_name="my-lambda",
    lambda_config={...},
    platform="linux/arm64",
)
```

## How It Works

When `base_image_uri` is provided:

1. **Content Hash Calculation**: The hash calculation skips packages that are in the base image (determined by `_should_include_base_packages()`)

2. **S3 Upload**: The upload script skips copying package directories that are in the base image (controlled by `SKIP_BASE_PACKAGES` environment variable)

3. **Docker Build**: The Dockerfile uses `FROM ${BASE_IMAGE_URI}` instead of installing packages from scratch

4. **Rebuild Propagation**: When a package in the base image changes, Pulumi detects the change (via content hash) and rebuilds the base image. All Lambdas using that base image automatically get the new version.

## Creating New Base Images

To create a base image for a new package combination:

1. **Update BaseImages class** in `infra/base_images/base_images.py`:
   - Add a new ECR repository
   - Create a new `docker_build.Image` resource
   - Add it to the `base_images` dictionary

2. **Create Dockerfile** in `infra/base_images/dockerfiles/`:
   - Copy the package directories
   - Install packages in dependency order
   - Use content-based tagging

3. **Update get_base_image_uri_for_package()** to return the new base image for appropriate packages

## Viewing the Dependency Graph

To view the dependency graph as JSON:

```bash
python scripts/generate_dependency_graph.py
```

This outputs the complete dependency graph including:
- All packages and their paths
- Direct dependencies for each package
- Transitive dependencies
- Topological sort order

## Example: Adding Base Image to a Lambda

Here's a complete example of adding base image support to a container Lambda:

```python
# In your infrastructure file
from infra.base_images.base_images import BaseImages

# Get base images (created in __main__.py)
base_images = BaseImages("base", pulumi.get_stack())

# Determine which base image to use
# This Lambda needs receipt_label, which is in the label base image
base_image_uri = base_images.get_base_image_uri_for_package("receipt_label")

# Create Docker image with base image
docker_image = CodeBuildDockerImage(
    "my-lambda-image",
    dockerfile_path="infra/my_lambda/Dockerfile.with_base",
    build_context_path=".",
    base_image_uri=base_image_uri,
    lambda_function_name="my-lambda",
    lambda_config={
        "role_arn": lambda_role.arn,
        "timeout": 300,
        "memory_size": 512,
    },
    platform="linux/arm64",
)

lambda_function = docker_image.lambda_function
```

## Cost Savings

Using base images can reduce:

- **S3 Upload Size**: From ~100-200MB (with packages) to ~1-5MB (handler only)
- **CodeBuild Time**: From 5-10 minutes to 1-2 minutes (no pip install)
- **CodeBuild Cost**: Proportional to time reduction (50-80% savings)

## Troubleshooting

### Base image not found

If `get_base_image_uri_for_package()` returns `None`:
- Check if a base image exists for that package
- Consider creating a new base image if the package is commonly used
- Use the dependency graph to find which base image contains your package

### Packages missing in base image

If your Lambda needs packages not in any base image:
- The Dockerfile will fall back to installing from scratch
- Consider creating a new base image that includes those packages
- Or manually copy the missing packages in your Dockerfile

### Rebuild not triggered

If changes to a package don't trigger Lambda rebuilds:
- Verify the base image is being rebuilt (check Pulumi outputs)
- Ensure `base_image_uri` is passed to `CodeBuildDockerImage`
- Check that the content hash includes the base image URI

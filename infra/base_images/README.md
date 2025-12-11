# Base Image Optimization for CodeBuildDockerImage

## Overview

This document explains the base image optimization strategy for Lambda container builds using the `CodeBuildDockerImage` component.

## Problem Statement

The original `CodeBuildDockerImage` implementation had performance issues:

1. **Slow S3 uploads**: Each Lambda uploaded the entire build context including `receipt_dynamo`, `receipt_chroma`, and `receipt_label` packages (typically 10-50 MB)
2. **Redundant uploads**: Multiple Lambdas uploaded the same dependency packages independently
3. **Inefficient change detection**: Changes to base packages triggered rebuilds of all Lambdas
4. **No dependency layer caching**: Each build reinstalled Python packages from scratch

## Solution: Base Images with Layer Caching

The solution leverages the existing but unused `base_images` module to create reusable Docker base images containing pre-installed Python packages.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Base Images (Built Once, Updated Only When Packages Change)    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  base-receipt-dynamo-{stack}                                    │
│  ├── FROM public.ecr.aws/lambda/python:3.12                     │
│  └── receipt_dynamo package installed                           │
│                                                                 │
│  base-receipt-label-{stack}                                     │
│  ├── FROM public.ecr.aws/lambda/python:3.12                     │
│  ├── receipt_dynamo package installed                           │
│  └── receipt_label[full] package installed                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Lambda Images (Built Often, Only Copy Handler Code)            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  create-labels-lambda                                           │
│  ├── FROM base-receipt-label-{stack}                            │
│  └── COPY handler.py                                            │
│                                                                 │
│  upload-images-lambda                                           │
│  ├── FROM base-receipt-label-{stack}                            │
│  └── COPY handler/                                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Benefits

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **S3 Upload Size** | 10-50 MB | 1-5 MB | ~70-90% reduction |
| **S3 Upload Time** | 10-30s | 1-5s | ~80% reduction |
| **CodeBuild Time** | 3-5 min | 30-60s | ~70% reduction |
| **Rebuild Triggers** | Any package change | Handler-only changes | Precise |

### How It Works

#### 1. Base Image Creation (Once)

```python
# In infra/__main__.py
base_images = BaseImages("base", pulumi.get_stack())
```

This creates two ECR repositories with Docker images:
- `base-receipt-dynamo-{stack}`: Contains only `receipt_dynamo`
- `base-receipt-label-{stack}`: Contains `receipt_dynamo` + `receipt_label[full]`

Base images are:
- Built using `pulumi-docker-build` for native Pulumi integration
- Tagged with content-based hashes (e.g., `git-abc1234` or `sha-fedcba98`)
- Cached in ECR for fast subsequent builds
- Rebuilt **only** when package files change

#### 2. Lambda Image Creation (Often)

```python
# In lambda infrastructure file
create_labels_docker_image = CodeBuildDockerImage(
    f"{name}-create-labels-img",
    dockerfile_path="infra/create_labels_step_functions/lambdas/Dockerfile.with_base",
    build_context_path=".",
    lambda_function_name=f"{name}-create-labels",
    lambda_config=create_labels_lambda_config,
    platform="linux/arm64",
    base_image_uri=base_images.label_base_image.tags[0],  # Use base image
)
```

When `base_image_uri` is provided:
1. **Hash Calculation**: Excludes base packages, only hashes handler code
2. **S3 Upload**: Skips `receipt_dynamo`, `receipt_chroma`, `receipt_label` directories
3. **Docker Build**: Uses `FROM $BASE_IMAGE_URI` instead of public Lambda base
4. **CodeBuild**: Only copies handler code, leveraging base image layers

### Dockerfile Patterns

#### Without Base Image (Original)
```dockerfile
# Multi-stage build
FROM public.ecr.aws/lambda/python:3.12 AS dependencies
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir "/tmp/receipt_label[full]"

FROM dependencies
COPY infra/my_lambda/handler.py ${LAMBDA_TASK_ROOT}/handler.py
CMD ["handler.lambda_handler"]
```

#### With Base Image (Optimized)
```dockerfile
# Single-stage build using base image
ARG BASE_IMAGE_URI
FROM ${BASE_IMAGE_URI:-public.ecr.aws/lambda/python:3.12}
COPY infra/my_lambda/handler.py ${LAMBDA_TASK_ROOT}/handler.py
CMD ["handler.lambda_handler"]
```

## Migration Guide

### Step 1: Verify Base Images are Built

Base images are created early in `__main__.py`:

```python
base_images = BaseImages("base", pulumi.get_stack())
```

After `pulumi up`, verify in ECR:
- `base-receipt-dynamo-{stack}`
- `base-receipt-label-{stack}`

### Step 2: Create Optimized Dockerfile

For your Lambda, create a new `Dockerfile.with_base`:

```dockerfile
# syntax=docker/dockerfile:1
ARG BASE_IMAGE_URI
FROM ${BASE_IMAGE_URI:-public.ecr.aws/lambda/python:3.12}

# Only copy Lambda-specific handler code
COPY infra/my_lambda/handler/ ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
```

### Step 3: Update Lambda Infrastructure

Modify your Lambda component to accept and use the base image:

```python
def __init__(
    self,
    name: str,
    base_image_uri: Optional[pulumi.Input[str]] = None,
    # ... other parameters
):
    # Choose Dockerfile based on whether base image is provided
    dockerfile_to_use = (
        "infra/my_lambda/Dockerfile.with_base"
        if base_image_uri
        else "infra/my_lambda/Dockerfile"
    )
    
    my_docker_image = CodeBuildDockerImage(
        f"{name}-img",
        dockerfile_path=dockerfile_to_use,
        build_context_path=".",
        lambda_config=my_lambda_config,
        platform="linux/arm64",
        base_image_uri=base_image_uri,  # Pass base image
    )
```

### Step 4: Update Main Infrastructure

Pass the base image when creating your Lambda:

```python
# Choose the appropriate base image
# - dynamo_base_image: Only receipt_dynamo
# - label_base_image: receipt_dynamo + receipt_label[full]
my_lambda = MyLambdaComponent(
    f"my-lambda-{pulumi.get_stack()}",
    base_image_uri=base_images.label_base_image.tags[0],
    # ... other parameters
)
```

### Step 5: Deploy and Verify

```bash
pulumi up
```

Monitor for:
- Base images built first
- Lambda uploads showing reduced context size
- CodeBuild logs showing base image usage
- Successful Lambda deployment

## Change Detection Logic

### When Base Packages Change

```
receipt_dynamo/receipt_dynamo/models.py modified
    ↓
Base image hash changes
    ↓
BaseImages component rebuilds base-receipt-dynamo image
    ↓
Dependent Lambdas detect base image URI change
    ↓
Lambdas rebuild using new base image
```

### When Handler Changes

```
infra/my_lambda/handler.py modified
    ↓
Lambda content hash changes (excluding base packages)
    ↓
Lambda triggers S3 upload (handler only, ~1 MB)
    ↓
CodeBuild builds Lambda using cached base image
    ↓
Lambda updates with new image
```

### When Unrelated Files Change

```
portfolio/README.md modified
    ↓
No hash changes (files not in include patterns)
    ↓
No rebuilds triggered
    ↓
Fast pulumi up
```

## Configuration

### Use Static Tags (Development)

For local development, avoid rebuilding base images on every change:

```yaml
# Pulumi.dev.yaml
config:
  portfolio:use-static-base-image: true
```

This uses the `stable` tag instead of content-based hashes.

### Control ECR Lifecycle

Manage how many base image versions to retain:

```yaml
# Pulumi.yaml
config:
  portfolio:ecr-max-images: 10  # Keep 10 most recent
  portfolio:ecr-max-age-days: 30  # Expire untagged after 30 days
```

## Troubleshooting

### Base Image Not Found

**Symptom**: CodeBuild fails with "Error: image not found"

**Solution**: Ensure base images are built before Lambdas:
```python
# In Lambda infrastructure
opts=ResourceOptions(
    parent=self,
    depends_on=[base_images.label_base_image],
)
```

### S3 Upload Still Large

**Symptom**: Context size not reduced as expected

**Solution**: Verify `base_image_uri` is passed and Dockerfile uses it:
```bash
# Check upload logs for "Using base image - skipping..."
pulumi up --logtostderr -v=9 2>&1 | grep "base image"
```

### Hash Collision

**Symptom**: Different code versions share same hash

**Solution**: Ensure git working tree is clean or disable static tagging:
```bash
git status  # Check for uncommitted changes
```

## Advanced Usage

### Creating Custom Base Images

For specialized dependencies, create additional base images:

```python
# In base_images.py
self.chromadb_base_repo = Repository(
    f"base-receipt-chromadb-ecr-{stack}",
    name=f"base-receipt-chromadb-{stack}",
)

self.chromadb_base_image = docker_build.Image(
    f"base-receipt-chromadb-img-{stack}",
    dockerfile={"location": "dockerfiles/Dockerfile.receipt_chromadb"},
    # ... configuration
)
```

### Multi-Architecture Builds

Base images support multi-architecture:

```python
platforms=["linux/arm64", "linux/amd64"],
```

## References

- [CodeBuildDockerImage Component](../components/codebuild_docker_image.py)
- [BaseImages Component](base_images.py)
- [Buildspec Generator](../shared/buildspecs.py)
- [Docker Build Best Practices](https://docs.docker.com/develop/dev-best-practices/)

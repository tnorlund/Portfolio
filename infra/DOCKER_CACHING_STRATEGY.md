# Docker Container Caching Strategy for Lambda Functions

## Current Problems

### 1. Redundant Package Installation
The current architecture defeats the purpose of base images:
- Base images (`base-receipt-dynamo`, `base-receipt-label`) install packages
- Lambda Dockerfiles then **reinstall the same packages** with `--force-reinstall`
- This causes complete cache invalidation and slow builds

### 2. Dynamic Base Image References
- Base images are always tagged as `:latest`
- Image names are Pulumi `Output[str]` types that change between runs
- Docker cannot effectively cache when FROM images keep changing

### 3. Large Build Context
- All builds use the project root as context
- Any file change anywhere in the project triggers rebuilds
- No targeted `.dockerignore` files for specific components

## Recommended Solutions

### Solution 1: Fix Lambda Dockerfiles

Stop reinstalling packages that already exist in base images:

```dockerfile
# ❌ CURRENT (Bad - reinstalls everything)
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}}
RUN pip install --no-cache-dir --force-reinstall /tmp/receipt_dynamo && \
    pip install --no-cache-dir --force-reinstall /tmp/receipt_label

# ✅ RECOMMENDED (Good - use what's in base image)
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}}
# Only install additional dependencies not in base image
RUN pip install --no-cache-dir chromadb>=0.5.0
```

### Solution 2: Content-Based Image Tags

Replace `:latest` with deterministic tags based on content:

```python
# In base_images/base_images.py
import subprocess
import hashlib
from pathlib import Path

def get_content_hash(package_dir: str) -> str:
    """Generate a hash based on package content."""
    # Option 1: Use git commit SHA
    try:
        commit = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=package_dir
        ).decode().strip()
        return f"git-{commit}"
    except:
        pass
    
    # Option 2: Hash package files
    package_files = list(Path(package_dir).rglob("*.py"))
    content = "".join(
        f.read_text() for f in sorted(package_files) 
        if f.is_file() and not f.name.startswith("test_")
    )
    return f"sha-{hashlib.sha256(content.encode()).hexdigest()[:12]}"

# Use in image builds
tag = get_content_hash("receipt_dynamo")
image_name = self.dynamo_base_repo.repository_url.apply(
    lambda url: f"{url}:{tag}"
)
```

### Solution 3: Development vs Production Modes

Support static base images for local development:

```python
# In chromadb_lambdas.py
def get_base_image_name(base_image_output, stack, service="label"):
    """Get base image name based on environment."""
    if os.environ.get("USE_STATIC_BASE_IMAGE"):
        # For local development - use a fixed tag
        account_id = get_caller_identity().account_id
        region = config.region
        return f"{account_id}.dkr.ecr.{region}.amazonaws.com/base-receipt-{service}-{stack}:stable"
    else:
        # For production - use dynamic Pulumi output
        return base_image_output
```

### Solution 4: Targeted Build Contexts

Create specific `.dockerignore` files for each component:

```bash
# infra/base_images/.dockerignore
*
!receipt_dynamo/
!receipt_label/
!pyproject.toml
**/__pycache__
**/*.pyc
**/*.pyo
**/tests/
**/test_*.py
**/.pytest_cache/
**/.mypy_cache/
```

```bash
# infra/embedding_step_functions/.dockerignore
*
!infra/embedding_step_functions/*/handler.py
!infra/word_label_step_functions/*/handler.py
```

### Solution 5: Docker BuildKit Optimization

Enable BuildKit inline cache and cache mounts:

```dockerfile
# In Dockerfiles
# syntax=docker/dockerfile:1
FROM ${BASE_IMAGE}

# Use cache mounts for pip
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install chromadb>=0.5.0
```

```python
# In Pulumi Docker builds
build=DockerBuildArgs(
    context=str(build_context_path),
    dockerfile=dockerfile_path,
    platform="linux/arm64",
    args={
        **build_args,
        "BUILDKIT_INLINE_CACHE": "1",
    },
    # Note: Pulumi Docker provider doesn't support cache_from with Outputs
    # For now, use environment variable for BuildKit
)
```

### Solution 6: ECR Caching Strategy

Pull existing images before building for cache:

```bash
# Before running pulumi up
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR_REGISTRY

# Pull latest images for cache
docker pull $ECR_REGISTRY/base-receipt-dynamo-dev:latest || true
docker pull $ECR_REGISTRY/base-receipt-label-dev:latest || true

# Enable BuildKit
export DOCKER_BUILDKIT=1
export BUILDKIT_PROGRESS=plain

# Now run pulumi up
pulumi up
```

## Implementation Plan

### Phase 1: Quick Wins (Immediate)
1. Fix Lambda Dockerfiles to stop reinstalling packages
2. Add targeted `.dockerignore` files
3. Enable BuildKit with environment variable

### Phase 2: Stable Development (1-2 days)
1. Implement content-based tagging for base images
2. Add development mode with static base images
3. Create build script that pulls images for caching

### Phase 3: Advanced Optimization (Optional)
1. Implement layer caching with cache mounts
2. Use multi-stage builds for smaller final images
3. Consider using AWS CodeBuild for remote caching

## Expected Results

- **Build time reduction**: 5-10 minutes → 30 seconds (when cached)
- **Network usage**: Reduced by 90% (no redundant package downloads)
- **Development experience**: Instant builds when only handler code changes

## Environment Variables

```bash
# For local development
export USE_STATIC_BASE_IMAGE=true
export DOCKER_BUILDKIT=1

# For CI/CD
export DOCKER_BUILDKIT=1
export BUILDKIT_PROGRESS=plain
```

## Monitoring Cache Effectiveness

Check Docker build output for cache hits:
```
=> CACHED [2/5] COPY receipt_dynamo /tmp/receipt_dynamo
=> CACHED [3/5] RUN pip install /tmp/receipt_dynamo
```

If you see "CACHED" for most layers, the caching is working effectively.
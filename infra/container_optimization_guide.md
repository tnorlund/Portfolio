# Container Build Optimization Guide

## Current Issues
- Local builds take 10-20 minutes for all containers
- Sequential builds (no parallelization)
- Large build contexts uploaded each time
- No cross-machine caching

## Optimization Strategies

### 1. Minimize Dockerfile Complexity
```dockerfile
# Bad - reinstalls everything
FROM public.ecr.aws/lambda/python:3.12
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label
RUN pip install /tmp/receipt_dynamo /tmp/receipt_label

# Good - uses pre-built base
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY handler.py ${LAMBDA_TASK_ROOT}/
```

### 2. Use Multi-Stage Builds for Dependencies
```dockerfile
# Build stage - compile dependencies once
FROM public.ecr.aws/lambda/python:3.12 AS builder
COPY requirements.txt .
RUN pip install --target /opt/python -r requirements.txt

# Runtime stage - just copy compiled deps
FROM public.ecr.aws/lambda/python:3.12
COPY --from=builder /opt/python /opt/python
COPY handler.py ${LAMBDA_TASK_ROOT}/
```

### 3. Leverage BuildKit Cache Mounts
```dockerfile
# Enable BuildKit
# syntax=docker/dockerfile:1
FROM public.ecr.aws/lambda/python:3.12
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install chromadb numpy pandas
```

### 4. Use Remote Builds (Recommended)

#### Option A: AWS CodeBuild (as shown in fast_container_image.py)
- Pros: Fully managed, parallel builds, automatic caching
- Cons: Slightly more complex setup

#### Option B: Docker Build Cloud
```typescript
// In Pulumi
new docker.Image("my-image", {
    build: {
        context: ".",
        dockerfile: "Dockerfile",
        platform: "linux/amd64",
        builderVersion: "BuilderBuildKit",
        cloudBuild: true,  // Use Docker Build Cloud
    },
});
```

#### Option C: GitHub Actions for Pre-building
```yaml
# .github/workflows/build-base-images.yml
on:
  push:
    paths:
      - 'receipt_dynamo/**'
      - 'receipt_label/**'
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/amazon-ecr-login@v1
      - run: |
          docker buildx build --push \
            -t $ECR_REPO:latest \
            --cache-from type=gha \
            --cache-to type=gha,mode=max .
```

### 5. Immediate Improvements You Can Make

#### A. Add .dockerignore
```bash
# .dockerignore
**/__pycache__
**/*.pyc
.git
.pytest_cache
.venv
*.md
tests/
docs/
```

#### B. Use Layer Caching Better
```python
# In chromadb_lambdas.py
build_args = {
    "PYTHON_VERSION": "3.12",
    "BUILDKIT_INLINE_CACHE": "1",  # Enable inline cache
}

# Add cache-from
self.find_unembedded_image = Image(
    f"find-unembedded-img-{stack}",
    build=DockerBuildArgs(
        context=str(build_context_path),
        dockerfile=str(dockerfile_path),
        platform="linux/arm64",
        args=build_args,
        cache_from=[
            f"{self.find_unembedded_repo.repository_url}:latest",
            base_image_name,  # Cache from base image
        ],
    ),
    # ...
)
```

#### C. Parallel Local Builds
Instead of Pulumi's sequential builds, trigger parallel builds:

```python
# build_all_containers.py
import asyncio
import subprocess

async def build_image(name, dockerfile, args):
    cmd = ["docker", "build", "-f", dockerfile, "-t", name]
    for k, v in args.items():
        cmd.extend(["--build-arg", f"{k}={v}"])
    cmd.append(".")
    
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.wait()
    return name

async def main():
    tasks = [
        build_image("lambda1", "Dockerfile1", {"BASE": "..."}),
        build_image("lambda2", "Dockerfile2", {"BASE": "..."}),
        # ... all your images
    ]
    await asyncio.gather(*tasks)

asyncio.run(main())
```

## Recommended Approach

1. **Short term**: Add .dockerignore, use cache_from, minimize Dockerfiles
2. **Medium term**: Implement FastContainerImage with CodeBuild
3. **Long term**: Pre-build base images in CI/CD, use them everywhere

## Expected Results
- Local builds: 10-20 min → 2-3 min
- CI/CD builds: 15-20 min → 5-7 min  
- Pulumi up: 20+ min → <1 min (with async builds)
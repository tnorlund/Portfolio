# ChromaDB Lambda Docker Optimization Guide

## Current Issues with ChromaDB Lambdas

The ChromaDB Lambda containers are taking a long time to build because:

### 1. **Massive Build Context (2.9GB)**
```python
# Current: Uses entire repository as context
build_context_path = Path(__file__).parent.parent.parent  # Goes up to repo root
```
- Every Lambda copies the entire 2.9GB repository
- Docker has to scan all files even if they're not used
- Any change anywhere in the repo invalidates the context hash

### 2. **No Docker Caching**
```python
# Current: Uses old Pulumi Docker provider
Image(
    build=DockerBuildArgs(
        # TODO: Add cache_from when Pulumi Docker provider supports it properly
    ),
)
```
- No `cache_from` support in old provider
- No ECR cache layers being used
- Every build starts from scratch

### 3. **Not Leveraging Base Images Properly**
```dockerfile
# Current: Conditionally uses base image but still copies packages
COPY receipt_dynamo /tmp/receipt_dynamo
COPY receipt_label /tmp/receipt_label
```
- Even with base image, still copying packages from repo
- Conditional logic adds complexity
- Not taking full advantage of pre-built layers

## Optimization Strategy

### 1. **Use Scoped Build Contexts**
```python
# Optimized: Just the handler directory (few KB)
context=docker_build.ContextArgs(
    location=str(handler_path),  # Just chromadb_word_polling_lambda/
)
```
- Context reduced from 2.9GB to ~10KB per Lambda
- Only includes handler.py and Dockerfile
- Context hash only changes when handler changes

### 2. **Migrate to docker-build Provider**
```python
# Optimized: Full caching support
docker_build.Image(
    cache_from=[
        docker_build.CacheFromArgs(
            registry=docker_build.CacheFromRegistryArgs(
                ref=repo.repository_url.apply(lambda url: f"{url}:cache"),
            ),
        ),
    ],
    cache_to=[
        docker_build.CacheToArgs(
            registry=docker_build.CacheToRegistryArgs(
                ref=repo.repository_url.apply(lambda url: f"{url}:cache"),
            ),
        ),
    ],
)
```
- Full ECR cache layer support
- Reuses layers from previous builds
- Dramatic speed improvement on subsequent builds

### 3. **Simplified Dockerfiles**
```dockerfile
# Optimized: Minimal Dockerfile leveraging base image
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Copy only the handler (context is just handler directory)
COPY handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.poll_handler"]
```
- Base image has all dependencies pre-installed
- No conditional logic needed
- Just copy handler and set CMD

## Expected Performance Improvements

### Before Optimization
- **Context size**: 2.9GB per Lambda
- **Context processing**: 30-60 seconds
- **Build time (no cache)**: 2-5 minutes per Lambda
- **Build time (with cache)**: Still 1-2 minutes (poor caching)
- **Total for 6 Lambdas**: 12-30 minutes

### After Optimization
- **Context size**: ~10KB per Lambda (99.99% reduction)
- **Context processing**: <1 second
- **Build time (no cache)**: 30-60 seconds per Lambda
- **Build time (with cache)**: 5-10 seconds per Lambda
- **Total for 6 Lambdas**: 3-6 minutes first build, <1 minute cached

## Implementation Steps

### Option 1: Quick Fix (Minimal Changes)
1. Update build_context_path to use handler directories
2. Create .dockerignore files in each handler directory
3. Keep existing Docker provider (no caching benefits)

### Option 2: Full Optimization (Recommended)
1. Use the new `chromadb_lambdas_v2.py` implementation
2. Update `infra.py` to import ChromaDBLambdasV2
3. Pass base_image_resource for proper dependencies
4. Benefit from all optimizations

### Option 3: Gradual Migration
1. Start with one Lambda as proof of concept
2. Measure performance improvements
3. Migrate remaining Lambdas incrementally

## Migration Example

```python
# In infra.py, replace:
from embedding_step_functions.chromadb_lambdas import ChromaDBLambdas

# With:
from embedding_step_functions.chromadb_lambdas_v2 import ChromaDBLambdasV2

# Update instantiation:
chromadb_lambdas = ChromaDBLambdasV2(
    "chromadb-lambdas",
    chromadb_bucket_name=chromadb_storage.bucket_name,
    chromadb_queue_url=chromadb_queues.delta_queue_url,
    chromadb_queue_arn=chromadb_queues.delta_queue_arn,
    openai_api_key=openai_api_key,
    s3_batch_bucket_name=s3_batch.bucket,
    stack=stack,
    base_image_name=base_images.label_base_image.ref,
    base_image_resource=base_images.label_base_image,  # For dependency
)
```

## Key Insights

1. **Context size matters more than you think** - Docker has to checksum every file
2. **Base images are powerful** - Leverage them fully, don't duplicate work
3. **ECR caching is essential** - The docker-build provider enables this
4. **Scoped contexts are the biggest win** - 99.99% reduction in context size

## Testing the Optimization

```bash
# Check context sizes
du -sh infra/embedding_step_functions/chromadb_word_polling_lambda/  # ~10KB

# Time the builds
time pulumi up  # First build
time pulumi up  # Cached build (should be much faster)

# Monitor Docker build output
docker system df  # Check cache usage
```

## Conclusion

The ChromaDB Lambdas are currently one of the slowest parts of the build because they:
- Use the entire 2.9GB repo as context (vs 10KB needed)
- Don't use ECR caching (old Docker provider)
- Don't properly leverage base images

The optimized version (`chromadb_lambdas_v2.py`) fixes all these issues and should reduce build times by 80-95% after the first build.
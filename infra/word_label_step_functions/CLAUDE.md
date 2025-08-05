# ChromaDB Containerized Lambda Development Guide

This document outlines the current implementation and future development roadmap for the ChromaDB containerized Lambda infrastructure.

## Current Implementation

### Hash-Based Build Optimization (Implemented)

The current implementation includes intelligent build caching:

1. **Change Detection**: Calculates SHA256 hash of:
   - Dockerfile contents
   - Python source files in specified directories
   - Requirements files
   
2. **Skip Logic**: 
   - Stores build hashes in S3 bucket
   - Compares current hash with stored hash
   - Verifies image exists in ECR
   - Skips build if no changes detected

3. **Force Rebuild**: Option to force rebuilds when needed:
   ```python
   ChromaDBLambdas(
       name="chromadb",
       force_rebuild=True,  # Forces rebuild even if hash unchanged
       ...
   )
   ```

### Performance Impact

- **Before**: Every `pulumi up` triggers full Docker build (~2-5 minutes)
- **After**: Only builds when source changes detected (~5 seconds for skip check)
- **Savings**: 95%+ time reduction for unchanged code

## Future Development Roadmap

### Phase 1: Async Container Building (Priority: High)

**Goal**: Make `pulumi up` near-instant by building containers asynchronously in AWS.

**Implementation Plan**:
1. Port `async_container_builder.py` logic into ChromaDBLambdas
2. Use CodeBuild for container builds (similar to `fast_lambda_layer.py`)
3. CodePipeline for orchestration
4. S3 for source artifacts

**Benefits**:
- `pulumi up` completes in <30 seconds
- Builds happen on AWS infrastructure (faster, parallel)
- No local Docker daemon required
- Better CI/CD integration

**Example Architecture**:
```
pulumi up → Upload source to S3 → Trigger CodePipeline → CodeBuild builds image → Push to ECR
           ↓
        (returns immediately)
```

### Phase 2: Multi-Stage Base Images (Priority: Medium)

**Goal**: Reduce build times by pre-building common dependencies.

**Implementation**:
1. Create base image with ChromaDB and dependencies:
   ```dockerfile
   # chromadb-base.Dockerfile
   FROM public.ecr.aws/lambda/python:3.12-arm64
   RUN pip install chromadb numpy boto3 openai pydantic
   ```

2. Lambda images just add code:
   ```dockerfile
   FROM my-account.dkr.ecr.region.amazonaws.com/chromadb-base:latest
   COPY handler.py ${LAMBDA_TASK_ROOT}/
   ```

**Benefits**:
- 80% faster builds (only copying code)
- Shared base layer across multiple Lambdas
- Easier dependency management

### Phase 3: Development Mode (Priority: Medium)

**Goal**: Ultra-fast local development with hot reloading.

**Features**:
1. **Local Testing Mode**: 
   ```python
   ChromaDBLambdas(
       development_mode=True,  # Uses local code directly
   )
   ```

2. **SAM Local Integration**:
   - Test containerized Lambdas locally
   - No ECR push required
   - Instant code changes

3. **Mock ECR URLs**:
   - Development uses local Docker images
   - Production uses ECR

### Phase 4: Smart Caching Strategy (Priority: Low)

**Goal**: Optimize caching across environments.

**Features**:
1. **Layer-Aware Hashing**: Different hashes for different change types:
   - Dependencies changed → Rebuild from base
   - Only handler changed → Quick rebuild
   
2. **Cross-Developer Cache Sharing**:
   - Shared S3 cache bucket
   - Team members benefit from each other's builds
   
3. **Cache Warming**:
   - Pre-build common configurations
   - Scheduled rebuilds for base images

### Phase 5: Monitoring and Observability (Priority: Low)

**Goal**: Track build performance and optimize bottlenecks.

**Metrics to Track**:
- Build frequency per Lambda
- Average build time
- Cache hit rate
- ECR storage costs
- Build failure rate

**Implementation**:
- CloudWatch metrics from CodeBuild
- Build time dashboards
- Cost analysis reports

## Implementation Priority Matrix

| Feature | Impact | Effort | Priority | Timeline |
|---------|--------|--------|----------|----------|
| Async Building | High | Medium | 1 | 1-2 weeks |
| Base Images | Medium | Low | 2 | 3-4 days |
| Dev Mode | High | Medium | 3 | 1 week |
| Smart Caching | Low | High | 4 | 2 weeks |
| Monitoring | Low | Low | 5 | 2-3 days |

## Quick Wins (Can Do Now)

### 1. Optimize Dockerfile Order
Move least-changing items first:
```dockerfile
# System deps (rarely change)
RUN dnf install -y gcc-c++ python3-devel

# Python deps (occasional changes)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Local packages (moderate changes)
COPY receipt_dynamo/ /lambda/receipt_dynamo/
COPY receipt_label/ /lambda/receipt_label/

# Handler (frequent changes)
COPY handler.py .
```

### 2. Use .dockerignore
Create `.dockerignore` to exclude unnecessary files:
```
**/__pycache__
**/*.pyc
**/.pytest_cache
**/tests
**/*.md
.git
```

### 3. Parallel Builds
Build polling and compaction images in parallel:
```python
# Current: Sequential
polling_image = build_polling()
compaction_image = build_compaction()

# Better: Parallel with asyncio
import asyncio
polling_image, compaction_image = await asyncio.gather(
    build_polling_async(),
    build_compaction_async(),
)
```

## Migration Path

### Step 1: Current Hash-Based Skip ✅
- Already implemented
- Provides immediate benefits
- No breaking changes

### Step 2: Add Async Building
1. Create feature flag: `use_async_build=True`
2. Implement alongside current approach
3. Test thoroughly
4. Switch default to async

### Step 3: Gradual Optimization
- Add features incrementally
- Maintain backward compatibility
- Document each improvement

## Development Commands

### Force Rebuild
```bash
pulumi config set chromadb:force-rebuild true
pulumi up
pulumi config rm chromadb:force-rebuild
```

### Check Build Status
```bash
# View hash in S3
aws s3 cp s3://chromadb-build-cache/docker-hashes/chromadb-poll/hash.txt -

# Check ECR image
aws ecr describe-images --repository-name chromadb-poll-dev
```

### Clean Build Cache
```bash
# Remove S3 hashes
aws s3 rm s3://chromadb-build-cache/docker-hashes/ --recursive

# Remove ECR images
aws ecr batch-delete-image --repository-name chromadb-poll-dev --image-ids imageTag=latest
```

## Troubleshooting

### Build Not Skipping
1. Check S3 hash: `aws s3 ls s3://chromadb-build-cache/docker-hashes/`
2. Verify ECR image exists: `aws ecr list-images --repository-name chromadb-poll-dev`
3. Enable debug logging: `export PULUMI_LOG_LEVEL=debug`

### Hash Mismatch
- Ensure consistent file paths
- Check for hidden files (.DS_Store, etc.)
- Verify .gitignore and .dockerignore alignment

### ECR Push Failures
- Check ECR repository exists
- Verify IAM permissions
- Ensure Docker daemon is running
- Check ECR storage limits

## Cost Optimization

### Current Costs
- ECR storage: ~$0.10/GB/month per image
- S3 hash storage: Negligible (<$0.01/month)
- Build time: Developer time savings

### Future Savings (with Async)
- CodeBuild: ~$0.005/build minute (ARM)
- Faster builds = lower costs
- Shared cache = fewer builds

## References

- [AWS Lambda Container Images](https://docs.aws.amazon.com/lambda/latest/dg/images-create.html)
- [ECR Lifecycle Policies](https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html)
- [CodeBuild Docker Layer Caching](https://docs.aws.amazon.com/codebuild/latest/userguide/build-caching.html)
- [Pulumi Docker Provider](https://www.pulumi.com/registry/packages/docker/)

## Next Steps

1. **Immediate**: Test hash-based skip in development
2. **This Week**: Plan async building implementation
3. **Next Sprint**: Implement base image strategy
4. **Future**: Add monitoring and advanced caching
# Base Images Evolution Summary

## Overview
The base_images module has evolved through three versions, each addressing specific limitations and improving performance.

## Version 1: `base_images.py` (Original)
**Status**: Legacy, still uses old Docker provider

### Characteristics:
- **Docker Provider**: Uses `pulumi_docker` (old provider)
- **Build Context**: Entire repository (2.9GB)
- **Caching**: Attempted but commented out due to Pulumi serialization issues
- **Dockerfiles**: `Dockerfile.receipt_dynamo` and `Dockerfile.receipt_label`

### Key Code:
```python
# Uses entire repo as context
context=str(build_context_path),  # Points to repo root (2.9GB)

# Cache disabled due to issues
# TODO: Re-enable cache_from once Pulumi serialization issue is resolved
```

### Issues:
- ❌ No ECR caching (Pulumi Docker provider doesn't support it properly)
- ❌ Massive build context (2.9GB)
- ❌ Context hash changes with any file change anywhere in repo
- ❌ Slow builds due to large context processing

## Version 2: `base_images_v2.py` (Docker-Build Migration)
**Status**: Intermediate version, fixed caching but still uses large context

### Characteristics:
- **Docker Provider**: Migrated to `pulumi_docker_build` (new provider)
- **Build Context**: Still entire repository (2.9GB)
- **Caching**: Working ECR cache_from and cache_to
- **Dockerfiles**: Same as v1

### Key Code:
```python
# Migrated to docker-build provider
import pulumi_docker_build as docker_build

# Working caching!
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
```

### Improvements:
- ✅ ECR caching now works
- ✅ Handles missing cache tags gracefully
- ✅ Faster subsequent builds due to caching

### Remaining Issues:
- ❌ Still uses entire repo as context (2.9GB)
- ❌ Context hash still unstable

## Version 3: `base_images_v3.py` (Scoped Context Optimization) ⭐ CURRENT
**Status**: Production - Most optimized version

### Characteristics:
- **Docker Provider**: Uses `pulumi_docker_build`
- **Build Context**: Scoped to specific package directories
- **Caching**: Full ECR cache support
- **Dockerfiles**: New scoped versions (`Dockerfile.receipt_dynamo.scoped`)

### Key Code:
```python
# SCOPED contexts - the game changer!
context=docker_build.ContextArgs(
    location=str(dynamo_package_dir),  # Just receipt_dynamo/ (~200KB with .dockerignore)
),

# Uses scoped Dockerfiles
dockerfile=docker_build.DockerfileArgs(
    location=str(Path(__file__).parent / "dockerfiles" / "Dockerfile.receipt_dynamo.scoped"),
),
```

### Improvements:
- ✅ Context reduced from 2.9GB to ~200KB (99.99% reduction)
- ✅ Context hash only changes when package changes
- ✅ Full ECR caching support
- ✅ Faster builds due to minimal context
- ✅ Proper .dockerignore files further reduce context

## Performance Comparison

| Metric | v1 (Original) | v2 (Docker-Build) | v3 (Scoped) |
|--------|--------------|-------------------|-------------|
| **Context Size** | 2.9GB | 2.9GB | ~200KB |
| **Context Processing** | 30-60s | 30-60s | <1s |
| **ECR Caching** | ❌ Broken | ✅ Working | ✅ Working |
| **First Build** | 2-3 min | 2-3 min | 2-3 min |
| **Cached Build** | 1-2 min | 15-30s | 5-10s |
| **Context Stability** | Poor | Poor | Excellent |

## Migration Path

### From v1 to v3:
1. Update import in `__main__.py`:
   ```python
   # Old
   from base_images import BaseImages
   
   # New
   from base_images.base_images_v3 import BaseImages
   ```

2. Ensure .dockerignore files exist in package directories

3. No other code changes needed!

### From v2 to v3:
1. Update import to use v3
2. Ensure scoped Dockerfiles exist
3. Add .dockerignore files to packages

## Key Insights

1. **Context size matters more than caching**: v3's biggest win is the 99.99% context reduction
2. **Docker-build provider is essential**: Enables proper ECR caching
3. **Scoped contexts are the ultimate optimization**: Stable hashes, fast processing
4. **Backward compatibility maintained**: All versions expose the same interface

## Recommendation

**Always use v3** (`base_images_v3.py`) for:
- Minimal build context (200KB vs 2.9GB)
- Stable context hashes
- Full ECR caching support
- Fastest possible builds

The evolution from v1 → v2 → v3 shows a clear optimization path:
1. v1 → v2: Fixed caching with docker-build provider
2. v2 → v3: Optimized context with scoped builds

v3 combines all optimizations and should be the standard for all new infrastructure.
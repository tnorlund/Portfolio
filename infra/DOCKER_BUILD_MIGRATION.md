# Migration to docker-build Provider

## Why Migrate?

The `docker-build` provider solves the caching issues we've been having:
- ✅ Proper `cache_from` and `cache_to` support
- ✅ No serialization errors with Pulumi Outputs
- ✅ Better ECR integration
- ✅ Up to 50x faster builds with proper caching

## Installation

```bash
# Install the docker-build provider
pip install pulumi-docker-build

# Or add to requirements.txt
pulumi-docker-build>=0.0.12
```

## Key Differences

### Old (docker provider)
```python
from pulumi_docker import Image, DockerBuildArgs, CacheFromArgs

Image(
    "my-image",
    build=DockerBuildArgs(
        context=".",
        cache_from=[CacheFromArgs(images=[...])],  # This causes errors!
    ),
    image_name=repo_url,
)
```

### New (docker-build provider)
```python
import pulumi_docker_build as docker_build

docker_build.Image(
    "my-image",
    context={"location": "."},
    cache_from=[{
        "registry": {"ref": f"{repo_url}:cache"}
    }],
    cache_to=[{
        "registry": {
            "image_manifest": True,
            "oci_media_types": True,
            "ref": f"{repo_url}:cache"
        }
    }],
    tags=[f"{repo_url}:latest"],
    push=True,
    registries=[{
        "address": repo_url,
        "password": auth_token.password,
        "username": auth_token.user_name,
    }],
)
```

## Migration Steps

### 1. Update base_images.py

Replace the current `base_images.py` with `base_images_v2.py`:

```bash
# Backup current version
cp infra/base_images/base_images.py infra/base_images/base_images_old.py

# Use new version
cp infra/base_images/base_images_v2.py infra/base_images/base_images.py
```

### 2. Update Dependencies

The new provider outputs `ref` instead of `image_name`. Update any code that uses base images:

```python
# Old
base_image_name = base_images.label_base_image.image_name

# New  
base_image_name = base_images.label_base_image.ref
```

### 3. Update Lambda Dockerfiles

Lambda functions that reference base images need to update:

```python
# In chromadb_lambdas.py and similar files
# Change from image_name to ref
build_args["BASE_IMAGE"] = base_image_ref  # was base_image_name
```

## Benefits After Migration

1. **Cache Actually Works**: The `:cache` tag stores build cache in the registry
2. **No Type Errors**: docker-build handles Pulumi Outputs correctly
3. **Faster Builds**: Proper cache layers mean 30-second rebuilds
4. **Simpler Code**: Less workarounds needed

## Testing the Migration

```bash
# 1. Install docker-build provider
pip install pulumi-docker-build

# 2. Test with a preview
pulumi preview

# 3. Deploy
pulumi up

# 4. Verify caching on second run
pulumi up  # Should be much faster!
```

## Rollback Plan

If issues occur:
```bash
# Restore old base_images.py
cp infra/base_images/base_images_old.py infra/base_images/base_images.py

# Uninstall docker-build provider
pip uninstall pulumi-docker-build

# Run with old setup
pulumi up
```

## Notes

- The docker-build provider requires Docker 20.10+ with BuildKit
- ECR authentication works the same way
- The `:cache` tag is separate from `:latest` and stores only cache manifests
- You can use both providers in the same project during migration
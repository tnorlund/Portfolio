# First Build Instructions

## Understanding the Cache System

The docker-build provider uses a `:cache` tag in ECR to store build cache manifests. This significantly speeds up subsequent builds by reusing layers.

## How It Works

1. **First Build**: When the `:cache` tag doesn't exist in the registry, the docker-build provider gracefully ignores the missing cache and performs a full build. The build will:
   - Build all layers from scratch
   - Push the resulting image with your specified tags (e.g., `:stable`, `:latest`)
   - Create and push the `:cache` tag with build cache metadata

2. **Subsequent Builds**: The provider will find the `:cache` tag and use it to:
   - Pull only changed layers
   - Reuse unchanged layers from cache
   - Result in 30-50x faster builds

## No Manual Intervention Required

The docker-build provider is designed to handle missing cache tags automatically. You don't need to:
- Check if cache exists before building
- Use `skip-cache-from` configuration
- Manually create cache tags
- Handle first-build scenarios differently

## The Error You Saw

The error "not found" for the `:stable` tag was actually looking for the wrong tag. The issue was that `skip-cache-from` was set to `true`, which prevented the cache_from configuration entirely. The fix removes the conditional logic - the provider handles missing cache gracefully on its own.

## Best Practices

1. **Always include cache_from**: Let the provider handle missing cache
2. **Always include cache_to**: Ensure cache is created for next build
3. **Don't overthink it**: The provider is smart enough to handle edge cases

## Verification Steps

```bash
# First build (no cache exists)
pulumi up
# Will see: "importing cache manifest from 681647709217.dkr.ecr.us-east-1.amazonaws.com/base-receipt-dynamo-dev:cache"
# Followed by: "ERROR: cache import: not found" (this is OK!)
# Build proceeds normally and creates cache

# Second build (cache exists)
pulumi up
# Will see: "importing cache manifest from 681647709217.dkr.ecr.us-east-1.amazonaws.com/base-receipt-dynamo-dev:cache"
# Build uses cache and completes in seconds
```

## Common Scenarios

### Scenario 1: Brand New Stack
- Cache doesn't exist
- Provider handles gracefully
- Cache created automatically

### Scenario 2: Cleared ECR Repository
- Cache was deleted
- Provider handles gracefully
- Cache recreated automatically

### Scenario 3: Normal Operations
- Cache exists
- Provider uses cache
- Builds are fast

## Troubleshooting

If you see persistent cache errors:

1. **Check ECR permissions**: Ensure the IAM role has permission to push/pull all tags
2. **Check Docker version**: Requires Docker 20.10+ with BuildKit enabled
3. **Check provider version**: Ensure `pulumi-docker-build>=0.0.12`

## Summary

The docker-build provider's cache handling is production-ready and doesn't require manual intervention. The removal of the `skip-cache-from` conditional logic ensures the provider can always attempt to use cache, falling back gracefully when it doesn't exist.
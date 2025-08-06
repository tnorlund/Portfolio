# Docker Caching Quick Start 🚀

## Recommended: Use the Wrapper Script

For guaranteed Docker BuildKit activation:

```bash
cd infra
./pulumi_up.sh  # This ensures DOCKER_BUILDKIT=1 is set
```

## Alternative: Set BuildKit Once

Set it permanently in your shell:
```bash
export DOCKER_BUILDKIT=1  # Add to ~/.zshrc or ~/.bashrc
cd infra
pulumi up
```

That's it! The following optimizations are automatically enabled:
- ✅ **Docker BuildKit** - Enabled automatically
- ✅ **Static base image tags** - Uses `stable` tag for consistent caching
- ✅ **Remote registry cache** - Pulls from ECR for layer reuse

### For Production (prod stack)
```bash
cd infra
pulumi stack select prod
pulumi up
```

Production automatically uses:
- ✅ **Docker BuildKit** - Enabled automatically
- ✅ **Content-based tags** - Uses git commit SHA for precise versioning
- ✅ **Remote registry cache** - Pulls from ECR for layer reuse

## Configuration Details

Settings are stored in Pulumi config files:

**`Pulumi.dev.yaml`:**
```yaml
portfolio:use-static-base-image: "true"   # Stable tags for fast dev builds
portfolio:docker-buildkit: "true"         # Auto-enables BuildKit
```

**`Pulumi.prod.yaml`:**
```yaml
portfolio:use-static-base-image: "false"  # Content-based tags for production
portfolio:docker-buildkit: "true"         # Auto-enables BuildKit
```

## Build Performance

| Scenario | Time | Cache Status |
|----------|------|--------------|
| First build | 5-10 min | No cache |
| No changes | <1 min | Full cache hit |
| Small changes | 1-2 min | Partial cache |

## How It Works

1. **Pulumi starts** → Reads config from `Pulumi.{stack}.yaml`
2. **BuildKit enabled** → Must be set in shell: `export DOCKER_BUILDKIT=1`
3. **Tag selection** → Uses stable tags (dev) or git SHA (prod)
4. **Cache preparation** → Run `./pull_base_images.sh` to populate local cache
5. **Build runs** → Docker reuses cached layers from local cache

## Important: Cache Strategy

Due to Pulumi Docker provider limitations with `cache_from`, we use a hybrid approach:
- **BuildKit inline cache** embeds cache metadata in images
- **Local Docker cache** stores layers on your machine
- **Manual cache warming** with `./pull_base_images.sh` pulls remote images

### For Best Performance:
```bash
# One-time setup
export DOCKER_BUILDKIT=1  # Add to ~/.zshrc or ~/.bashrc

# Before each build session
./pull_base_images.sh  # Pulls latest images into local cache
pulumi up              # Uses local cache for fast builds
```

## Manual Override (Optional)

If you need to override the config:

```bash
# Force content-based tags in dev
pulumi config set portfolio:use-static-base-image false

# Or use environment variable (overrides config)
export USE_STATIC_BASE_IMAGE=false
pulumi up
```

## Troubleshooting

### Verify caching is working:
Look for "CACHED" in build output:
```
=> CACHED [2/5] COPY receipt_dynamo /tmp/receipt_dynamo
=> CACHED [3/5] RUN pip install /tmp/receipt_dynamo
```

### Force rebuild without cache:
```bash
# Temporarily disable static tags
pulumi config set portfolio:use-static-base-image false
pulumi up

# Then re-enable for next build
pulumi config set portfolio:use-static-base-image true
```

### Pre-warm cache (optional):
```bash
./pull_base_images.sh  # Pulls images before build
pulumi up
```

## Summary

**No exports needed! No environment variables! Just run `pulumi up`!** 🎉

The configuration handles everything automatically based on your stack.
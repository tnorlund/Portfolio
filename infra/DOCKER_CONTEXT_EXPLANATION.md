# Understanding Docker Context Hash

## The Problem

When you run a Docker build, Docker calculates a hash of the entire build context to determine if anything changed. The context hash includes:

1. **Every file in the context directory** (even if ignored by .dockerignore)
2. **File metadata** (timestamps, permissions)
3. **Directory structure**

## Current Situation

```python
# In base_images_v2.py
build_context_path = Path(__file__).parent.parent.parent  # Points to repo root
```

This means:
- Context = `/Users/tnorlund/GitHub/example/` (entire repo!)
- Any file change ANYWHERE in the repo changes the context hash
- Even if you edit `README.md` or `infra/some_other_file.py`, Docker thinks the context changed

## Why This Matters (Or Doesn't)

### With Dynamic Tags (Production)
```
use-static-base-image: false
```
- Tag = `git-abc123` (based on git commit)
- Context hash changes → New build triggered
- Problem: Unnecessary rebuilds for unrelated changes

### With Static Tags (Development) ✅
```
use-static-base-image: true  # Your current setting
```
- Tag = `stable` (always the same)
- Context hash changes → Docker still uses cached layers!
- **No problem**: The stable tag means Docker can reuse existing layers

## The Real Issue

The context hash changing isn't actually causing rebuilds in your case because:

1. You're using `stable` tags
2. Docker's layer caching works independently of context hash
3. The Dockerfile hasn't changed, so layers are reused

But it's still inefficient because:
- Docker has to scan the entire repo directory tree
- Large context = slower build start (uploading context to daemon)
- Unnecessary I/O operations

## Solutions

### Option 1: Narrower Context (Best)
Create separate Dockerfiles that can use package directories as context:

```dockerfile
# infra/base_images/dockerfiles/receipt_dynamo/Dockerfile
FROM public.ecr.aws/lambda/python:3.12
COPY . /tmp/receipt_dynamo
RUN pip install /tmp/receipt_dynamo
```

Then use:
```python
context=docker_build.ContextArgs(
    location=str(dynamo_package_dir),  # Just the package!
)
```

### Option 2: Better .dockerignore (Current)
The current `.dockerignore` helps but Docker still scans everything.

### Option 3: Build Cache Mount (Advanced)
Use BuildKit cache mounts to cache pip downloads:
```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /tmp/receipt_dynamo
```

## Why You're Not Seeing The Problem

Your builds are probably still fast because:
1. `use-static-base-image: true` means same tag
2. Docker layer caching works even with context changes
3. BuildKit optimizations help

But you would see issues if:
- You switched to dynamic tags
- You cleared Docker's build cache
- Multiple developers built with different local changes

## Recommendation

Since you're using static tags for development, the context hash changes don't matter much. But for production builds with dynamic tags, you should:

1. Use package-specific contexts
2. Or create a CI/CD build process that creates clean contexts
3. Or accept that base images rebuild when anything changes (might be desirable for production)

## Quick Test

To see the context size issue:
```bash
# See how much data Docker has to process
cd /Users/tnorlund/GitHub/example
du -sh .                    # Total repo size
du -sh receipt_dynamo       # Just what you need
du -sh receipt_label        # Just what you need

# The difference is wasted I/O
```

## Summary

- **Context hash** = fingerprint of all files in build context
- **Your context** = entire repo (inefficient but working)
- **Static tags** = shields you from rebuild issues
- **Real problem** = unnecessary I/O and slower build starts
- **Solution** = narrow the context to just the package directories
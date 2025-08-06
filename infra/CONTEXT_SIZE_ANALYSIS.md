# Docker Context Size Analysis

## The Problem: 131MB for a Python Package?!

When we scoped the build context to just `receipt_dynamo/`, it was still 131MB. That seemed excessive for a Python package.

## What Was Taking Space

| Component | Size | Should Include? |
|-----------|------|-----------------|
| `.mypy_cache/` | 58MB | ❌ NO! Cache files |
| `tests/` | 36MB | ❌ NO! Tests not needed in production |
| `receipt_dynamo/` (actual code) | 35MB | ✅ YES, but... |
| `__pycache__/` | ~17MB | ❌ NO! Python bytecode cache |
| Migration scripts | ~100KB | ❌ NO! One-time scripts |
| `.pytest_cache/` | 640KB | ❌ NO! Test cache |
| `pylint_*.json` | 724KB | ❌ NO! Linting reports |

Within the 35MB "actual code":
- 30MB was another `.mypy_cache/` nested inside!
- Several MB of `__pycache__/`
- The actual Python source is tiny

## The Solution: Proper .dockerignore

Created package-specific `.dockerignore` files that exclude:
- All test files and directories
- All cache directories (mypy, pytest, pycache)
- Development/migration scripts
- IDE files, logs, data files
- Build artifacts

## Final Results

| Package | Without .dockerignore | With .dockerignore | Reduction |
|---------|----------------------|-------------------|-----------|
| receipt_dynamo | 131MB | **0.21MB** | **99.8%** |
| receipt_label | 5.2MB | **0.32MB** | **93.8%** |

## Combined Optimization Impact

Starting from the entire repo context:
1. **Original (v2)**: 2.9GB context for both packages
2. **Scoped (v3)**: 131MB + 5.2MB = 136.2MB total
3. **Scoped + .dockerignore**: 0.21MB + 0.32MB = **0.53MB total**

**Total reduction: 2.9GB → 0.53MB = 99.98% smaller!**

## Why This Matters

1. **Docker build context upload**: Instead of uploading 2.9GB, Docker uploads 0.53MB
2. **Context hash stability**: Hash is computed from 0.53MB of actual code, not 2.9GB of noise
3. **Build speed**: Faster context processing, faster builds
4. **Network usage**: Crucial for remote Docker daemons or CI/CD
5. **Developer experience**: Changes to tests or cache don't trigger rebuilds

## Key Insight

The actual source code is TINY - most of the space was:
- **58MB of mypy cache** (type checking cache)
- **36MB of tests** (including JSON fixtures)
- **17MB of Python bytecode** (__pycache__)

These are all development artifacts that have no business being in a production Docker image!

## Best Practice

Always create a `.dockerignore` in package directories to exclude:
- Cache directories (all of them!)
- Test files and fixtures
- Development scripts
- Build artifacts
- IDE and OS files

The actual code you're shipping is probably less than 1MB!
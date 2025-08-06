# Docker Build Context Optimization Summary

## What We Fixed

### Before (v2)
- **Build context**: Entire repository (2.9GB)
- **receipt_dynamo build**: Scans 2.9GB to build from 131MB package
- **receipt_label build**: Scans 2.9GB to build from 5.2MB package
- **Impact**: Any file change anywhere triggers context hash change

### After (v3) 
- **receipt_dynamo context**: Just `receipt_dynamo/` directory (131MB)
- **receipt_label context**: Just `receipt_label/` directory (5.2MB)
- **Impact**: Only changes to the specific package affect its context hash

## Size Comparison

| Image | Before Context | After Context | Reduction |
|-------|---------------|---------------|-----------|
| receipt_dynamo | 2.9GB | 131MB | **95.5%** |
| receipt_label | 2.9GB | 5.2MB | **99.8%** |

## Key Insight

`receipt_label` doesn't need `receipt_dynamo` in its build context because:
1. It uses `receipt_dynamo` as its `BASE_IMAGE`
2. It inherits everything from the base image
3. It only needs to add the `receipt_label` package on top

## Implementation Details

### 1. Scoped Dockerfiles
Created new Dockerfiles that expect package directories as context:
- `Dockerfile.receipt_dynamo.scoped`: Expects `receipt_dynamo/` as context
- `Dockerfile.receipt_label.scoped`: Expects `receipt_label/` as context

### 2. Updated Build Contexts
```python
# receipt_dynamo - uses package directory
context=docker_build.ContextArgs(
    location=str(dynamo_package_dir),  # Just 131MB!
)

# receipt_label - also uses its own package directory
context=docker_build.ContextArgs(
    location=str(label_package_dir),  # Just 5.2MB!
)
```

## Benefits

### Immediate Benefits
1. **Faster build starts**: Docker only scans relevant files
2. **Stable context hash**: Only package changes affect the hash
3. **Less I/O**: 95-99% reduction in files scanned
4. **Cleaner builds**: Context only contains what's needed

### Long-term Benefits
1. **Predictable rebuilds**: Know exactly what triggers a rebuild
2. **Better caching**: Cache invalidation only when package changes
3. **Faster CI/CD**: Smaller contexts upload faster to build servers
4. **Developer experience**: Less "why is it rebuilding?" confusion

## Performance Impact

With `use-static-base-image: true` (development):
- **Before**: Slow context processing but cached layers still used
- **After**: Fast context processing AND cached layers used

With dynamic tags (production):
- **Before**: Any repo change = rebuild (because context hash changed)
- **After**: Only package changes = rebuild (proper invalidation)

## Testing

To verify the optimization works:
```bash
# Check context sizes
du -sh /Users/tnorlund/GitHub/example/               # 2.9GB
du -sh /Users/tnorlund/GitHub/example/receipt_dynamo  # 131MB
du -sh /Users/tnorlund/GitHub/example/receipt_label   # 5.2MB

# Build and watch the context upload size
pulumi up

# Make a change to infra/ (unrelated file)
echo "# test" >> infra/some_file.py

# Build again - context hash should NOT change for base images
pulumi up
```

## Summary

By scoping the build context to just the package directories, we:
- Reduced context size by **95-99%**
- Made context hash stable (only changes when package changes)
- Improved build performance and predictability
- Maintained all functionality (receipt_label still gets receipt_dynamo from base image)
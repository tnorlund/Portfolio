# Docker Build Dependency Fix

## Problem
Lambda containers were trying to build before base images finished building, resulting in errors like:
```
error reading build output: 681647709217.dkr.ecr.us-east-1.amazonaws.com/base-receipt-label-dev:stable: not found
```

## Root Cause
1. Base images are built with the new `docker-build` provider
2. Lambda images are built with the old `docker` provider  
3. The dependency chain wasn't properly established between them
4. When passing `base_image.ref` (an Output[str]), the dependency wasn't tracked

## Solution
Pass both the image reference AND the resource to establish proper dependencies:

### 1. Updated Component Signatures
```python
# Before
class LineEmbeddingStepFunction(ComponentResource):
    def __init__(self, name: str, base_image_name: Output[str] = None, ...)

# After  
class LineEmbeddingStepFunction(ComponentResource):
    def __init__(self, name: str, base_image_name: Output[str] = None, 
                 base_image_resource: Resource = None, ...)
```

### 2. Added Dependency Chain
```python
# In infra.py and submit_step_function.py
lambda_opts = ResourceOptions(parent=self)
if base_image_resource:
    lambda_opts = ResourceOptions(parent=self, depends_on=[base_image_resource])
    
self.chromadb_lambdas = ChromaDBLambdas(
    ...,
    opts=lambda_opts,  # Now waits for base image
)
```

### 3. Updated Main Instantiation
```python
# In __main__.py
line_embedding_step_functions = LineEmbeddingStepFunction(
    "step-func", 
    base_image_name=base_images.label_base_image.ref,  # The URL
    base_image_resource=base_images.label_base_image   # The resource (for dependency)
)
```

## Why This Works
- Pulumi tracks dependencies through Resource objects
- When you pass an Output[str], it only knows about the string value, not the resource that produces it
- By explicitly passing the resource and adding it to `depends_on`, we ensure proper ordering

## Build Order (After Fix)
1. ✅ Base images build first (docker-build provider)
2. ✅ Base images push to ECR with `:stable` tag
3. ✅ Lambda containers wait for base images
4. ✅ Lambda containers build using base image `:stable` tag
5. ✅ Lambda containers push to ECR

## Alternative Solutions (Not Used)
1. **Migrate everything to docker-build**: Would work but requires more changes
2. **Use Pulumi's implicit dependencies**: Doesn't work across provider boundaries
3. **Add retry logic**: Would mask the issue rather than fix it

## Testing
```bash
# Clean build to verify dependency order
pulumi destroy -y
pulumi up

# Should see base images building first:
# base-images → base-receipt-dynamo-img-dev → creating
# base-images → base-receipt-label-img-dev → creating
# Then Lambda images:
# chromadb-lambdas → chromadb-poll-img-dev → creating
```

## Files Modified
- `/infra/__main__.py` - Pass base_image_resource to components
- `/infra/embedding_step_functions/infra.py` - Accept and use base_image_resource
- `/infra/word_label_step_functions/submit_step_function.py` - Accept and use base_image_resource
- `/infra/embedding_step_functions/chromadb_lambdas.py` - Simplified (dependency handled upstream)

## Benefits
- ✅ No more "image not found" errors
- ✅ Proper dependency ordering
- ✅ Faster builds with caching
- ✅ Clear separation between providers
---
name: ChromaDB Lambda Layer Integration
about: Track the proper integration of ChromaDB into Lambda layers
title: 'Add ChromaDB support to Lambda layers with proper dependency management'
labels: enhancement, infrastructure, lambda
assignees: ''
---

## Background

We recently removed `chromadb-client` from the receipt-label Lambda layer to fix a PIL/_imaging import error. The issue was that chromadb-client has transitive dependencies on image processing libraries (likely through ONNX runtime) that weren't properly configured for the Lambda environment.

## Problem

While the current embedding Lambda functions don't use ChromaDB, future Lambda functions will need it for:
- Vector similarity search during receipt processing
- Real-time label validation using vector embeddings
- Merchant pattern matching from ChromaDB collections
- Compaction operations for the S3-backed ChromaDB storage

## Current State

- ChromaDB code exists in `receipt_label/utils/chroma_client.py`
- The `lambda` extras in `receipt_label/pyproject.toml` is currently empty
- Pillow/PIL dependencies were causing import errors in Lambda environment
- The issue was specifically with Linux ARM64 compiled `.so` files not being properly structured in the layer

## Requirements

1. **Separate Layer Strategy**: Create a dedicated `chromadb-layer` for functions that need vector search
   - This prevents bloating layers for functions that don't need it
   - Allows independent versioning and updates

2. **Proper Dependency Resolution**: 
   - Identify minimal dependencies for chromadb-client
   - Consider using `chromadb-client` without ONNX if possible
   - Or properly configure ONNX/PIL dependencies for Lambda ARM64

3. **Layer Structure Fix**: Ensure proper flattening
   - Packages must be at `/opt/python/` not `/opt/python/lib/python3.12/site-packages/`
   - The recent fix to remove nested `lib` directory after flattening should help

## Proposed Solution

### Option 1: Minimal ChromaDB Client
```python
# In pyproject.toml
[project.optional-dependencies]
chromadb = [
    "chromadb-client>=0.5.0",
    # Explicitly exclude or mock image processing deps
]
```

### Option 2: Separate ChromaDB Layer
```python
# In fast_lambda_layer.py
{
    "package_dir": "chromadb_layer",
    "name": "chromadb-vector",  
    "description": "ChromaDB client for vector operations",
    "python_versions": ["3.12"],
    "needs_pillow": True,  # If ONNX requires it
    "custom_packages": ["chromadb-client==0.5.0"],
}
```

### Option 3: Conditional Dependencies
```python
# Use environment detection to conditionally import
if os.environ.get("LAMBDA_TASK_ROOT"):
    # Lambda environment - use lightweight client
    from chromadb_client_lite import Client
else:
    # Full environment - use full client
    import chromadb
```

## Testing Requirements

1. **Local Testing with Docker**:
   ```bash
   python3 test_lambda_layer.py --layer-name chromadb-vector --docker
   ```

2. **Import Validation**:
   - Verify ChromaDB client can be imported
   - Verify vector operations work
   - Ensure no PIL import errors

3. **Performance Testing**:
   - Measure cold start impact
   - Check layer size limits (250MB unzipped)

## Implementation Steps

1. [ ] Investigate minimal chromadb-client dependencies
2. [ ] Create test Lambda function that uses ChromaDB
3. [ ] Configure proper layer build settings
4. [ ] Test with Docker Lambda runtime (ARM64)
5. [ ] Deploy and validate in actual Lambda environment
6. [ ] Document the solution in CLAUDE.md

## Success Criteria

- [ ] ChromaDB can be imported in Lambda without errors
- [ ] Vector search operations work correctly
- [ ] No PIL/_imaging import errors
- [ ] Layer size remains under Lambda limits
- [ ] Cold start time remains acceptable (<3s)

## Related Files

- `/infra/fast_lambda_layer.py` - Lambda layer build configuration
- `/receipt_label/pyproject.toml` - Package dependencies
- `/receipt_label/utils/chroma_client.py` - ChromaDB client code
- `/test_lambda_layer.py` - Layer testing utility

## Notes

The core issue is that ChromaDB's dependencies (particularly ONNX runtime) expect image processing capabilities that aren't needed for vector operations. We need to either:
1. Strip out unnecessary dependencies
2. Properly configure them for Lambda
3. Use a different vector store that's more Lambda-friendly

## References

- [AWS Lambda Layers Documentation](https://docs.aws.amazon.com/lambda/latest/dg/configuration-layers.html)
- [ChromaDB Client Documentation](https://docs.trychroma.com/reference/Client)
- [ONNX Runtime in Lambda](https://github.com/microsoft/onnxruntime/issues/11096)
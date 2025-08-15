# Validation Pipeline ChromaDB Migration - TODO

## Overview

The validation pipeline infrastructure still references Pinecone and needs to be migrated to ChromaDB in a future PR. This document tracks the files and components that require migration.

## Files Requiring ChromaDB Migration

### 1. Validation Pipeline Infrastructure
- `validation_pipeline/infra.py` - Contains Pinecone environment variables
- `validation_pipeline/lambda.py` - May reference Pinecone operations
- `validation_by_merchant/infra.py` - Contains Pinecone configuration
- `validation_by_merchant/lambda.py` - Uses Pinecone for merchant validation

### 2. Merchant Validation Step Functions
- `validate_merchant_step_functions/validate_merchant_step_functions.py` - References Pinecone
- `validate_merchant_step_functions/handlers/` - May contain Pinecone operations

### 3. Word Label Step Functions (Legacy)
- `word_label_step_functions/submit_step_function.py` - Pinecone references
- `word_label_step_functions/poll_single_batch.py` - Pinecone upsert operations
- `word_label_step_functions/poll_line_embedding_batch_handler.py` - Pinecone operations

### 4. Upload Images Infrastructure
- `upload_images/infra.py` - Contains Pinecone environment configuration

## Migration Tasks

### Phase 1: Update Infrastructure Files
- [ ] Replace Pinecone environment variables with ChromaDB configuration
- [ ] Update Lambda layer dependencies (remove pinecone, add chromadb-client)
- [ ] Update IAM permissions for S3 access (ChromaDB snapshots/deltas)

### Phase 2: Update Lambda Handlers
- [ ] Replace Pinecone client initialization with ChromaDB client
- [ ] Update vector upsert operations to use ChromaDB delta pattern
- [ ] Modify query operations to use ChromaDB snapshot pattern
- [ ] Update metadata structure to match ChromaDB requirements

### Phase 3: Update Step Functions
- [ ] Modify state machine definitions for ChromaDB operations
- [ ] Update error handling for ChromaDB-specific errors
- [ ] Add compaction triggers where appropriate

### Phase 4: Testing
- [ ] Update unit tests to mock ChromaDB instead of Pinecone
- [ ] Create integration tests for ChromaDB operations
- [ ] Validate end-to-end pipeline with ChromaDB

## Technical Considerations

### ChromaDB Client Configuration
The validation pipeline should use the ChromaDB client in **read mode** for querying:
```python
from receipt_label.utils.chroma_client import ChromaDBClient

# For validation queries
client = ChromaDBClient(mode="read", persist_directory="/tmp/snapshot")
```

### S3 Integration Pattern
Follow the pattern established in `receipt_label/utils/chroma_s3_helpers.py`:
- Validation lambdas should consume snapshots
- Any new embeddings should produce deltas
- Trigger compaction after batch operations

### Environment Variables to Add
```
CHROMA_PERSIST_PATH=/tmp/chroma
CHROMA_S3_BUCKET=your-vectors-bucket
CHROMA_DATABASE_NAME=words  # or lines depending on use case
```

### Environment Variables to Remove
```
PINECONE_API_KEY
PINECONE_HOST
PINECONE_INDEX_NAME
```

## Dependencies

This migration depends on:
- PR #314 (ChromaDB base infrastructure) - **COMPLETED**
- ChromaDB compaction infrastructure - **COMPLETED**
- S3 bucket configuration for vectors - **COMPLETED**

## Estimated Effort

- **Infrastructure updates**: 2-3 days
- **Lambda handler migration**: 3-4 days
- **Testing and validation**: 2-3 days
- **Total estimate**: 1-2 weeks

## Notes

- The `embedding_step_functions/` directory already implements ChromaDB patterns and can serve as a reference
- Consider deprecating `word_label_step_functions/` entirely in favor of the unified embedding approach
- The validation pipeline is critical path - ensure thorough testing before deployment

## Related Documentation

- [ChromaDB S3 Compaction Architecture](./CHROMADB_S3_COMPACTION_ARCHITECTURE.md)
- [Embedding Step Functions README](./embedding_step_functions/README.md)
- [ChromaDB Migration Guide](../receipt_label/PINECONE_TO_CHROMA_MIGRATION.md)

---

**Status**: TO BE IMPLEMENTED
**Target PR**: Future PR after #314 is merged
**Priority**: High - validation pipeline is actively used
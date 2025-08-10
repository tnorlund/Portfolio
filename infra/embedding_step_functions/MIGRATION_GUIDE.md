# Unified Lambda Container Migration Guide

## Overview

This guide walks through migrating from 6 separate Lambda containers to a single unified container that reduces build times and improves maintainability.

## Benefits

- **85% faster builds**: Build 1 container instead of 6
- **Reduced ECR storage**: Single image with shared layers
- **Easier maintenance**: Update dependencies in one place
- **Better caching**: Docker layer cache is more effective
- **Simpler debugging**: All handlers in one codebase

## Migration Steps

### 1. Deploy the Unified Container (Zero Downtime)

First, deploy the unified container alongside existing containers:

```bash
cd infra
pulumi up -y
```

### 2. Update Step Function Definitions

Update your step functions to use the new Lambda ARNs. The handler selection is controlled by the `HANDLER_TYPE` environment variable.

### 3. Test Each Handler

Test each handler type to ensure it works correctly:

```bash
# Test word polling
aws lambda invoke \
  --function-name word-poll-dev \
  --payload '{"batch_id": "test-batch-123"}' \
  response.json

# Test line polling  
aws lambda invoke \
  --function-name line-poll-dev \
  --payload '{"batch_id": "test-line-batch"}' \
  response.json

# Test compaction
aws lambda invoke \
  --function-name compact-dev \
  --payload '{"delta_paths": [], "target_collection": "test"}' \
  response.json
```

### 4. Monitor Performance

Compare metrics between old and new implementations:

- Cold start times (should be similar)
- Execution duration (should be identical)
- Memory usage (should be identical)
- Error rates (should be zero)

### 5. Clean Up Old Resources

Once confident, remove old Lambda functions and ECR repositories:

```python
# In chromadb_lambdas.py, comment out or delete the old component
# from infra.__main__ import ChromaDBLambdas  # Remove this
from infra.embedding_step_functions.unified_chromadb_lambdas import UnifiedChromaDBLambdas
```

## Architecture Comparison

### Before (6 Containers)
```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Word Polling │  │Line Polling  │  │  Compaction  │
│   Container  │  │  Container   │  │  Container   │
└──────────────┘  └──────────────┘  └──────────────┘
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│Find Unembedd │  │Submit OpenAI │  │List Pending  │
│   Container  │  │  Container   │  │  Container   │
└──────────────┘  └──────────────┘  └──────────────┘
```

### After (1 Container)
```
┌─────────────────────────────────────┐
│       Unified Container             │
├─────────────────────────────────────┤
│  Router (HANDLER_TYPE env var)      │
├─────────────────────────────────────┤
│ - Word Polling Handler              │
│ - Line Polling Handler              │
│ - Compaction Handler                │
│ - Find Unembedded Handler           │
│ - Submit OpenAI Handler             │
│ - List Pending Handler              │
└─────────────────────────────────────┘
```

## Handler Configuration

Each Lambda function sets `HANDLER_TYPE` to route to the correct handler:

| Lambda Function | HANDLER_TYPE | Handler Class |
|----------------|--------------|---------------|
| word-poll-dev | word_polling | WordPollingHandler |
| line-poll-dev | line_polling | LinePollingHandler |
| compact-dev | compaction | CompactionHandler |
| find-unembedded-dev | find_unembedded | FindUnembeddedHandler |
| submit-openai-dev | submit_openai | SubmitOpenAIHandler |
| list-pending-dev | list_pending | ListPendingHandler |

## Rollback Plan

If issues arise, rollback is simple:

1. Update step functions to use old Lambda ARNs
2. Old containers remain in ECR until explicitly deleted
3. No data migration required

## Performance Metrics

Expected improvements:

- **Build time**: ~6 minutes → ~1 minute
- **ECR storage**: ~2.4 GB (6 × 400MB) → ~450 MB
- **Deployment time**: ~3 minutes → ~30 seconds
- **Layer cache hit rate**: ~40% → ~95%

## Troubleshooting

### Handler Not Found Error

If you see "Invalid HANDLER_TYPE", check:
1. Environment variable is set correctly
2. Handler is registered in `handlers/__init__.py`
3. Handler class is properly imported

### Import Errors

The unified container uses lazy imports in handlers to reduce cold start time. If you see import errors:
1. Check the import paths in the handler
2. Ensure dependencies are in `receipt_label` requirements

### Memory Issues

Each Lambda still has independent memory settings. Adjust in `unified_chromadb_lambdas.py`:

```python
lambda_configs = [
    {
        "name": "compact",
        "memory": 4096,  # Increase this if needed
        ...
    }
]
```

## Next Steps

After successful migration:

1. Delete old Dockerfiles and handler files
2. Update CI/CD pipelines to build only the unified container
3. Consider further optimizations:
   - Shared database connection pooling
   - Centralized metrics collection
   - Common error handling patterns